"""Bert-VITS2 **v1.1.0** inference for the GuetLzy 16-speaker 原神 model (G_114000).

Reuses the v2.3 repo's text front-end (identical 112-symbol set) and loads the
checkpoint with the bundled oldVersion/V110 architecture.

CLI:
  python bv2_v1_say.py --text "..." --out a.wav --sid 纳西妲 [--list-speakers]
"""
import argparse
import json
import os
import sys

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.join(STACK, "bertvits2", "repo")
V110 = os.path.join(REPO, "oldVersion", "V110")
DEFAULT_CFG = os.path.join(STACK, "bertvits2", "pretrained", "guetlzy", "config.json")
DEFAULT_CKPT = os.path.join(STACK, "bertvits2", "pretrained", "guetlzy", "G_114000.pth")

ORIG_CWD = os.getcwd()
os.chdir(REPO)
sys.path.insert(0, REPO)


def speakers(cfg_path=DEFAULT_CFG):
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    return cfg["data"]["spk2id"]


def _load(ckpt_path, cfg_path, device="cpu"):
    import importlib
    import types

    import torch
    from text.symbols import symbols

    # oldVersion/V110/__init__.py pulls in the Japanese cleaner (needs MeCab);
    # inject lightweight parent packages so only V110/models.py + V110/text/symbols.py load.
    for name, path in (("oldVersion", os.path.join(REPO, "oldVersion")),
                       ("oldVersion.V110", V110)):
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = [path]
            sys.modules[name] = pkg
    v110_models = importlib.import_module("oldVersion.V110.models")
    SynthesizerTrn = v110_models.SynthesizerTrn

    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    mcfg = dict(cfg["model"])
    kwargs = dict(
        n_vocab=len(symbols),
        spec_channels=cfg["data"]["filter_length"] // 2 + 1,
        segment_size=cfg["train"]["segment_size"],
        # this checkpoint was trained with a 4-layer transformer flow (1123 keys exact match)
        n_layers_trans_flow=4,
        **mcfg,
    )
    net = SynthesizerTrn(**kwargs)
    net.eval()
    blob = torch.load(ckpt_path, map_location="cpu")
    state = blob["model"] if isinstance(blob, dict) and "model" in blob else blob
    missing, unexpected = net.load_state_dict(state, strict=False)
    if missing or unexpected:
        real_missing = [k for k in missing if "dur" not in k and "disc" not in k]
        real_unexpected = [k for k in unexpected if "dur" not in k and "disc" not in k]
        if real_missing or real_unexpected:
            raise RuntimeError(
                f"state_dict mismatch: missing={real_missing[:6]} unexpected={real_unexpected[:6]}")
    net = net.to(device)
    return net, cfg


def _install_bert(bert_dir, device="cpu"):
    """Point the v2.3 Chinese BERT hook at an arbitrary local HF checkpoint.

    The GuetLzy model is v1.1.0 and expects a 768-dim BERT, while the stock
    chinese-roberta-wwm-ext-large in this repo is 1024-dim.
    """
    import torch
    from transformers import AutoModelForMaskedLM, AutoTokenizer

    import text.chinese_bert as cb

    tok = AutoTokenizer.from_pretrained(bert_dir)
    model = AutoModelForMaskedLM.from_pretrained(bert_dir).to(device).eval()

    def _feat(text, word2ph, device=None, style_text=None, style_weight=0.7):
        with torch.no_grad():
            inputs = tok(text, return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            res = model(**inputs, output_hidden_states=True)
            res = torch.cat(res["hidden_states"][-3:-2], -1)[0].cpu()
        if len(word2ph) != len(text) + 2:
            raise ValueError(f"word2ph len {len(word2ph)} != len(text)+2 ({len(text) + 2})")
        feats = [res[i].repeat(int(n), 1) for i, n in enumerate(word2ph)]
        return torch.cat(feats, dim=0).T

    cb.get_bert_feature = _feat
    return model.config.hidden_size


def say(text, out_wav, sid="纳西妲", noise=0.6, noise_w=0.8, length=1.0, sdp=0.2,
        device="cpu", cfg_path=DEFAULT_CFG, ckpt_path=DEFAULT_CKPT,
        bert_dir=None, ja_dim=768):
    import numpy as np
    import soundfile as sf
    import torch
    import infer as bv2_infer  # repo v2.3 module: text front-end + hps helpers

    if not os.path.isabs(out_wav):
        out_wav = os.path.join(ORIG_CWD, out_wav)
    net, cfg = _load(ckpt_path, cfg_path, device)
    spk = speakers(cfg_path)
    if isinstance(sid, str):
        if sid not in spk:
            raise KeyError(f"unknown speaker {sid!r}; choose from {list(spk)}")
        sid_i = spk[sid]
    else:
        sid_i = int(sid)

    # minimal hps shim: get_text needs hps.data.add_blank
    class _D:
        pass

    hps = _D()
    hps.data = _D()
    hps.data.add_blank = bool(cfg["data"].get("add_blank", True))
    hps.data.sampling_rate = int(cfg["data"]["sampling_rate"])

    if bert_dir:
        _install_bert(bert_dir, device)

    bert, ja_bert, _en_bert, phone, tone, language = bv2_infer.get_text(
        text, "ZH", hps, device)
    # v1.1.0 TextEncoder硬编码 bert_proj=1024(中文 large) / ja_bert_proj=768(日语 base)；
    # v2.3 的 get_text 会给 ja_bert 造 1024 维随机张量，这里按 v1.1.0 infer_cli 的约定置零。
    ja_bert = torch.zeros(ja_dim, phone.shape[-1])
    with torch.no_grad():
        audio = net.infer(
            phone.unsqueeze(0).to(device),
            torch.LongTensor([phone.shape[-1]]).to(device),
            torch.LongTensor([sid_i]).to(device),
            tone.unsqueeze(0).to(device),
            language.unsqueeze(0).to(device),
            bert.unsqueeze(0).to(device),
            ja_bert.unsqueeze(0).to(device),
            noise_scale=noise,
            length_scale=length,
            noise_scale_w=noise_w,
            sdp_ratio=sdp,
        )[0]
    audio = audio.squeeze(0).cpu().numpy()
    sr = hps.data.sampling_rate
    os.makedirs(os.path.dirname(out_wav) or ".", exist_ok=True)
    sf.write(out_wav, np.asarray(audio, dtype="float32").reshape(-1), sr, subtype="PCM_16")
    return {"out": out_wav, "sid": sid, "sr": sr,
            "dur": round(float(np.asarray(audio).shape[-1]) / sr, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--sid", default="纳西妲")
    ap.add_argument("--noise", type=float, default=0.6)
    ap.add_argument("--noise-w", dest="noise_w", type=float, default=0.8)
    ap.add_argument("--length", type=float, default=1.0)
    ap.add_argument("--sdp", type=float, default=0.2)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--bert-dir", dest="bert_dir", default=None,
                    help="local HF checkpoint for the Chinese BERT (768-dim for this model)")
    ap.add_argument("--list-speakers", action="store_true")
    args = ap.parse_args()
    if args.list_speakers:
        print(json.dumps(speakers(), ensure_ascii=False))
        return 0
    if not args.text or not args.out:
        ap.error("--text and --out are required")
    res = say(args.text, args.out, sid=args.sid, noise=args.noise,
              noise_w=args.noise_w, length=args.length, sdp=args.sdp,
              device=args.device, bert_dir=args.bert_dir)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

