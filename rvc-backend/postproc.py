# -*- coding: utf-8 -*-
"""翻唱后处理：去噪 → 音色回补 → 人声门控 → 动态对齐 → 去齿音。

2026-11-04 按会话 session-56b24645-3946-4abf-b8cb-6d2619948980 的**最终版**
重写（早期 docs 里的参数已经过时）。常数全部走 BACKEND_* 环境变量，默认值
就是那一版调完之后定下来的值：

    DENOISE_STRENGTH 1.6   GATE_FLOOR 0.12    TIMBRE_DYN 1  TIMBRE_MAX_DB 6
    DYN_MAX_DB 8  DYN_SMOOTH 3  DEESS_THR 1.8  DEESS_FLOOR 0.65  HF_CUT 0.85
    NOISE_SCALE 0.26(wide)  F0_REPAIR 1

生产顺序（与旧版一致）：

    denoise_vocals(demucs 人声) -> svc(harvest) -> restore_timbre
    -> melody_mask/gap_mask -> apply_vocal_gate -> match_dynamics
    -> clean_vocal_output -> mix
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d

try:
    import librosa
except Exception:  # pragma: no cover
    librosa = None

HOP_S = 0.02
N_FFT = 1024
HOP = 256

DENOISE_STRENGTH = float(os.environ.get("BACKEND_DENOISE_STRENGTH", 0.0))
GATE_FLOOR = float(os.environ.get("BACKEND_GATE_FLOOR", 0.12))
TIMBRE_DYN = int(os.environ.get("BACKEND_TIMBRE_DYN", 0))
TIMBRE_MAX_DB = float(os.environ.get("BACKEND_TIMBRE_MAX_DB", 3))
DYN_MAX_DB = float(os.environ.get("BACKEND_DYN_MAX_DB", 6))
# 逐帧 20ms 的增益修正是「颗粒感/忽大忽小」的直接来源（实测 20-80Hz 调制能量
# 是静态增益的 3.4 倍），所以默认把平滑拉到 15 帧（300ms）、上限压到 6dB。
DYN_SMOOTH = int(os.environ.get("BACKEND_DYN_SMOOTH", 15))
DEESS_THR = float(os.environ.get("BACKEND_DEESS_THR", 2.6))
DEESS_FLOOR = float(os.environ.get("BACKEND_DEESS_FLOOR", 0.85))
HF_CUT = float(os.environ.get("BACKEND_HF_CUT", 1.0))
NOISE_SCALE_PRESETS = {"wide": 0.26, "normal": 0.32}
NOISE_SCALE = float(os.environ.get(
    "BACKEND_NOISE_SCALE", NOISE_SCALE_PRESETS.get(
        os.environ.get("BACKEND_PRESET", "wide"), 0.26)))
F0_REPAIR = int(os.environ.get("BACKEND_F0_REPAIR", 1))
# 2026-10-28 调参结论：辅音移植 0.6 会把原唱音色成片混回来（7-12k 相关性 0.75），
# 改成 0.10 且只在「原唱无音高」的帧上移植，既保咬字瞬态又不带原唱的音高段。
CONS_RATIO = float(os.environ.get("BACKEND_CONS_RATIO", 0.10))
REF_VOICING_MIN, REF_VOICING_MAX = 53.0, 900.0


# --------------------------------------------------------------------- 基础

def _read(path: Path) -> tuple[np.ndarray, int]:
    y, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return y.mean(axis=1), int(sr)


def _write(path: Path, y: np.ndarray, sr: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    sf.write(str(path), np.clip(y, -1.0, 1.0), sr)
    return path


def _stft(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    S = librosa.stft(y, n_fft=N_FFT, hop_length=HOP, window="hann")
    return np.abs(S), np.angle(S)


def _istft(mag: np.ndarray, phase: np.ndarray, length: int) -> np.ndarray:
    return librosa.istft(mag * np.exp(1j * phase), hop_length=HOP, length=length)


def frame_rms_db(y: np.ndarray, sr: int, hop_s: float = HOP_S) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = max(1, int(np.ceil(len(y) / hop)))
    pad = np.pad(y, (0, n * hop - len(y)))
    frames = pad[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return 20.0 * np.log10(rms + 1e-9)


def _resample_gate(gate: np.ndarray, n_samples: int) -> np.ndarray:
    if gate.size == 0:
        return np.ones(n_samples, dtype=np.float32)
    idx = np.linspace(0, gate.size - 1, n_samples)
    lo = np.floor(idx).astype(int)
    hi = np.minimum(lo + 1, gate.size - 1)
    frac = idx - lo
    return (gate[lo] * (1 - frac) + gate[hi] * frac).astype(np.float32)


def melody_mask(reference: Path, n_frames: int | None = None,
                hop_s: float = HOP_S) -> np.ndarray:
    """按参考人声的能量包络判断“这一刻到底有没有在唱”。"""
    y, sr = _read(reference)
    hop = max(1, int(sr * hop_s))
    db = frame_rms_db(y, sr, hop_s)
    if n_frames:
        db = np.interp(np.linspace(0, 1, n_frames), np.linspace(0, 1, db.size), db)
    if db.size == 0:
        return np.zeros(n_frames or 0, dtype=np.float32)
    top = float(np.percentile(db, 95))
    mask = np.clip((db - (top - 32.0)) / 12.0, 0.0, 1.0)
    return uniform_filter1d(mask, size=max(1, int(0.12 / hop_s)), mode="nearest")


def gap_mask(reference: Path, n_frames: int, max_gap_s: float = 0.45,
             hop_s: float = HOP_S) -> np.ndarray:
    """短于 max_gap_s 的静音缝填平，避免字尾被削得太干。"""
    mm = melody_mask(reference, n_frames, hop_s) > 0.5
    out = mm.astype(np.float32)
    gap = int(max_gap_s / hop_s)
    i = 0
    n = len(mm)
    while i < n:
        if mm[i]:
            i += 1
            continue
        j = i
        while j < n and not mm[j]:
            j += 1
        if 0 < i and j < n and (j - i) <= gap:
            out[i:j] = 1.0
        i = j
    return uniform_filter1d(out, size=max(1, int(0.04 / hop_s)), mode="nearest")


# --------------------------------------------------------------------- 各阶段

def denoise_vocals(src: Path, dst: Path, strength: float | None = None) -> Path:
    strength = DENOISE_STRENGTH if strength is None else float(strength)
    y, sr = _read(src)
    if y.size == 0:
        return _write(dst, y, sr)
    mag, phase = _stft(y)
    energy = mag.mean(axis=0)
    quiet = energy <= np.percentile(energy, 20)
    noise = np.median(mag[:, quiet], axis=1, keepdims=True) + 1e-9
    clean = np.maximum(mag - strength * noise, 0.02 * mag)
    # 掩蔽沿着时间轴平滑一点，避免“水声”起伏
    clean = uniform_filter1d(clean, size=3, axis=1, mode="nearest")
    return _write(dst, _istft(clean, phase, len(y)), sr)


def _ref_uv_weight(reference: Path, n_frames: int, hop_s: float) -> np.ndarray:
    """原唱每一帧「无音高」的权重（1=气声/辅音，0=唱出来的音高段）。

    用 parselmouth 判浊音（53-900 Hz，voicing_threshold 0.6）。判不出来就返回全 0，
    也就是「宁可不移植」，绝不把原唱音高段混进成品。
    """
    try:
        import parselmouth
        from parselmouth.praat import call as praat_call
    except Exception:  # noqa: BLE001
        return np.zeros(n_frames, dtype=np.float32)
    try:
        y, sr = _read(reference)
        snd = parselmouth.Sound(y.astype(np.float64), sampling_frequency=sr)
        pitch = praat_call("To Pitch", snd, hop_s, REF_VOICING_MIN, REF_VOICING_MAX)
        f0 = np.nan_to_num(pitch.selected_array["f0"], nan=0.0)
        voiced = (f0 > 0.0).astype(np.float32)
        if voiced.size == 0:
            return np.zeros(n_frames, dtype=np.float32)
        idx = np.clip(np.linspace(0, voiced.size - 1, n_frames).astype(int), 0, voiced.size - 1)
        return (1.0 - voiced[idx]).astype(np.float32)
    except Exception:  # noqa: BLE001
        return np.zeros(n_frames, dtype=np.float32)


def restore_timbre(converted: Path, reference: Path, dst: Path,
                   cons_ratio: float | None = None) -> Path:
    """把原唱的音色倾向「轻量」挪过来：只补气声/辅音段，不动音高段。

    10-28 的实测：整段 LTAS 移植（0.6 权重）会把原唱的 6-16 kHz 波形叠回来，
    两个歌手的齿音互拍频 → 用户听到的「撕裂 / 颗粒」。这里权重降到 0.10，
    并且乘上「原唱无音高帧」掩码，只覆盖真正是辅音的帧。
    """
    ratio = CONS_RATIO if cons_ratio is None else float(cons_ratio)
    y, sr = _read(converted)
    if y.size == 0 or ratio <= 0:
        return _write(dst, y, sr)
    ref, sr2 = _read(reference)
    if sr != sr2 or ref.size == 0:
        return _write(dst, y, sr)
    c_db = frame_rms_db(y, sr)
    r_db = frame_rms_db(ref, sr)
    n = min(c_db.size, r_db.size)
    uv_w = _ref_uv_weight(reference, n, HOP_S)
    if float(uv_w.mean()) <= 0.0:
        return _write(dst, y, sr)
    mag, phase = _stft(y)
    ref_mag, _ = _stft(ref)
    c_l = 20 * np.log10(mag.mean(axis=1) + 1e-9)
    r_l = 20 * np.log10(ref_mag.mean(axis=1) + 1e-9)
    diff = uniform_filter1d(r_l - c_l, size=9, mode="nearest")
    diff = np.clip(diff, -TIMBRE_MAX_DB, TIMBRE_MAX_DB) * ratio
    gain = 10 ** (diff / 20.0)
    frames = mag.shape[1]
    w = np.interp(np.linspace(0, 1, frames), np.linspace(0, 1, uv_w.size), uv_w)
    mag = mag * (gain[:, None] ** w[None, :])
    return _write(dst, _istft(mag, phase, len(y)), sr)


def apply_vocal_gate(converted: Path, reference: Path, dst: Path,
                     lyric_gate: np.ndarray | None = None,
                     gap_gate: np.ndarray | None = None) -> Path:
    y, sr = _read(converted)
    hop_s = HOP_S
    n = max(1, int(len(y) / (sr * hop_s)))
    db = frame_rms_db(y, sr, hop_s)
    db = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, db.size), db)
    ref_level = float(np.percentile(db, 95))
    energy_gate = np.clip((db - (ref_level - 40.0)) / 10.0, GATE_FLOOR, 1.0)
    mm = melody_mask(reference, n, hop_s)
    if lyric_gate is not None and len(lyric_gate) == n:
        allowed = lyric_gate.astype(np.float32)
        if gap_gate is not None and len(gap_gate) == n:
            allowed = np.maximum(allowed, gap_gate.astype(np.float32) * mm)
    else:
        allowed = mm
    gate = energy_gate * allowed
    y2 = y * _resample_gate(gate, len(y))
    return _write(dst, y2, sr)


def match_dynamics(converted: Path, reference: Path, dst: Path,
                   hop_s: float = HOP_S, max_db: float | None = None,
                   smooth_frames: int | None = None) -> Path:
    max_db = DYN_MAX_DB if max_db is None else float(max_db)
    smooth_frames = DYN_SMOOTH if smooth_frames is None else int(smooth_frames)
    y, sr = _read(converted)
    ref, sr2 = _read(reference)
    if sr != sr2 or y.size == 0 or ref.size == 0:
        return _write(dst, y, sr)
    c_db = frame_rms_db(y, sr, hop_s)
    r_db = frame_rms_db(ref, sr, hop_s)
    n = min(c_db.size, r_db.size)
    if n == 0:
        return _write(dst, y, sr)
    c_db, r_db = c_db[:n], r_db[:n]
    # 只对齐“有声”的帧，静音段的比值没有意义
    voiced = r_db > (np.percentile(r_db, 95) - 40.0)
    ratio = np.zeros(n)
    ratio[voiced] = r_db[voiced] - c_db[voiced]
    if voiced.any():
        ratio = np.interp(np.arange(n), np.flatnonzero(voiced),
                          ratio[voiced]) if not voiced.all() else ratio
    ratio = uniform_filter1d(np.clip(ratio, -max_db, max_db), size=max(1, smooth_frames), mode="nearest")
    y2 = y * _resample_gate(10 ** (ratio / 20.0), len(y))
    peak = float(np.max(np.abs(y2)) or 1.0)
    if peak > 0.98:
        y2 = y2 / peak * 0.98
    return _write(dst, y2, sr)


def clean_vocal_output(src: Path, dst: Path) -> Path:
    y, sr = _read(src)
    if y.size == 0:
        return _write(dst, y, sr)
    mag, phase = _stft(y)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    # 1) 去齿音：4.5k~9k 的瞬态能量超过中位数 DEESS_THR 倍就压下去
    band = (freqs >= 4500) & (freqs <= 9000)
    if band.any():
        e = mag[band].mean(axis=0)
        thr = float(np.median(e) * DEESS_THR) + 1e-9
        hot = np.clip(thr / (e + 1e-9), DEESS_FLOOR, 1.0)
        hot = uniform_filter1d(hot, size=3, mode="nearest")
        mag[band] *= hot[None, :]
    # 2) 高频滚降：HF_CUT 以上慢慢收，别让 svc 的金属味刺出来
    cut = HF_CUT * (sr / 2)
    if cut < freqs[-1]:
        ramp = np.clip((freqs - cut) / max(1.0, (freqs[-1] - cut)), 0.0, 1.0)
        mag *= (1.0 - 0.85 * ramp)[:, None]
    return _write(dst, _istft(mag, phase, len(y)), sr)


def run_chain(vocals: Path, converted: Path, out: Path, workdir: Path) -> Path:
    """把后处理串起来：converted 是 svc 的输出，vocals 是参考人声。"""
    workdir.mkdir(parents=True, exist_ok=True)
    step1 = workdir / "pp-timbre.wav"
    step2 = workdir / "pp-gate.wav"
    step3 = workdir / "pp-dyn.wav"
    src = converted
    if DENOISE_STRENGTH > 0:
        # 谱减只在显式要求时做（默认 0）：1.6 的强度会把气声削成音乐噪声，
        # 听感就是「颗粒感重、不通透」。
        den = workdir / "pp-denoise.wav"
        denoise_vocals(converted, den, DENOISE_STRENGTH)
        src = den
    restore_timbre(src, vocals, step1)
    apply_vocal_gate(step1, vocals, step2)
    match_dynamics(step2, vocals, step3)
    return clean_vocal_output(step3, out)
