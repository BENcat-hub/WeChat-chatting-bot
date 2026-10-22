"""原神 16 角色 Bert-VITS2（114000 步）本地 TTS 提供商。

包装 `bertvits2\\bv2_v1_say.py`（权重 https://huggingface.co/guetLzy/Bert-vits2，
G_114000，v1.1.0 架构，16 说话人），作为 AstrBot 的 text_to_speech 提供商，
供陪伴插件 `context.get_using_tts_provider(...).get_audio(text)` 使用。

配置键（provider 条目里）：
  gl_python      bertvits2 的 python 解释器（默认 <stack>\\bertvits2\\.venv\\Scripts\\python.exe）
  gl_script      bv2_v1_say.py 路径
  gl_device      cpu / cuda
  gl_sdp         SDP 比例（默认 0.2）
  gl_noise       音色扰动（默认 0.6）
  gl_noise_w     时长扰动（默认 0.8）
  gl_length      语速（默认 1.0，越大越慢）
  gl_sid         说话人（默认 纳西妲；可选 钟离/申鹤/魈/胡桃/香菱/行秋/神里绫人/心海/
                 烟绯/宵宫/丽莎/可莉/提纳里/神里绫华/绮良良）
  timeout        单次合成超时秒数（默认 120）
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
DEFAULT_SCRIPT = os.path.join(STACK, "bertvits2", "bv2_v1_say.py")


def _out_dir() -> str:
    """产物必须落在 AstrBot 的 data 目录里（插件会校验语音路径安全）。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        base = os.path.join(str(get_astrbot_data_path()), "temp", "tts_gl")
    except Exception:
        base = os.path.join(STACK, "data", "temp", "tts_gl")
    os.makedirs(base, exist_ok=True)
    return base


@register_provider_adapter(
    "gl_tts_api",
    "原神 16 角色 Bert-VITS2（本地，114000 步）",
    provider_type=ProviderType.TEXT_TO_SPEECH,
)
class ProviderGlTTSAPI(TTSProvider):
    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        cfg = provider_config or {}
        self.python = str(cfg.get("gl_python") or DEFAULT_PY)
        self.script = str(cfg.get("gl_script") or DEFAULT_SCRIPT)
        self.device = str(cfg.get("gl_device") or "cpu")
        self.sdp = float(cfg.get("gl_sdp", 0.2))
        self.noise = float(cfg.get("gl_noise", 0.6))
        self.noise_w = float(cfg.get("gl_noise_w", 0.8))
        self.length = float(cfg.get("gl_length", 1.0))
        self.sid = str(cfg.get("gl_sid") or "纳西妲")
        self.timeout = int(cfg.get("timeout", 120) or 120)
        self.set_model(str(cfg.get("model") or "G_114000"))

    def _run(self, text: str, out_path: str) -> dict:
        cmd = [
            self.python, "-X", "utf8", "-W", "ignore", self.script,
            "--text", text,
            "--out", out_path,
            "--sid", self.sid,
            "--device", self.device,
            "--sdp", str(self.sdp),
            "--noise", str(self.noise),
            "--noise-w", str(self.noise_w),
            "--length", str(self.length),
        ]
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
                "gl_tts 合成失败: " + ((proc.stderr or proc.stdout or "")[-500:])
            )
        import json

        info = json.loads(payload)
        if not os.path.isfile(out_path) or float(info.get("dur") or 0) <= 0:
            raise RuntimeError(f"gl_tts 合成失败: {payload[:400]}")
        return info

    async def get_audio(self, text: str) -> str:
        spoken = (text or "").strip()
        if not spoken:
            raise RuntimeError("gl_tts: 空文本")
        out_dir = _out_dir()
        out_path = os.path.join(
            out_dir,
            f"gl-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.wav",
        )
        async with _SYNTH_LOCK:
            await asyncio.to_thread(self._run, spoken, out_path)
        return out_path


_SYNTH_LOCK = asyncio.Lock()
