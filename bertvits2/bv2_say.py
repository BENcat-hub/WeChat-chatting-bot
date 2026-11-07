# -*- coding: utf-8 -*-
"""Bert-VITS2 (fishaudio v1.1.1) 单句合成。

对外只暴露一个函数（与原 bertvits2/bv2_v1_say.py 的 gl_say 同签名）：

    gl_say(text, out_wav, sid="纳西妲", sdp=0.2, noise=0.6, noise_w=0.8, length=1.0)

模型常驻（进程内缓存），第一次调用会加载 checkpoint + BERT，之后单句只做推理。
"""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # HuggingFace 走镜像
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# text/chinese_bert.py 用相对路径 ./bert/chinese-roberta-wwm-ext-large 找 BERT
if os.getcwd() != ROOT:
    os.chdir(ROOT)

import soundfile as sf  # noqa: E402
import torch  # noqa: E402

import commons  # noqa: E402
import utils  # noqa: E402
from models import SynthesizerTrn  # noqa: E402
from text import cleaned_text_to_sequence, get_bert, symbols  # noqa: E402
from text.cleaner import clean_text  # noqa: E402

DEVICE = os.environ.get("BV2_DEVICE", "cuda")
DEFAULT_SID = os.environ.get("BV2_SPEAKER", "纳西妲")
DEFAULT_DIR = os.path.join(ROOT, "pretrained", DEFAULT_SID)
DEFAULT_CONFIG = os.path.join(DEFAULT_DIR, "config.json")
DEFAULT_PTH = os.path.join(DEFAULT_DIR, "G_114000.pth")

_LOCK = threading.Lock()
_STATE: dict[tuple[str, str, str], tuple[object, object]] = {}


def _device() -> str:
    if DEVICE.startswith("cuda") and not torch.cuda.is_available():
        return "cpu"
    return DEVICE


def _load(config_path: str = DEFAULT_CONFIG, pth_path: str = DEFAULT_PTH):
    """加载（并缓存）生成器与 hparams。"""
    dev = _device()
    key = (config_path, pth_path, dev)
    with _LOCK:
        if key in _STATE:
            return _STATE[key]
        if not os.path.exists(pth_path):
            raise FileNotFoundError(f"checkpoint 不存在: {pth_path}")
        hps = utils.get_hparams_from_file(config_path)
        net = SynthesizerTrn(
            len(symbols),
            hps.data.filter_length // 2 + 1,
            hps.train.segment_size // hps.data.hop_length,
            n_speakers=hps.data.n_speakers,
            **hps.model,
        ).to(dev)
        net.eval()
        utils.load_checkpoint(pth_path, net, None, skip_optimizer=True)
        _STATE[key] = (net, hps)
        return net, hps


def _text_to_inputs(text: str, language: str, hps, dev: str):
    norm_text, phone, tone, word2ph = clean_text(text, language)
    phone, tone, lang_ids = cleaned_text_to_sequence(phone, tone, language)
    if hps.data.add_blank:
        phone = commons.intersperse(phone, 0)
        tone = commons.intersperse(tone, 0)
        lang_ids = commons.intersperse(lang_ids, 0)
        for i in range(len(word2ph)):
            word2ph[i] *= 2
        word2ph[0] += 1
    try:
        bert = get_bert(norm_text, word2ph, language, dev)
    except Exception as exc:  # BERT 权重缺失时退零向量，先保证能出声
        print(f"[bv2] BERT 不可用（{type(exc).__name__}: {exc}），改用零向量", flush=True)
        bert = torch.zeros(1024, len(phone))
    if bert.shape[-1] != len(phone):
        # 极少数标点会让 BERT 序列与音素错位，退回零向量（音色仍在，韵律略平）
        bert = torch.zeros(1024, len(phone))
    if language == "ZH":
        ja_bert = torch.zeros(768, len(phone))
    elif language == "JA":
        ja_bert = bert
        bert = torch.zeros(1024, len(phone))
    else:
        bert = torch.zeros(1024, len(phone))
        ja_bert = torch.zeros(768, len(phone))
    return bert, ja_bert, phone, tone, lang_ids


def synthesize(
    text: str,
    sid: str = DEFAULT_SID,
    sdp: float = 0.2,
    noise: float = 0.6,
    noise_w: float = 0.8,
    length: float = 1.0,
    language: str = "ZH",
    config_path: str = DEFAULT_CONFIG,
    pth_path: str = DEFAULT_PTH,
) -> tuple[np.ndarray, int, dict]:
    """返回 (audio float32, sample_rate, info)。"""
    t0 = time.time()
    net, hps = _load(config_path, pth_path)
    t_load = time.time() - t0
    dev = next(net.parameters()).device
    t1 = time.time()
    bert, ja_bert, phone, tone, lang_ids = _text_to_inputs(text, language, hps, str(dev))
    t_text = time.time() - t1
    if sid not in hps.data.spk2id:
        raise KeyError(f"说话人 {sid!r} 不在模型里（共 {len(hps.data.spk2id)} 个）")
    with torch.no_grad():
        t2 = time.time()
        audio = net.infer(
            torch.LongTensor(phone).unsqueeze(0).to(dev),
            torch.LongTensor([len(phone)]).to(dev),
            torch.LongTensor([hps.data.spk2id[sid]]).to(dev),
            torch.LongTensor(tone).unsqueeze(0).to(dev),
            torch.LongTensor(lang_ids).unsqueeze(0).to(dev),
            bert.unsqueeze(0).to(dev),
            ja_bert.unsqueeze(0).to(dev),
            sdp_ratio=sdp,
            noise_scale=noise,
            noise_scale_w=noise_w,
            length_scale=length,
        )
        y = audio[0][0, 0].data.cpu().numpy().astype(np.float32)
        t_infer = time.time() - t2
    peak = float(np.max(np.abs(y))) or 1.0
    if peak > 0.99:
        y = y / peak * 0.99
    info = {"load_s": round(t_load, 2), "text_s": round(t_text, 2), "infer_s": round(t_infer, 2),
            "total_s": round(time.time() - t0, 2), "samples": int(y.shape[0])}
    return y, int(hps.data.sampling_rate), info


def gl_say(text: str, out_wav: str, sid: str = DEFAULT_SID, sdp: float = 0.2,
           noise: float = 0.6, noise_w: float = 0.8, length: float = 1.0) -> dict:
    """合成一句话到 out_wav，返回耗时信息。"""
    y, sr, info = synthesize(text, sid=sid, sdp=sdp, noise=noise, noise_w=noise_w, length=length)
    os.makedirs(os.path.dirname(out_wav) or ".", exist_ok=True)
    sf.write(out_wav, y, sr)
    info["sr"] = sr
    info["dur_s"] = round(y.shape[0] / sr, 2)
    return info


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Bert-VITS2 单句合成")
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", default=os.path.join(ROOT, "..", "logs", "bv2-test.wav"))
    ap.add_argument("--sid", default=DEFAULT_SID)
    ap.add_argument("--sdp", type=float, default=0.2)
    ap.add_argument("--noise", type=float, default=0.6)
    ap.add_argument("--noise-w", type=float, default=0.8)
    ap.add_argument("--length", type=float, default=1.0)
    a = ap.parse_args()
    info = gl_say(a.text, a.out, sid=a.sid, sdp=a.sdp, noise=a.noise, noise_w=a.noise_w, length=a.length)
    print(info)
    print(os.path.abspath(a.out))


if __name__ == "__main__":
    main()
