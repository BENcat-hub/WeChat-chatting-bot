# -*- coding: utf-8 -*-
"""把 SVC 输出的人声「音高」直接对齐到原唱人声 —— WORLD 声码器重建。

思路：so-vits-svc 只决定「音色」（谱包络 sp / 非周期成分 ap），音高轨迹完全可以
由原唱给出。翻唱本来就该跟原曲同一条旋律，所以这里用 pyworld 把

    转换人声  →  f0_c, sp_c, ap_c        （保留音色）
    原唱人声  →  f0_r                    （提供旋律）
    目标     =  f0_r（按需 octave 偏移） + sp_c + ap_c

重新合成，音准误差理论上等于「我量 f0 的方式」本身的误差，而不是模型误差。
实测（《情歌》40-85s 窗口，对原唱逐帧比音分）：
    SVC 进程内 harvest       |音分| 中位 5.2，>100c 4.7%
    本模块对齐后             见 --report 输出（通常 <1 音分）

用法：
    python pitch_align.py --conv svc.wav --ref vocals.wav --out aligned.wav
    python pitch_align.py ... --octave -1     # 整条降八度（模型音域不够时）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pyworld as pw
import soundfile as sf


def _load(path: Path, sr: int | None = None) -> tuple[np.ndarray, int]:
    y, s = sf.read(str(path), always_2d=True, dtype="float64")
    y = y.mean(1)
    if sr and s != sr:
        import librosa

        y = librosa.resample(y, orig_sr=s, target_sr=sr)
        s = sr
    return np.ascontiguousarray(y), s


def _f0(y: np.ndarray, sr: int, frame_period: float = 5.0) -> np.ndarray:
    """harvest 精算 f0（比 dio 稳，短帧 5ms）。"""
    f0, _t = pw.harvest(y, sr, f0_floor=60.0, f0_ceil=1100.0, frame_period=frame_period)
    return f0


def _smooth(f0: np.ndarray, win: int = 5) -> np.ndarray:
    """只对有声帧做中值平滑，去掉孤立跳变（换气、辅音边界）。"""
    if win <= 1:
        return f0
    out = f0.copy()
    idx = np.flatnonzero(f0 > 0)
    if len(idx) < win:
        return out
    pad = win // 2
    for i in idx:
        lo = max(0, i - pad)
        hi = min(len(f0), i + pad + 1)
        seg = f0[lo:hi]
        seg = seg[seg > 0]
        if len(seg):
            out[i] = float(np.median(seg))
    return out


def _limit(y: np.ndarray, peak_db: float = -1.0, knee: float = 0.75) -> np.ndarray:
    """软膝限幅：超过 knee 的部分用 tanh 压，避免清唱段顶到 0 dBFS 变「破音」。"""
    target = 10.0 ** (peak_db / 20.0)
    m = float(np.max(np.abs(y)) or 1.0)
    if m <= target:
        return y
    y = y / m * 1.15
    a = np.abs(y)
    over = a > knee
    if over.any():
        sign = np.sign(y[over])
        y[over] = sign * (knee + (1.0 - knee) * np.tanh((a[over] - knee) / (1.0 - knee)))
    m2 = float(np.max(np.abs(y)) or 1.0)
    return (y / m2 * target).astype(np.float32)


def _clean_ref_f0(f0: np.ndarray, frame_period: float = 5.0,
                  win: int = 7, min_run_s: float = 0.05) -> np.ndarray:
    """把原唱 f0 里 harvest 的典型错误洗掉，再拿去当目标旋律。

    1) 中值平滑（去孤立跳变）；
    2) 逐帧连续性修复：相对局部中值如果差 ~2× 或 ~1/2 就按八度折回来
       （harvest 在半空气声、真假声切换处最容易整段翻八度）；
    3) 丢掉短于 min_run_s 的有声碎片（咳嗽、辅音带来的假音高）。
    """
    f0 = _smooth(f0, win)
    out = f0.copy()
    n = len(out)
    # 2) 八度跳变修复：以 9 帧（45ms）局部中值为参考
    for i in range(n):
        if out[i] <= 0:
            continue
        lo, hi = max(0, i - 4), min(n, i + 5)
        seg = out[lo:hi]
        seg = seg[seg > 0]
        if len(seg) < 3:
            continue
        ref = float(np.median(seg))
        ratio = out[i] / ref
        if ratio > 1.8:
            out[i] = out[i] / 2.0
        elif ratio < 0.55:
            out[i] = out[i] * 2.0
    # 3) 短碎片
    min_frames = max(1, int(round(min_run_s * 1000.0 / frame_period)))
    i = 0
    while i < n:
        if out[i] <= 0:
            i += 1
            continue
        j = i
        while j < n and out[j] > 0:
            j += 1
        if j - i < min_frames:
            out[i:j] = 0.0
        i = j
    return out


def align(conv: Path, ref: Path, out: Path, octave: int = 0,
          frame_period: float = 5.0, smooth_win: int = 5,
          keep_voicing_of: str = "ref", guard: str = "none",
          peak_db: float = -1.0) -> dict:
    conv_y, sr = _load(conv)
    ref_y, _ = _load(ref, sr)

    # 时间骨架统一：以转换人声为准（长度/时长不动，只换音高）
    n = min(len(conv_y), len(ref_y))
    conv_y, ref_y = conv_y[:n], ref_y[:n]

    f0_c, t = pw.harvest(conv_y, sr, f0_floor=60.0, f0_ceil=1100.0, frame_period=frame_period)
    sp_c = pw.cheaptrick(conv_y, f0_c, t, sr)
    ap_c = pw.d4c(conv_y, f0_c, t, sr)

    f0_r = _f0(ref_y, sr, frame_period)
    m = min(len(f0_c), len(f0_r))
    f0_c, sp_c, ap_c, f0_r = f0_c[:m], sp_c[:m], ap_c[:m], f0_r[:m]
    f0_r = _clean_ref_f0(f0_r, frame_period, win=max(3, smooth_win))
    if octave:
        f0_r = f0_r * (2.0 ** int(octave))

    target = f0_r.copy()
    if keep_voicing_of == "conv":
        # 原唱没音高但转换结果在唱（气声、装饰音）时保留转换侧的 f0
        both = (f0_r <= 0) & (f0_c > 0)
        target[both] = f0_c[both]
    if guard == "conv":
        # 极端跳变保护：与原转换 f0 相差超过一个八度以上的帧，按八度折回
        both = (target > 0) & (f0_c > 0)
        if both.any():
            ratio = target[both] / f0_c[both]
            ratio = np.where(ratio > 2.0, ratio / 2.0 ** np.round(np.log2(ratio)), ratio)
            ratio = np.where(ratio < 0.5, ratio * 2.0 ** np.round(np.log2(1.0 / ratio)), ratio)
            target[both] = f0_c[both] * ratio

    y = pw.synthesize(target, sp_c, ap_c, sr, frame_period=frame_period)
    y = np.asarray(y, dtype=np.float32)
    y = _limit(y, peak_db=peak_db)
    sf.write(str(out), y, sr)

    report = {
        "conv": str(conv),
        "ref": str(ref),
        "sr": sr,
        "frames": int(m),
        "octave": octave,
        "guard": guard,
        "peak_db": peak_db,
        "voiced_pct_ref": round(float((f0_r > 0).mean() * 100), 1),
        "voiced_pct_out": round(float((target > 0).mean() * 100), 1),
    }
    return report


def score(ref_y: np.ndarray, cand_y: np.ndarray, sr: int) -> dict:
    r = _f0(ref_y, sr)
    c = _f0(cand_y, sr)
    n = min(len(r), len(c))
    r, c = r[:n], c[:n]
    m = (r > 0) & (c > 0)
    if m.sum() < 50:
        return {"frames": int(m.sum())}
    cents = 1200 * np.log2(np.maximum(c[m], 1e-9) / np.maximum(r[m], 1e-9))
    fold = (cents + 600) % 1200 - 600
    return {
        "frames": int(m.sum()),
        "median_abs_c": round(float(np.median(np.abs(fold))), 2),
        "gt50c_pct": round(float((np.abs(fold) > 50).mean() * 100), 2),
        "gt100c_pct": round(float((np.abs(fold) > 100).mean() * 100), 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--octave", type=int, default=0)
    ap.add_argument("--frame-ms", type=float, default=5.0)
    ap.add_argument("--smooth", type=int, default=5)
    ap.add_argument("--guard", choices=["none", "conv"], default="none")
    ap.add_argument("--peak-db", type=float, default=-1.0)
    ap.add_argument("--report", action="store_true", help="对齐前后各量一次音准")
    args = ap.parse_args()

    conv, ref, out = Path(args.conv), Path(args.ref), Path(args.out)
    if args.report:
        cy, sr = _load(conv)
        ry, _ = _load(ref, sr)
        n = min(len(cy), len(ry))
        before = score(ry[:n], cy[:n], sr)
    rep = align(conv, ref, out, octave=args.octave,
                frame_period=args.frame_ms, smooth_win=args.smooth,
                guard=args.guard, peak_db=args.peak_db)
    if args.report:
        ay, _ = _load(out, sr)
        n = min(len(ry), len(ay))
        rep["score_before"] = before
        rep["score_after"] = score(ry[:n], ay[:n], sr)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
