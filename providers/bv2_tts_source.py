"""知更鸟 Bert-VITS2 本地 TTS 提供商。

把 wechat-ai-stack 里已经跑通的 `bertvits2\\bv2_say.py`（网上公开权重 G_5800，
https://huggingface.co/spaces/Sprt98/Bert-VITS2_HSR_Robin）包装成 AstrBot 的
text_to_speech 提供商，这样 AstrBot 的插件（陪伴插件等）调用
`context.get_using_tts_provider(...).get_audio(text)` 时，拿到的是知更鸟音色。

配置键（provider 条目里）：
  bv2_python      bertvits2 的 python 解释器（默认 <stack>\\bertvits2\\.venv\\Scripts\\python.exe）
  bv2_script      bv2_say.py 路径
  bv2_device      cpu / cuda
  bv2_sdp         SDP 比例（默认 0.2）
  bv2_noise       音色扰动（默认 0.6）
  bv2_noise_w     时长扰动（默认 0.8）
  bv2_length      语速（默认 1.0，越大越慢）
  bv2_dereverb    去混响强度（默认 0.5；0 关闭）
  bv2_sid         说话人（默认 Robin）
  bv2_lang        语种（默认 ZH）
  timeout         单次合成超时秒数（默认 180）
"""

import asyncio
import os
import subprocess
import time
import uuid

from ..entities import ProviderType
from ..provider import TTSProvider
from ..register import register_provider_adapter

STACK = os.environ.get(
    "WECHAT_AI_STACK",
    r"C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace"
    r"\wechat-ai-stack",
)
DEFAULT_PY = os.path.join(STACK, "bertvits2", ".venv", "Scripts", "python.exe")
DEFAULT_SCRIPT = os.path.join(STACK, "bertvits2", "bv2_say.py")


def _out_dir() -> str:
    """产物必须落在 AstrBot 的 data 目录里。

    陪伴插件在 `tts_enhancement.py:6198` 会用
    `Path(audio_path).resolve().is_relative_to(Path(get_astrbot_data_path()))`
    校验语音路径，落在外面就报「TTS强化拒绝不安全语音路径」并丢弃，所以这里和
    官方 mimo 提供商一样走 astrbot 的 data/temp。
    """
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        base = os.path.join(str(get_astrbot_data_path()), "temp", "tts_bv2")
    except Exception:
        base = os.path.join(STACK, "data", "temp", "tts_bv2")
    os.makedirs(base, exist_ok=True)
    return base



@register_provider_adapter(
    "bv2_tts_api",
    "知更鸟 Bert-VITS2（本地）",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderBv2TTSAPI(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        cfg = provider_config or {}
        self.python = str(cfg.get("bv2_python") or DEFAULT_PY)
        self.script = str(cfg.get("bv2_script") or DEFAULT_SCRIPT)
        self.device = str(cfg.get("bv2_device") or "cpu")
        self.sdp = float(cfg.get("bv2_sdp", 0.2))
        self.noise = float(cfg.get("bv2_noise", 0.6))
        self.noise_w = float(cfg.get("bv2_noise_w", 0.8))
        self.length = float(cfg.get("bv2_length", 1.0))
        self.dereverb = float(cfg.get("bv2_dereverb", 0.35))
        self.max_chars = int(cfg.get("bv2_max_chars", 60) or 60)
        self.clause_split = bool(cfg.get("bv2_clause_split", False))
        self.sid = str(cfg.get("bv2_sid") or "Robin")
        self.lang = str(cfg.get("bv2_lang") or "ZH")
        self.timeout = int(cfg.get("timeout", 180) or 180)
        self.set_model(str(cfg.get("model") or "G_5800"))

    def _run(self, text: str, out_path: str) -> dict:
        cmd = [
            self.python, "-X", "utf8", "-W", "ignore", self.script,
            "--text", text,
            "--out", out_path,
            "--sid", self.sid,
            "--lang", self.lang,
            "--device", self.device,
            "--sdp", str(self.sdp),
            "--noise", str(self.noise),
            "--noise-w", str(self.noise_w),
            "--length", str(self.length),
            "--dereverb", str(self.dereverb),
            "--max-chars", str(self.max_chars),
        ]
        if not self.clause_split:
            cmd.append("--no-clause-split")
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout,
            cwd=STACK,
        )
        payload = ""
        for line in (proc.stdout or "").splitlines():
            if line.strip().startswith("{"):
                payload = line.strip()
        if not payload:
            raise RuntimeError(
                "bv2_tts 合成失败: "
                + ((proc.stderr or proc.stdout or "")[-500:])
            )
        import json

        info = json.loads(payload)
        if not info.get("ok") or not os.path.isfile(out_path):
            raise RuntimeError(f"bv2_tts 合成失败: {payload[:400]}")
        return info

    async def get_audio(self, text: str) -> str:
        spoken = (text or "").strip()
        if not spoken:
            raise RuntimeError("bv2_tts: 空文本")
        out_dir = _out_dir()
        out_path = os.path.join(
            out_dir,
            f"bv2-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.wav",
        )
        # 模型加载约 5s，同一时刻只跑一个子进程，避免 CPU/内存叠加
        async with _SYNTH_LOCK:
            await asyncio.to_thread(self._run, spoken, out_path)
        return out_path


_SYNTH_LOCK = asyncio.Lock()
