# -*- coding: utf-8 -*-
"""P1-A：给主动消息上「温和档」节奏限制（面板 API 写入，避免被启动镜像回写覆盖）。

现状：default_proactive_daily_limit=0（不限）、proactive_unanswered_pause_after=0（无人回应也不停）、
proactive_intensity_preset='live'。结果是一天十几条主动、多数没被回应，看起来像自说自话。

温和档 A：
  default_proactive_daily_limit = 12    每人每天最多 12 条主动
  proactive_unanswered_pause_after = 6  连续 6 条没人回应就暂停，等对方先开口
  proactive_intensity_preset = 'calm'   强度档位下移到 calm（若插件不认识该值会退回 live，属安全失败）
  proactive_unanswered_slowdown_start / max_interval_multiplier 保持原值（1 / 2.2）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time
import urllib.error
import urllib.request

CFG = "astrbot/data/cmd_config.json"
cfg = json.load(io.open(CFG, encoding="utf-8-sig"))
SECRET = cfg["dashboard"]["jwt_secret"]

SETTINGS = {
    "default_proactive_daily_limit": 12,
    "proactive_unanswered_pause_after": 6,
    "proactive_intensity_preset": "balanced",
}


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def token(exp: int = 3600) -> str:
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64(json.dumps({"username": "astrbot", "exp": int(time.time()) + exp}, separators=(",", ":")).encode())
    msg = header + b"." + payload
    sig = b64(hmac.new(SECRET.encode(), msg, hashlib.sha256).digest())
    return (msg + b"." + sig).decode()


def call(path: str, body: dict) -> tuple[int, str]:
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request("http://127.0.0.1:6185" + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()[:300]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


if __name__ == "__main__":
    code, body = call("/api/plug/astrbot_plugin_private_companion/page/settings/update", {"settings": SETTINGS})
    print("update:", code, body[:200])
    code, body = call("/api/plug/astrbot_plugin_private_companion/page/settings/read", {})
    if code == 200:
        try:
            data = json.loads(body) if body.strip().startswith("{") else {}
        except Exception:  # noqa: BLE001
            data = {}
        print("read  :", code)
