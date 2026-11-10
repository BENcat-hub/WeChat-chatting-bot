# -*- coding: utf-8 -*-
"""把风格块 + 分条参数一起写进插件（面板 API）。

v2 的额外动作：
* 私聊分段参数之前是独立的（private_max_segments=3 / private_min_segment_chars=8），
  只改全局对私聊无效 —— 这里一起改。
* 分条正则加上逗号：真人很少用逗号串长句，逗号处本来就该停顿/换条。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "astrbot", "data", "cmd_config.json")
SECRET = json.load(io.open(CFG, encoding="utf-8-sig"))["dashboard"]["jwt_secret"]

REGEX_COMMA = r".*?[。？！~…，,]+|.+$"


def b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def token(exp: int = 3600) -> str:
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    p = b64(json.dumps({"username": "astrbot", "exp": int(time.time()) + exp}, separators=(",", ":")).encode())
    msg = h + b"." + p
    return (msg + b"." + b64(hmac.new(SECRET.encode(), msg, hashlib.sha256).digest())).decode()


def call(path: str, body: dict | None = None, method: str = "POST") -> tuple[int, str]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:6185" + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token())
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=os.path.join(ROOT, "_restore-20261111", "style-block-v2.txt"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-regex", action="store_true", help="不改分条正则（只调提示词与条数）")
    args = ap.parse_args()

    text = io.open(args.file, encoding="utf-8").read().strip()
    settings = {
        "reply_style_prompt": text,
        "segmented_proactive_max_segments": 4,
        "segmented_proactive_min_segment_chars": 2,
        "segmented_proactive_private_max_segments": 4,
        "segmented_proactive_private_min_segment_chars": 2,
    }
    if not args.no_regex:
        settings["segmented_proactive_regex"] = REGEX_COMMA

    print(f"style block: {len(text)} 字; settings: {list(settings)}")
    if not args.apply:
        print("[dry-run] 加 --apply 才写")
        return 0
    code, body = call("/api/plug/astrbot_plugin_private_companion/page/settings/update", {"settings": settings})
    print("update:", code, body[:120])
    code, body = call("/api/plug/astrbot_plugin_private_companion/page/overview", None, "GET")
    if code == 200:
        s = json.loads(body).get("data", {}).get("settings", {})
        for key in ("segmented_proactive_private_max_segments",
                    "segmented_proactive_private_min_segment_chars",
                    "segmented_proactive_regex"):
            print(f"   {key} = {s.get(key)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
