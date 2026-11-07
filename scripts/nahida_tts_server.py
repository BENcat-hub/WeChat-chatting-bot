"""纳西妲语音服务：mimo TTS -> so-vits-svc(纳西妲模型) -> OpenAI 兼容 /v1/audio/speech。

用法：
    astrbot/.venv/Scripts/python.exe scripts/nahida_tts_server.py --port 8795

设计要点：
- 与唱歌完全分离：唱歌用 rvc-backend/models/孙燕姿，这里固定读 rvc-backend/models_voice/<名字>。
- 对外伪装成 OpenAI TTS（POST /v1/audio/speech，返回 audio/wav 字节），
  AstrBot 里用 openai_tts_api 类型的 provider 指过来即可，不需要改 AstrBot 源码。
- mimo key/base 读 secrets/mimo_api_key.txt、secrets/mimo_api_base.txt。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

try:  # 只有 rvc-backend/.venv 带 librosa（astrbot venv 没有），缺失时按需报错
    import librosa as _librosa
except Exception:  # pragma: no cover
    _librosa = None

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
RVC = ROOT / "rvc-backend"
FFMPEG = ROOT / "tools" / "ffmpeg.exe"
SVC = RVC / ".venv" / "Scripts" / "svc.exe"
MODELS = RVC / "models_voice"
BERTV2 = ROOT / "bertvits2"
if BERTV2.is_dir() and str(BERTV2) not in sys.path:
    sys.path.insert(0, str(BERTV2))  # engine=bv2 时用（Bert-VITS2 单段式）

DEFAULT_BASE = "https://token-plan-cn.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5-tts"
DEFAULT_VOICE = "mimo_default"
DEFAULT_SPEAKER = "纳西妲"
F0_METHOD = os.environ.get("NAHIDA_F0", "harvest")
NOISE_SCALE = float(os.environ.get("NAHIDA_NOISE_SCALE", "0.4"))


def librosa_load(path: str, sr: int):
    if _librosa is None:
        raise RuntimeError("缺少 librosa：请用 rvc-backend/.venv 的 python 跑本服务")
    return _librosa.load(path, sr=sr)

LOG = ROOT / "logs" / "nahida-tts.log"
_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def read_secret(name: str, default: str = "") -> str:
    path = SECRETS / name
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text or default
    except Exception:
        return default


def mimo_tts(text: str, api_key: str, api_base: str, model: str, voice: str) -> bytes:
    payload = {
        "model": model,
        "messages": [{"role": "assistant", "content": text}],
        "audio": {"format": "wav", "voice": voice},
    }
    req = urllib.request.Request(
        f"{api_base.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    audio = (body.get("choices") or [{}])[0].get("message", {}).get("audio", {}).get("data")
    if not audio:
        raise RuntimeError(f"mimo 未返回音频: {str(body)[:300]}")
    return base64.b64decode(audio)


def find_voice_model(speaker: str) -> tuple[Path, Path, Path | None]:
    model_dir = MODELS / speaker
    pths = sorted(model_dir.glob("G_*.pth"))
    if not pths:
        raise RuntimeError(f"缺少音色模型: {model_dir}/G_*.pth")
    cfg = model_dir / "config.json"
    if not cfg.is_file():
        raise RuntimeError(f"缺少 config.json: {cfg}")
    kmeans = None
    for name in ("kmeans.pt", "kmeans_10000.pt"):
        if (model_dir / name).is_file():
            kmeans = model_dir / name
            break
    return pths[0], cfg, kmeans


_SVC_CACHE: dict[str, object] = {}


def get_svc(speaker: str):
    """常驻 svc 模型：只加载一次，后续请求只做推理（原来每请求起 svc.exe 要 30~60s）。"""
    cached = _SVC_CACHE.get(speaker)
    if cached is not None:
        return cached
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from so_vits_svc_fork.inference.core import Svc

    pth, cfg, _kmeans = find_voice_model(speaker)
    t0 = time.time()
    svc = Svc(net_g_path=str(pth), config_path=str(cfg),
              device=os.environ.get("NAHIDA_DEVICE", "cuda"))
    # 首推理慢（权重预热 / cuDNN autotune），先跑半秒静音把这块成本挪到启动期
    svc.infer(speaker=0, transpose=0,
              audio=np.zeros(int(svc.target_sample * 0.5), dtype=np.float32),
              f0_method=F0_METHOD, noise_scale=NOISE_SCALE)
    log(f"svc model loaded speaker={speaker} device={svc.device} in {time.time() - t0:.2f}s")
    _SVC_CACHE[speaker] = svc
    return svc


def svc_convert(src_wav: Path, out_wav: Path, speaker: str) -> Path:
    # 注意：不给 -k。torch 2.6+ 默认 weights_only=True，kmeans 是 numpy pickle，
    # 会直接抛 WeightsUnpickler error（voice_send.py 里同一处坑）。
    svc = get_svc(speaker)
    audio, _ = librosa_load(str(src_wav), int(svc.target_sample))
    out = svc.infer_silence(
        audio.astype(np.float32),
        speaker=0,
        transpose=0,
        f0_method=F0_METHOD,
        noise_scale=NOISE_SCALE,
        db_thresh=-40,
        pad_seconds=0.5,
        chunk_seconds=0.5,
    )
    sf.write(str(out_wav), out, int(svc.target_sample))
    if not out_wav.is_file():
        raise RuntimeError("svc 没有产出音频")
    return out_wav


def to_44k(src: Path, dst: Path) -> Path:
    proc = subprocess.run(
        [str(FFMPEG), "-y", "-v", "error", "-i", str(src),
         "-ar", "44100", "-ac", "1", str(dst)],
        capture_output=True, text=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr[-300:]}")
    return dst


EDGE_VOICE = os.environ.get("NAHIDA_EDGE_VOICE", "zh-CN-XiaoyiNeural")

_CJK = r"[\u3400-\u4dbf\u4e00-\u9fff]"


def prepare_speech_text(text: str) -> str:
    """把要念的文本整理成「能念出语气」的样子。

    两个坑：
    1. 上层的 TTS 标记（如 `<pc_tts ...>`）会原样进合成，edge 会把标签念成
       「tc tts」之类的字母，听起来像把工具调用读出来了。
    2. 出站用的微信排版会把「，。」全换成空格，念出来就是一串没有停顿、
       没有升降调的平铺句子，所以这里把子句之间的空格还原成逗号。
    """
    s = str(text or "")
    s = re.sub(r"</?[A-Za-z_][\w:.-]*(?:\s[^>]*)?/?>", "", s)
    for junk in ("**", "__", "`", "#", "~~"):
        s = s.replace(junk, "")
    s = s.replace("\r", " ").replace("\n", "，")
    s = re.sub(r"[ \t]+", " ", s)
    # 子句之间补逗号：edge 靠标点决定停顿和语调，纯空格会念成一条直线
    s = re.sub(rf"(?<={_CJK}) (?={_CJK})", "，", s)
    s = re.sub(r"(?<=[，。！？；]) ", "", s)
    s = re.sub(r"[，,]{2,}", "，", s)
    s = re.sub(r"，(?=[。！？])", "", s)
    s = re.sub(r"^[，。！？\s]+", "", s).strip()
    if s and s[-1] not in "。！？…":
        s += "。"
    return s


def edge_tts_wav(text: str, dst: Path) -> Path:
    """本地 edge-tts 合成（亚秒级），用来替代原来 Bert-VITS2 的“秒出”速度。"""
    import asyncio

    import edge_tts

    mp3 = dst.with_suffix(".mp3")

    async def run() -> None:
        comm = edge_tts.Communicate(text, EDGE_VOICE, rate="+0%", volume="+0%")
        await comm.save(str(mp3))

    asyncio.run(run())
    if not mp3.is_file() or mp3.stat().st_size < 1024:
        raise RuntimeError("edge-tts 没有产出音频")
    return to_44k(mp3, dst)


def synthesize(text: str, speaker: str, skip_svc: bool = False) -> bytes:
    """TTS → (可选) svc 音色转换 → wav 字节。

    skip_svc=True 时只出 44.1k 原始 TTS：voice_send.py（微信语音气泡）要自己
    在 rvc-backend 里跑 svc，避免这里转一遍、那边又转一遍把人声磨平。
    """
    engine = os.environ.get("NAHIDA_ENGINE", "edge").strip().lower()
    spoken = prepare_speech_text(text)
    if not spoken:
        raise RuntimeError("没有可念的文本")
    with _lock:
        work = Path(tempfile.mkdtemp(prefix="nahida-", dir=str(RVC / "temp")))
        t0 = time.time()
        if engine == "bv2":
            # 单段式：Bert-VITS2 直接出 44.1k，不再过 svc
            import bv2_say  # noqa: PLC0415
            out = work / "bv2.wav"
            info = bv2_say.gl_say(spoken, str(out), sid=speaker)
            data = out.read_bytes()
            log(f"ok engine=bv2 speaker={speaker} infer={info.get('infer_s')}s "
                f"total={info.get('total_s')}s text={spoken[:40]!r} bytes={len(data)}")
            return data
        if engine == "mimo":
            api_key = read_secret("mimo_api_key.txt") or os.environ.get("MIMO_API_KEY", "")
            api_base = read_secret("mimo_api_base.txt", DEFAULT_BASE)
            if not api_key:
                raise RuntimeError("缺少 secrets/mimo_api_key.txt")
            raw = work / "raw.wav"
            raw.write_bytes(mimo_tts(spoken, api_key, api_base, DEFAULT_MODEL, DEFAULT_VOICE))
            a44 = to_44k(raw, work / "src44.wav")
        else:
            a44 = edge_tts_wav(text, work / "src44.wav")
        t1 = time.time()
        if skip_svc:
            data = a44.read_bytes()
            log(f"ok engine={engine} speaker={speaker} skip_svc=1 tts={t1 - t0:.2f}s "
                f"total={time.time() - t0:.2f}s text={spoken[:40]!r} bytes={len(data)}")
            return data
        out = svc_convert(a44, work / "out.wav", speaker)
        data = out.read_bytes()
        log(f"ok engine={engine} speaker={speaker} tts={t1 - t0:.2f}s "
            f"svc={time.time() - t1:.2f}s total={time.time() - t0:.2f}s "
            f"text={spoken[:40]!r} bytes={len(data)}")
        return data


class Handler(BaseHTTPRequestHandler):
    server_version = "NahidaTTS/1.0"
    speaker = DEFAULT_SPEAKER

    def log_message(self, fmt, *args):  # noqa: A003
        log("http " + fmt % args)

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] in ("/health", "/v1/health", "/"):
            engine = os.environ.get("NAHIDA_ENGINE", "edge").strip().lower()
            pth, cfg, _ = find_voice_model(self.speaker)
            if engine == "bv2":
                w = BERTV2 / "pretrained" / self.speaker / "G_114000.pth"
                self._json(200, {"ok": True, "speaker": self.speaker, "engine": "bv2",
                                 "model": w.name, "config": w.is_file()})
            else:
                self._json(200, {"ok": True, "speaker": self.speaker, "engine": engine,
                                 "model": pth.name, "config": cfg.is_file()})
        elif self.path.split("?")[0].rstrip("/") == "/v1/models":
            self._json(200, {"object": "list", "data": [
                {"id": f"nahida-{self.speaker}", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/v1/audio/speech", "/audio/speech"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            text = (payload.get("input") or payload.get("text") or "").strip()
            if not text:
                self._json(400, {"error": "input required"})
                return
            speaker = payload.get("voice") or self.speaker
            if speaker.startswith("nahida-"):
                speaker = speaker[len("nahida-"):]
            if not (MODELS / speaker).is_dir():
                speaker = self.speaker
            data = synthesize(text, speaker, skip_svc=bool(payload.get("skip_svc")
                                                           or payload.get("raw")))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            log(f"ERR {exc}")
            self._json(500, {"error": str(exc)[:500]})


def preload(speaker: str) -> None:
    """启动即把模型读进显存，别让第一条微信消息等 25 秒。"""
    engine = os.environ.get("NAHIDA_ENGINE", "edge").strip().lower()
    try:
        if engine == "bv2":
            import bv2_say  # noqa: PLC0415

            t0 = time.time()
            bv2_say._load()
            # 预热一次，把 BERT 与 cudnn 的首次开销吃掉
            bv2_say.synthesize("预热", sid=speaker)
            log(f"preload done engine=bv2 speaker={speaker} in {time.time() - t0:.1f}s")
            return
        get_svc(speaker)
        log(f"preload done speaker={speaker}")
    except Exception as exc:  # noqa: BLE001
        log(f"preload failed: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8795)
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER)
    ap.add_argument("--no-preload", action="store_true")
    args = ap.parse_args()
    Handler.speaker = args.speaker
    (RVC / "temp").mkdir(parents=True, exist_ok=True)
    log(f"start http://{args.host}:{args.port} speaker={args.speaker} "
        f"engine={os.environ.get('NAHIDA_ENGINE', 'edge')} "
        f"svc={'有' if SVC.is_file() else '缺失'} ffmpeg={'有' if FFMPEG.is_file() else '缺失'}")
    if not args.no_preload:
        threading.Thread(target=preload, args=(args.speaker,), daemon=True).start()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
