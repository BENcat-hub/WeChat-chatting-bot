# -*- coding: utf-8 -*-
"""把「历史 id → 当前 id」的别名写进插件运行配置（面板 API，避免被启动回写覆盖）。

现状：`private_user_aliases` 里已有 `1007787221=2119307100`（陈奥琦走旧 id，
插件据此把新 id 归并到旧 id）。P0-B 合并后「不曾为世界整除」的档案落在
1031792701，需要把它的旧 id 1905794693 也声明成别名，避免旧 id 再从别的
入口冒出来变成第二个用户。
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

ALIASES = {
    "1905794693": "1031792701",  # 不曾为世界整除：旧 hash id -> 当前 sha256 id
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
            return resp.status, resp.read().decode()[:400]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:400]


def main() -> int:
    print("current:", call("/api/plug/astrbot_plugin_private_companion/page/settings/read", {})[:1])
    text = "\n".join(f"{k}={v}" for k, v in ALIASES.items())
    # 保留既有 1007787221=2119307100，再追加新的
    merged = "1007787221=2119307100\n" + text
    print("write  :", call(
        "/api/plug/astrbot_plugin_private_companion/page/settings/update",
        {"settings": {"private_user_aliases": merged}},
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
