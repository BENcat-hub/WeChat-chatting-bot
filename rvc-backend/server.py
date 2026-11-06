# -*- coding: utf-8 -*-
"""rvc-backend —— 点歌翻唱后端（Gradio），供 astrbot_plugin_matsuko_cover 调用。

2026-11-03 目录被误删后重建。对外契约严格对齐插件的 gradio_client 调用：

    /show_model          -> list[str]                 模型名（= models/ 下的子目录名）
    /list_voice_profiles -> list[dict]                可选：音色显示名
    /show_msst_models    -> list[str]                 可选：分离模型（本实现只有 demucs）
    /cache_info          -> dict                      可选
    /clear_cache         -> str                       可选
    /convert(...)        -> (out_path, "false")       主入口，返回值元组第二项是缓存标记

/convert 的参数名与顺序**必须**与插件 main.py 的 legacy 分支一致，否则
gradio_client 会报参数不匹配：

    song_name_src, key_shift, vocal_vol, inst_vol, model_dropdown,
    reverb_intensity, delay_intensity, uvr5_agg, uvr5_tta, uvr5_postprocess,
    uvr5_window_size, uvr5_high_end_process, msst_batch_size, msst_num_overlap,
    msst_normalize, vocal_postprocess, shift_accompaniment,
    f0_method, index_rate, filter_radius

流程：Demucs 分离人声/伴奏 -> so-vits-svc(`svc infer`) 换声 -> 混音 -> 输出 wav。
模型缺失时 /convert 抛出可读错误，插件会把它显示在聊天里。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _is_ascii_path(path: str) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _resolve_output_dir() -> Path:
    """翻唱成品统一落到「桌面\\翻唱试听」。

    2026-11-06 用户要求：所有翻唱测试的输出都放在桌面同一个文件夹里，
    不要再散落在 rvc-backend/outputs。用环境变量 COVER_OUTPUT_DIR 可覆盖
    （分享包/别的机器上桌面不存在时也不会崩，自动回落到 rvc-backend/outputs）。
    """
    override = os.environ.get("COVER_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / "翻唱A-B试听"
    try:
        desktop.mkdir(parents=True, exist_ok=True)
        return desktop
    except Exception:  # noqa: BLE001 —— 桌面不可写就别硬撑
        return ROOT / "outputs"


OUTPUT_DIR = _resolve_output_dir()
CACHE_DIR = ROOT / "cache"
MODELS_DIR = ROOT / "models"

PY = sys.executable
SVC_EXE = Path(PY).parent / "svc.exe"

# 全局：Demucs 模型常驻，避免每次翻唱重新加载
_SEPARATOR = None

_MODEL_SEP = "\u0000"  # 插件用 MODEL_ALIAS_SEPARATOR，这里只返回纯名字


def log(msg: str) -> None:
    print(f"[rvc] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# --------------------------------------------------------------------------- 模型

def _model_dirs() -> list[Path]:
    if not MODELS_DIR.is_dir():
        return []
    out = []
    for child in sorted(MODELS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if any(child.glob("G_*.pth")):
            out.append(child)
    return out


def _pick_ckpt(model_dir: Path) -> Path:
    preferred = model_dir / "G_latest.pth"
    if preferred.is_file():
        return preferred
    cands = sorted(model_dir.glob("G_*.pth"),
                   key=lambda p: int("".join(ch for ch in p.stem if ch.isdigit()) or 0))
    if not cands:
        raise RuntimeError(f"{model_dir.name} 里没有 G_*.pth")
    return cands[-1]


def list_models() -> list[str]:
    return [p.name for p in _model_dirs()]


def show_model() -> list[str]:
    names = list_models()
    log(f"/show_model -> {len(names)} 个: {names[:5]}")
    return names


def list_voice_profiles() -> list[dict]:
    return [{"profile_id": p.name, "display_name": p.name} for p in _model_dirs()]


def show_msst_models() -> list[str]:
    # 本实现用 demucs 两轨分离，没有可选的 MSST 模型
    return ["demucs-htdemucs"]


# --------------------------------------------------------------------------- 音频

def _demucs_separate(wav: Path) -> tuple[Path, Path]:
    """返回 (vocals, instrumental)。优先 demucs，失败则回退「整轨当人声，静音伴奏」。"""
    global _SEPARATOR
    try:
        import demucs.api  # 延迟导入，缺失时走回退

        if _SEPARATOR is None:
            _SEPARATOR = demucs.api.Separator(model="htdemucs", device="cuda"
                                              if _cuda_ok() else "cpu")
        origin, separated = _SEPARATOR.separate_audio_file(str(wav))
        import numpy as np
        import soundfile as sf  # torchaudio 2.9+ 的 save 要 torchcodec，Windows 没有轮子

        sr = int(getattr(separated, "samplerate", 44100) or 44100)
        stem_dir = CACHE_DIR / f"stems-{int(time.time() * 1000)}"
        stem_dir.mkdir(parents=True, exist_ok=True)
        paths = {}
        for name, tensor in separated.items():
            p = stem_dir / f"{name}.wav"
            data = tensor.detach().cpu().numpy().astype(np.float32)
            sf.write(str(p), data.T, sr)  # (channels, samples) -> (samples, channels)
            paths[name] = p
        vocals = paths.get("vocals")
        inst = paths.get("other") or paths.get("no_vocals")
        if vocals and inst:
            log(f"demucs 分离完成: {vocals.name} / {inst.name}")
            return vocals, inst
        raise RuntimeError(f"demucs 未返回预期分轨: {list(paths)}")
    except Exception as exc:  # noqa: BLE001
        log(f"demucs 不可用，回退单轨模式: {exc}")
        return wav, None


def _cuda_ok() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


_SVC_CACHE: dict[str, object] = {}


def _get_svc(model_dir: Path):
    """按模型目录常驻一个 Svc 实例（载入一次 6s，之后每次推理只要 1~3s）。"""
    key = str(model_dir.resolve())
    cached = _SVC_CACHE.get(key)
    if cached is not None:
        return cached
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from so_vits_svc_fork.inference.core import Svc

    dev = os.environ.get("BACKEND_SVC_DEVICE") or ("cuda" if _cuda_ok() else "cpu")
    svc = Svc(
        net_g_path=str(_pick_ckpt(model_dir)),
        config_path=str(model_dir / "config.json"),
        device=dev,
    )
    _SVC_CACHE[key] = svc
    log(f"svc 模型已常驻: {model_dir.name} device={dev} sr={svc.target_sample}")
    return svc


def _svc_infer(src: Path, model_dir: Path, out: Path, transpose: int, f0_method: str) -> Path:
    cfg = model_dir / "config.json"
    if not cfg.is_file():
        raise RuntimeError(f"{model_dir.name} 缺少 config.json（so-vits-svc 模型配置）")
    # f0 提取器：默认 harvest。11-06 客观量过（40-80s 窗口，与原唱 vocals 逐帧比
    # parselmouth 的 to_pitch）：进程内 harvest 版 |音分| 中位数 5.2（>100c 占 5%），
    # svc.exe CLI 版同样的 harvest 却是 299（>100c 占 91%）—— CLI 路径在这台机器上
    # 会把音高整体推高约 2.9 个半音（听感就是跑调），所以推理一律走进程内。
    # 要试别的提取器：BACKEND_SVC_F0=crepe|dio|pm|harvest。
    f0 = (f0_method or "harvest").lower()
    env_f0 = os.environ.get("BACKEND_SVC_F0", "").strip().lower()
    if env_f0 and env_f0 != "caller":
        f0 = env_f0
    elif f0 in ("parselmouth", "pm"):
        log("f0 提取器: 请求 parselmouth → 使用 harvest（parselmouth 实测整体偏低 ≈238 音分）")
        f0 = "harvest"
    noise = float(os.environ.get("BACKEND_NOISE_SCALE", "0.30"))
    db_thresh = float(os.environ.get("BACKEND_DB_THRESH", "-20"))
    chunk = float(os.environ.get("BACKEND_CHUNK_SECONDS", "0.5"))
    try:
        import librosa
        import numpy as np
        import soundfile as sf

        svc = _get_svc(model_dir)
        audio, _sr = librosa.load(str(src), sr=svc.target_sample)
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        log(f"svc 进程内推理: f0={f0} t={transpose} n={noise} db={db_thresh} ch={chunk} len={len(audio)/svc.target_sample:.1f}s")
        res = svc.infer_silence(
            audio,
            speaker=0,
            transpose=int(transpose),
            auto_predict_f0=False,
            noise_scale=noise,
            f0_method=f0,
            db_thresh=db_thresh,
            pad_seconds=0.5,
            chunk_seconds=chunk,
            max_chunk_seconds=40,
        )
        sf.write(str(out), res, svc.target_sample)
        return out
    except Exception as exc:  # noqa: BLE001
        log(f"进程内 svc 失败，回退 svc.exe: {exc}")
    if not SVC_EXE.is_file():
        raise RuntimeError(
            f"缺少 {SVC_EXE}（so-vits-svc-fork 未安装）。"
            "在 rvc-backend 里执行: uv pip install so-vits-svc-fork"
        )
    ckpt = _pick_ckpt(model_dir)
    cmd = [str(SVC_EXE), "infer", str(src), "-m", str(ckpt), "-c", str(cfg),
           "-fm", f0, "-t", str(int(transpose)),
           "-n", str(noise), "-ch", str(int(chunk) or 1), "-db", str(db_thresh),
           "-o", str(out)]
    log("svc " + " ".join(cmd[3:]))
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    if proc.returncode != 0 or not out.is_file():
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"svc infer 失败(rc={proc.returncode}): {tail}")
    return out


def _export_mp3(wav: Path, bitrate: str = "320k") -> Path:
    """成品转成 mp3（微信/手机都能直接听，体积也只有 wav 的 1/20）。

    ffmpeg 用 tools/ffmpeg.exe；转不出来就把 wav 原样交回去，不让一条编码失败
    把整次翻唱判死。
    """
    ff = ROOT / "tools" / "ffmpeg.exe"
    if not ff.is_file():
        try:
            import imageio_ffmpeg
            ff = Path(imageio_ffmpeg.get_ffmpeg_exe())
        except Exception:  # noqa: BLE001
            return wav
    dst = wav.with_suffix(".mp3")
    try:
        proc = subprocess.run(
            [str(ff), "-y", "-v", "error", "-i", str(wav), "-codec:a", "libmp3lame",
             "-b:a", bitrate, "-ar", "44100", str(dst)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        if proc.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
            log(f"mp3 已导出 {dst.name} {dst.stat().st_size // 1024}KB")
            return dst
        log("mp3 导出失败: " + (proc.stderr or "")[-300:])
    except Exception as exc:  # noqa: BLE001
        log(f"mp3 导出异常: {exc}")
    return wav


def _mix(vocals: Path, inst: Path | None, out: Path,
         vocal_vol: float, inst_vol: float) -> Path:
    import numpy as np
    import soundfile as sf

    v, sr = sf.read(str(vocals), always_2d=True, dtype="float32")
    vg = 1.0 if not vocal_vol else float(vocal_vol)
    v = v * vg
    if inst is not None and inst.is_file():
        i, sr2 = sf.read(str(inst), always_2d=True, dtype="float32")
        ig = 1.0 if not inst_vol else float(inst_vol)
        i = i * ig
        n = max(len(v), len(i))
        if len(v) < n:
            v = np.pad(v, ((0, n - len(v)), (0, 0)))
        if len(i) < n:
            i = np.pad(i, ((0, n - len(i)), (0, 0)))
        if i.shape[1] != v.shape[1]:
            i = np.repeat(i[:, :1], v.shape[1], axis=1)
            mix = v + i
    else:
        mix = v
    # 2026-11-06：以前是「超 0.99 就直接除」（硬顶），清唱段会顶到 0 dBFS 听感发破；
    # 改成软膝限幅到 -1 dBFS（tanh 压拐点以上，不切平）。
    try:
        from pitch_align import _limit as _soft_limit

        if mix.ndim == 2:
            chans = [_soft_limit(np.ascontiguousarray(mix[:, c], dtype=np.float32))
                     for c in range(mix.shape[1])]
            mix = np.stack(chans, axis=1)
        else:
            mix = _soft_limit(np.ascontiguousarray(mix, dtype=np.float32))
    except Exception:
        peak = float(np.max(np.abs(mix)) or 1.0)
        if peak > 0.99:
            mix = mix / peak * 0.99
    out.parent.mkdir(parents=True, exist_ok=True)
    # libsndfile 在 Windows 上打不开含中文的路径（Desktop\翻唱A-B试听\… 直接
    # `LibsndfileError: Error opening …: System error.`，2026-11-06 实测），
    # 所以先写到 ASCII 的 cache 目录，再用 Python 的文件 API 搬过去。
    try:
        sf.write(str(out), mix, sr)
    except Exception:
        if _is_ascii_path(str(out)):
            raise
        tmp = CACHE_DIR / f"mix-{_fingerprint(out.name, time.time())}.wav"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sf.write(str(tmp), mix, sr)
        shutil.move(str(tmp), str(out))
    return out


# --------------------------------------------------------------------------- 主流程

def _fingerprint(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def convert(song_name_src: str,
            key_shift: int = 0,
            vocal_vol: float = 0,
            inst_vol: float = 0,
            model_dropdown: str = "",
            reverb_intensity: float = 0,
            delay_intensity: float = 0,
            uvr5_agg: int = 10,
            uvr5_tta: int = 0,
            uvr5_postprocess: bool = False,
            uvr5_window_size: int = 512,
            uvr5_high_end_process: bool = False,
            msst_batch_size: int = 1,
            msst_num_overlap: float = 0.25,
            msst_normalize: bool = True,
            vocal_postprocess: bool = False,
            shift_accompaniment: bool = False,
            f0_method: str = "harvest",
            index_rate: float = 0.75,
            filter_radius: int = 3):
    """返回 (输出文件绝对路径, 是否命中缓存)。"""
    started = time.monotonic()
    src = Path(str(song_name_src)).expanduser()
    if not src.is_file():
        raise ValueError(f"找不到源音频：{src}")

    models = _model_dirs()
    if not models:
        raise ValueError(
            f"{MODELS_DIR} 下没有任何模型（需要 <名字>/G_*.pth + config.json）。"
            "把音色模型放进去后用 /刷新rvc模型 重新读取。"
        )
    picked = None
    for d in models:
        if d.name == str(model_dropdown):
            picked = d
            break
    if picked is None:
        picked = models[0]
        log(f"未匹配到模型 '{model_dropdown}'，回退 {picked.name}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tag = _fingerprint(src.name, src.stat().st_size, picked.name, key_shift, f0_method)
    final_out = OUTPUT_DIR / f"cover-{tag}.wav"
    if final_out.is_file() and final_out.stat().st_size > 1024:
        log(f"缓存命中 {final_out.name}")
        return str(final_out), "true"

    log(f"开始翻唱: {src.name} -> {picked.name} (shift={key_shift}, f0={f0_method})")
    try:
        vocals, inst = _demucs_separate(src)
        # 2026-11-04：按会话 56b24645 的最终版把后处理链接回来
        import postproc

        vdn = vocals
        converted = CACHE_DIR / f"svc-{tag}.wav"
        _svc_infer(vdn, picked, converted, int(key_shift or 0), str(f0_method or "harvest"))
        # 2026-11-06：音准最后一道 —— 用 WORLD 把 SVC 输出的音高直接换成原唱旋律
        # （音色 sp/ap 仍是 SVC 的）。只有真分离出人声轨时才做，单轨回退时原唱人声
        # 里混着伴奏，硬套会把旋律带偏。关掉：BACKEND_PITCH_ALIGN=0
        if inst is not None and os.environ.get("BACKEND_PITCH_ALIGN", "1") != "0":
            try:
                import pitch_align

                aligned = CACHE_DIR / f"al-{tag}.wav"
                rep = pitch_align.align(converted, vdn, aligned)
                log(f"音高对齐: 原唱有声 {rep['voiced_pct_ref']}% / 输出有声 {rep['voiced_pct_out']}%")
                converted = aligned
            except Exception as exc:  # noqa: BLE001
                log(f"音高对齐失败，用原始 SVC 输出: {exc}")
        processed = postproc.run_chain(vdn, converted, CACHE_DIR / f"pp-{tag}.wav", CACHE_DIR)
        if shift_accompaniment and int(key_shift or 0):
            # 伴奏同步变调代价高，这里只在明确要求时提示未实现细节
            log("shift_accompaniment 暂按原调混音")
        _mix(processed, inst, final_out, vocal_vol, inst_vol)
    except Exception:
        log("翻唱失败:\n" + traceback.format_exc())
        raise

    delivered = _export_mp3(final_out)
    log(f"完成 {delivered.name} 用时 {time.monotonic() - started:.1f}s")
    return str(delivered), "false"


def cache_info() -> dict:
    files = list(OUTPUT_DIR.glob("*.wav")) if OUTPUT_DIR.is_dir() else []
    total = sum(f.stat().st_size for f in files)
    return {"files": len(files), "bytes": total,
            "output_dir": str(OUTPUT_DIR), "models": list_models()}


def clear_cache() -> str:
    """只清理中间产物（cache/）。

    成品现在落在桌面「翻唱试听」里，那是给人试听/留档的，不该被一次
    「清缓存」按掉（2026-11-06 改输出目录时一起改的）。
    """
    removed = 0
    for d in (CACHE_DIR,):
        if not d.is_dir():
            continue
        for f in d.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    return f"cleared {removed} files"


def build_app():
    import gradio as gr

    with gr.Blocks(title="rvc-backend") as demo:
        gr.Markdown("# rvc-backend\nDemucs + so-vits-svc 翻唱后端")
        with gr.Row():
            src = gr.Textbox(label="song_name_src（本地路径或 URL）")
            model = gr.Textbox(label="model_dropdown")
        with gr.Row():
            shift = gr.Slider(-24, 24, value=0, step=1, label="key_shift")
            f0 = gr.Dropdown(["crepe", "fcpe", "rmvpe", "harvest", "dio", "pm"],
                             value="harvest", label="f0_method")
        with gr.Row():
            vv = gr.Number(value=0, label="vocal_vol")
            iv = gr.Number(value=0, label="inst_vol")
            rr = gr.Number(value=0, label="reverb_intensity")
            dd = gr.Number(value=0, label="delay_intensity")
        with gr.Row():
            ir = gr.Number(value=0.75, label="index_rate")
            fr = gr.Number(value=3, label="filter_radius")
        with gr.Row():
            u_agg = gr.Number(value=10, label="uvr5_agg")
            u_tta = gr.Number(value=0, label="uvr5_tta")
            u_pp = gr.Checkbox(value=False, label="uvr5_postprocess")
            u_win = gr.Number(value=512, label="uvr5_window_size")
            u_hi = gr.Checkbox(value=False, label="uvr5_high_end_process")
        with gr.Row():
            m_bs = gr.Number(value=1, label="msst_batch_size")
            m_ov = gr.Number(value=0.25, label="msst_num_overlap")
            m_nm = gr.Checkbox(value=True, label="msst_normalize")
        with gr.Row():
            v_pp = gr.Checkbox(value=False, label="vocal_postprocess")
            s_acc = gr.Checkbox(value=False, label="shift_accompaniment")
        run = gr.Button("convert", variant="primary")
        out_path = gr.Textbox(label="out_path")
        cache_flag = gr.Textbox(label="cache")

        run.click(
            fn=convert,
            inputs=[src, shift, vv, iv, model, rr, dd, u_agg, u_tta, u_pp, u_win, u_hi,
                    m_bs, m_ov, m_nm, v_pp, s_acc, f0, ir, fr],
            outputs=[out_path, cache_flag],
            api_name="convert",
        )

        # 只读端点：用 Button.click 注册，插件按 api_name 调用
        b_models = gr.Button("show_model", visible=False)
        t_models = gr.JSON(visible=False)
        b_models.click(fn=show_model, outputs=t_models, api_name="show_model")

        b_cache = gr.Button("cache_info", visible=False)
        t_cache = gr.JSON(visible=False)
        b_cache.click(fn=cache_info, outputs=t_cache, api_name="cache_info")

        b_clear = gr.Button("clear_cache", visible=False)
        t_clear = gr.Textbox(visible=False)
        b_clear.click(fn=clear_cache, outputs=t_clear, api_name="clear_cache")

        b_prof = gr.Button("list_voice_profiles", visible=False)
        t_prof = gr.JSON(visible=False)
        b_prof.click(fn=list_voice_profiles, outputs=t_prof, api_name="list_voice_profiles")

        b_msst = gr.Button("show_msst_models", visible=False)
        t_msst = gr.JSON(visible=False)
        b_msst.click(fn=show_msst_models, outputs=t_msst, api_name="show_msst_models")

    return demo


def main() -> int:
    global MODELS_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3333)
    ap.add_argument("--models", default=str(MODELS_DIR))
    args = ap.parse_args()
    MODELS_DIR = Path(args.models).expanduser().resolve()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "outputs").mkdir(exist_ok=True)
    log(f"models={MODELS_DIR} ({len(list_models())} 个)")
    log(f"python={PY}")
    log(f"svc.exe={'有' if SVC_EXE.is_file() else '缺失(需 uv pip install so-vits-svc-fork)'}")
    app = build_app()
    # Gradio 4/5/6 的 launch() 关键字在不同大版本之间增删过（6.x 去掉了 show_api），
    # 这里按签名过滤，避免因为一个可选参数把整个后端起不来。
    try:
        app = app.queue(default_concurrency_limit=1)
    except TypeError:
        app = app.queue()
    wanted = dict(
        server_name=args.host, server_port=args.port, show_api=True, quiet=False,
        allowed_paths=[str(OUTPUT_DIR), str(CACHE_DIR)],
    )
    accepted = set(inspect.signature(app.launch).parameters)
    kwargs = {k: v for k, v in wanted.items() if k in accepted}
    dropped = sorted(set(wanted) - set(kwargs))
    if dropped:
        log(f"launch() 不支持 {dropped}，已忽略")
    app.launch(**kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
