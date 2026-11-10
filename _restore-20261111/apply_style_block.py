# -*- coding: utf-8 -*-
"""把「像真人手打聊天」的风格块写进人格配置（面板 API，避免启动回写覆盖）。

用法：
  python _restore-20261111/apply_style_block.py                # 用 style-block-v1.txt，预览
  python _restore-20261111/apply_style_block.py --apply
  python _restore-20261111/apply_style_block.py --file _restore-20261111/style-block-v2.txt --apply
  python _restore-20261111/apply_style_block.py --restore       # 还原到改动前的原始文案
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
ORIGINAL = (
    "每次回复至多三句话；简单的回答尽可能保持在 1~2 句，并尽可能保持口语化与简洁化。"
    "回复长度和语气应该与当前对话节奏相匹配，必须使用简体中文进行回复，话语符合社交媒体交流习惯。"
    "当你需要解决复杂问题或进行详细说明时，可以无视这一要求。"
)

cfg = json.load(io.open(CFG, encoding="utf-8-sig"))
SECRET = cfg["dashboard"]["jwt_secret"]


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
    ap.add_argument("--file", default=os.path.join(ROOT, "_restore-20261111", "style-block-v1.txt"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--segments", type=int, default=4, help="最多几条气泡")
    ap.add_argument("--min-chars", type=int, default=2, help="允许的最短气泡字符数")
    args = ap.parse_args()

    if args.restore:
        text = ORIGINAL
    else:
        text = io.open(args.file, encoding="utf-8").read().strip()

    settings = {"reply_style_prompt": text}
    if not args.restore:
        settings.update({
            "segmented_proactive_max_segments": args.segments,
            "segmented_proactive_min_segment_chars": args.min_chars,
        })

    print(f"style block: {len(text)} 字")
    print(text[:160].replace("\n", " / ") + " …")
    if not args.apply:
        print("\n[dry-run] 加 --apply 才写")
        return 0
    code, body = call("/api/plug/astrbot_plugin_private_companion/page/settings/update", {"settings": settings})
    print("update:", code, body[:160])

    code, body = call("/api/plug/astrbot_plugin_private_companion/page/overview", None, "GET")
    if code == 200:
        try:
            data = json.loads(body).get("data", {}).get("settings", {})
        except Exception:  # noqa: BLE001
            data = {}
        got = str(data.get("reply_style_prompt") or "")
        print("read  :", "OK" if got.startswith(text.strip()[:20]) else "MISMATCH")
        for key in ("segmented_proactive_max_segments", "segmented_proactive_min_segment_chars"):
            print(f"   {key} = {data.get(key)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
