r"""知更鸟（崩坏：星穹铁道）Bert-VITS2 v2.3 本地推理封装。

模型来源（网上公开权重）：HuggingFace / Space `Sprt98/Bert-VITS2_HSR_Robin`
  - 训练轮次 5800、数据集作者 @红血球AE3803
  - 仓库内自带 `bert/chinese-roberta-wwm-ext-large`（中文 BERT），无需联网

用法：
  python bv2_say.py --text "你好，我是知更鸟。" --out logs/robin.wav
  python bv2_say.py --text "..." --out out.wav --sdp 0.3 --noise 0.667 --noise-w 0.8 --length 1.0
  python bv2_say.py --selftest

约定：
  - 本进程的 cwd 会被切到 bertvits2\repo（config.py 读 ./config.yml，路径都是相对的）
  - BV2_SKIP_BERT_CHECK=1 关掉 text/__init__.py 里那次联网 check_bert_models()
  - 输出采样率取模型 config 的 44100，后续交给 ffmpeg 统一转 48k
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = r"C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack"
REPO = os.path.join(ROOT, "bertvits2", "repo")
CKPT = os.path.join(REPO, "Data", "Robin", "models", "G_5800.pth")
HPARAMS = os.path.join(REPO, "Data", "Robin", "config.json")

os.environ.setdefault("BV2_SKIP_BERT_CHECK", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

_NET = None
_HPS = None


def _load(device="cpu"):
    global _NET, _HPS
    if _NET is not None:
        return _NET, _HPS
    os.chdir(REPO)
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    import utils  # noqa: PLC0415
    import infer as bv2_infer  # noqa: PLC0415

    hps = utils.get_hparams_from_file(HPARAMS)
    net = bv2_infer.get_net_g(CKPT, hps.version, device, hps)
    _NET, _HPS = net, hps
    return net, hps


def _stft(x, n_fft=1024, hop=256):
    import numpy as np  # noqa: PLC0415

    win = np.hanning(n_fft + 1)[:-1]
    pad = n_fft // 2
    xp = np.concatenate([np.zeros(pad), np.asarray(x, dtype=np.float32), np.zeros(pad)])
    n_frames = 1 + (len(xp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    return np.fft.rfft(xp[idx] * win, axis=1), win, n_frames


def _istft(spec, win, hop, length):
    import numpy as np  # noqa: PLC0415

    frames = np.fft.irfft(spec, n=len(win), axis=1) * win
    out = np.zeros(hop * (len(frames) - 1) + len(win), dtype=np.float32)
    norm = np.zeros_like(out)
    w2 = win ** 2
    for i, fr in enumerate(frames):
        s = i * hop
        out[s:s + len(win)] += fr
        norm[s:s + len(win)] += w2
    out = np.where(norm > 1e-8, out / np.maximum(norm, 1e-8), 0.0)
    pad = len(win) // 2
    return out[pad:pad + length]


def _dereverb(x, sr, strength=0.5, tau_ms=140.0, delay_ms=25.0, hop=256, n_fft=1024):
    """轻量去混响：对每个时频点用「延迟 + 指数平滑」估计后期混响能量并从当前能量里减掉。

    strength=0 关闭；0.4–0.6 可听出混响变干；>0.8 会伤齿音。
    """
    import numpy as np  # noqa: PLC0415

    spec, win, _ = _stft(x, n_fft=n_fft, hop=hop)
    mag = np.abs(spec)
    power = mag ** 2
    frame_rate = sr / hop
    smooth = float(np.exp(-1.0 / max(1e-6, tau_ms * 1e-3 * frame_rate)))
    delay = max(1, int(round(delay_ms * 1e-3 * frame_rate)))

    # 后期混响估计：把「之前的能量」按 tau 衰减地累积起来（因果平滑），再延后 delay 帧使用
    late = np.zeros_like(power)
    acc = np.zeros(power.shape[1], dtype=np.float32)
    for t in range(power.shape[0]):
        acc = acc * smooth + power[t] * (1.0 - smooth)
        late[t] = acc
    late = np.concatenate([np.zeros((delay, power.shape[1]), dtype=np.float32), late[:-delay]], axis=0) \
        if delay < power.shape[0] else np.zeros_like(power)

    # Wiener 式增益：当前能量里减掉 strength * 后期混响估计
    clean = np.maximum(power - strength * late, power * 0.05)
    gain = np.sqrt(clean / np.maximum(power, 1e-10))
    out = _istft(spec * gain, win, hop, len(x))
    peak = float(np.max(np.abs(out))) or 1.0
    if peak > 0.99:
        out = out * (0.99 / peak)
    return out.astype(np.float32)


_SENT_END = "。！？!?…\n"
_CLAUSE_END = "，,、；;：:"
# 不超过这个字数的文本整段一次合成（不切段拼接，语调更连贯、少「机器念稿感」）
AUTO_SINGLE_PASS_CHARS = 60


def _split_for_pauses(text: str, max_chars: int = 34, clause_split: bool = True,
                      gap_scale: float = 1.0):
    """把文本切成 (片段, 片段后面要留多少毫秒) 的序列。

    Bert-VITS2 一次性合成整段时，中文标点几乎不产生停顿，听起来「一口气念完」。
    切成小句后逐句合成、句间补静音，节奏接近真人说话。

    clause_split=False 时只在句末切（接缝少，语气更连贯）；gap_scale 缩放停顿。
    """
    import re  # noqa: PLC0415

    text = (text or "").strip()
    if not text:
        return []
    # 先按句末标点切
    sentences = [s for s in re.split(r"([。！？!?…\n]+)", text) if s]
    merged = []
    buf = ""
    for piece in sentences:
        if piece and piece[0] in _SENT_END:
            buf += piece
            merged.append((buf, 320))
            buf = ""
        else:
            buf += piece
    if buf.strip():
        merged.append((buf, 320))

    out = []
    for sent, gap in merged:
        sent = sent.strip()
        if not sent:
            continue
        if not clause_split:
            pieces = [(sent, gap)]
        else:
            # 长句再按逗号类标点切，避免单句过长导致语调飘
            parts = [p for p in re.split(r"([，,、；;：:])", sent) if p]
            cur = ""
            pieces = []
            for p in parts:
                if p and p[0] in _CLAUSE_END:
                    cur += p
                    pieces.append((cur, 180))
                    cur = ""
                else:
                    cur += p
            if cur.strip():
                pieces.append((cur, gap))
        # 太长的片段按字数硬切
        for chunk, g in pieces:
            chunk = chunk.strip()
            while len(chunk) > max_chars:
                out.append((chunk[:max_chars], 180 * gap_scale))
                chunk = chunk[max_chars:]
            if chunk:
                out.append((chunk, g * gap_scale))
    if out:
        out[-1] = (out[-1][0], 0)
    return out


def _trim_silence(audio, sr, thresh=0.012, keep=0.03):
    """掐掉片段首尾的静音，避免拼接后出现「空白 + 拖音」。"""
    import numpy as np  # noqa: PLC0415

    if len(audio) == 0:
        return audio
    a = np.asarray(audio, dtype=np.float32)
    idx = np.where(np.abs(a) > thresh)[0]
    if len(idx) == 0:
        return a[: int(0.02 * sr)]
    lo = max(0, idx[0] - int(keep * sr))
    hi = min(len(a), idx[-1] + int(keep * sr))
    return a[lo:hi]


def _split_phrases(audio, sr, gap_ms=140.0, floor_db=-38.0):
    """按能量静音把音频切成短语区间 [(start, end, gap_after)]（采样点）。

    Bert-VITS2 的语调在同一次合成内部也偏平，按短语做「降调 + 轻微随机」
    更像真人说话；这里先找短语边界。
    """
    import numpy as np  # noqa: PLC0415

    a = np.asarray(audio, dtype=np.float32)
    if len(a) == 0:
        return []
    win = max(1, int(sr * 0.01))
    frames = len(a) // win
    if frames < 2:
        return [(0, len(a), 0)]
    e = np.array([float(np.sqrt(np.mean(a[i * win:(i + 1) * win] ** 2))) for i in range(frames)])
    peak = float(e.max()) or 1.0
    thr = peak * (10 ** (floor_db / 20.0))
    silent = e < thr
    min_gap = max(1, int(gap_ms / 10.0))

    regions = []
    run_start = None
    for i, s in enumerate(silent):
        if s and run_start is None:
            run_start = i
        elif not s and run_start is not None:
            if i - run_start >= min_gap:
                regions.append((run_start * win, i * win))
            run_start = None

    phrases = []
    prev = 0
    for gs, ge in regions:
        if gs > prev:
            phrases.append((prev, gs, ge - gs))
        prev = ge
    if prev < len(a):
        phrases.append((prev, len(a), 0))
    return phrases


def _rubberband(in_path, out_path, pitch=1.0, tempo=1.0):
    """用 ffmpeg 的 rubberband 做保共振峰的移调/变速。"""
    ff = os.path.join(ROOT, "tools", "ffmpeg", "ffmpeg.exe")
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", in_path,
           "-af", f"rubberband=pitch={pitch:.5f}:tempo={tempo:.5f}", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"rubberband failed: {(proc.stderr or '')[-300:]}")


def _shape_prosody(audio, sr, strength=0.5, seed=7):
    """给整段合成音频加「降调 + 逐短语微小随机」，缓解念稿感。

    规则：第 i/N 个短语的音高乘子从 +2*strength% 线性降到 -2.5*strength%，
    再叠加 ±0.4*strength% 的随机抖动；语速 ±3*strength% 随机。
    """
    import random  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    phrases = _split_phrases(audio, sr)
    if len(phrases) < 2:
        return audio
    rng = random.Random(seed)
    n = len(phrases)
    out = []
    tmpdir = tempfile.mkdtemp(prefix="prosody-")
    try:
        for idx, (s, e, gap) in enumerate(phrases):
            seg = np.asarray(audio[s:e], dtype=np.float32)
            if len(seg) < int(sr * 0.05):
                out.append(seg)
                if gap:
                    out.append(np.zeros(gap, dtype=np.float32))
                continue
            t = idx / max(1, n - 1)  # 0 → 1
            factor = 1.0 + strength * (0.02 * (1 - t) - 0.025 * t)
            factor *= 1.0 + rng.uniform(-0.004, 0.004) * strength
            tempo = 1.0 + rng.uniform(-0.03, 0.03) * strength
            src = os.path.join(tmpdir, f"p{idx}.wav")
            dst = os.path.join(tmpdir, f"q{idx}.wav")
            sf.write(src, seg, sr)
            try:
                _rubberband(src, dst, pitch=factor, tempo=tempo)
                seg2, _ = sf.read(dst, dtype="float32")
                out.append(np.asarray(seg2, dtype=np.float32))
            except Exception:
                out.append(seg)
            if gap:
                out.append(np.zeros(gap, dtype=np.float32))
    finally:
        import shutil  # noqa: PLC0415

        shutil.rmtree(tmpdir, ignore_errors=True)
    res = np.concatenate(out) if out else np.asarray(audio, dtype=np.float32)
    return res


def _synth_once(text, bv2_infer, hps, net, device, sdp, noise, noise_w, length,
                sid, lang, style_text, style_weight):
    return bv2_infer.infer(
        text,
        0,  # emotion（2.3 路径未使用 CLAP 情感）
        sdp,
        noise,
        noise_w,
        length,
        sid,
        lang,
        hps,
        net,
        device,
        reference_audio=None,
        skip_start=False,
        skip_end=False,
        style_text=style_text,
        style_weight=style_weight,
    )


def say(text, out_wav, sdp=0.2, noise=0.6, noise_w=0.8, length=1.0, sid="Robin", lang="ZH",
        device="cpu", style_text=None, style_weight=0.7, dereverb=0.35, pauses=True,
        max_chars=34, clause_split=True, keep_s=0.03, gap_scale=1.0, fade_ms=6.0,
        dc_block=True, prosody=0.0):
    # 先固定输出路径：_load() 会把 cwd 切到 repo，之后相对路径会跟着变
    out_wav = os.path.abspath(out_wav)
    net, hps = _load(device)
    import infer as bv2_infer  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    import soundfile as sf  # noqa: PLC0415

    sr = int(hps.data.sampling_rate)
    # 短句整段一次合成：分段拼接会让每句重新起音，听感「一句一句念」、音调发直。
    # 只有长文本才切段（避免单次合成过长导致语调漂移）。
    if pauses and len((text or "").strip()) <= AUTO_SINGLE_PASS_CHARS:
        chunks = [(text, 0)]
    elif pauses:
        chunks = _split_for_pauses(text, max_chars=max_chars, clause_split=clause_split,
                                   gap_scale=gap_scale)
    else:
        chunks = [(text, 0)]
    if not chunks:
        chunks = [(text, 0)]

    fade = max(0, int(sr * fade_ms / 1000.0))
    pieces = []
    for chunk_text, gap_ms in chunks:
        audio = _synth_once(chunk_text, bv2_infer, hps, net, device, sdp, noise,
                            noise_w, length, sid, lang, style_text, style_weight)
        audio = _trim_silence(audio, sr, keep=keep_s)
        if fade and len(audio) > 2 * fade:
            # 片段两端做短淡入淡出，消除硬切带来的「咔」和金属感
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio = audio.copy()
            audio[:fade] *= ramp
            audio[-fade:] *= ramp[::-1]
        pieces.append(audio)
        if gap_ms > 0:
            pieces.append(np.zeros(int(sr * gap_ms / 1000.0), dtype=np.float32))
    audio = np.concatenate(pieces) if len(pieces) > 1 else pieces[0]

    if dc_block and len(audio):
        # 去直流（Wiener 增益容易留下低频偏置，听起来「闷」）
        audio = audio - float(np.mean(audio))

    if prosody > 0:
        # 逐短语降调 + 轻微随机，缓解「念稿」的平直语调
        audio = _shape_prosody(audio, sr, strength=prosody)

    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    if dereverb > 0:
        audio = _dereverb(audio, sr, strength=dereverb)
    sf.write(out_wav, audio, sr)
    peak = float(abs(audio).max()) if len(audio) else 0.0
    return {
        "ok": True,
        "out": out_wav,
        "sampling_rate": sr,
        "dur": round(len(audio) / sr, 3),
        "peak": round(peak, 4),
        "bytes": os.path.getsize(out_wav),
        "text": text,
        "dereverb": dereverb,
        "chunks": len(chunks),
        "prosody": prosody,
        "params": {
            "sdp": sdp, "noise": noise, "noise_w": noise_w, "length": length,
            "max_chars": max_chars, "clause_split": clause_split,
            "keep_s": keep_s, "gap_scale": gap_scale, "fade_ms": fade_ms,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--sdp", type=float, default=0.2)
    ap.add_argument("--noise", type=float, default=0.6)
    ap.add_argument("--noise-w", dest="noise_w", type=float, default=0.8)
    ap.add_argument("--length", type=float, default=1.0)
    ap.add_argument("--sid", default="Robin")
    ap.add_argument("--lang", default="ZH")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--style", default=None)
    ap.add_argument("--dereverb", type=float, default=0.35,
                    help="去混响强度 0=关，0.4-0.6 自然，>0.8 伤齿音")
    ap.add_argument("--max-chars", dest="max_chars", type=int, default=34,
                    help="单段最多字数（越大接缝越少，语气越连贯）")
    ap.add_argument("--no-clause-split", dest="clause_split", action="store_false",
                    help="只在句末切分，不按逗号切")
    ap.add_argument("--keep", dest="keep_s", type=float, default=0.03,
                    help="掐静音时两端保留多少秒（留一点呼吸更自然）")
    ap.add_argument("--gap-scale", dest="gap_scale", type=float, default=1.0,
                    help="句间停顿缩放")
    ap.add_argument("--fade-ms", dest="fade_ms", type=float, default=6.0,
                    help="片段两端淡入淡出毫秒数")
    ap.add_argument("--prosody", type=float, default=0.0,
                    help="逐短语降调强度 0=关，0.4-0.8 可听出语调起伏")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    kw = dict(dereverb=args.dereverb, prosody=args.prosody, max_chars=args.max_chars,
              clause_split=args.clause_split, keep_s=args.keep_s,
              gap_scale=args.gap_scale, fade_ms=args.fade_ms)
    t0 = time.time()
    if args.selftest or not args.text:
        info = say("你好，我是知更鸟。今天想听我唱首歌吗？",
                   args.out or os.path.join(ROOT, "logs", "robin_net_selftest.wav"),
                   args.sdp, args.noise, args.noise_w, args.length, args.sid, args.lang,
                   args.device, args.style, **kw)
    else:
        info = say(args.text, args.out,
                   args.sdp, args.noise, args.noise_w, args.length, args.sid, args.lang,
                   args.device, args.style, **kw)
    info["elapsed_s"] = round(time.time() - t0, 2)
    print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
