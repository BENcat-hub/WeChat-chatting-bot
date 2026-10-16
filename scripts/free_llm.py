"""Minimal client for the local free-model gateway (our-free-model plugin).

Usage:
  python scripts/free_llm.py --model mimo-v2.6-flash-free --prompt "hi"
  python scripts/free_llm.py --model nemotron-3.5-super-vl-free --prompt "评分" --image a.png --image b.png
  python scripts/free_llm.py --list

The gateway speaks the OpenAI chat-completions protocol and needs the forward
key from %APPDATA%\\dsh-desktop\\harness\\our-free-model\\settings.json.
It is meant for auxiliary text/vision work so the main agent does not spend its
own context on bulk prompting or image scoring.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = os.environ.get("FREE_MODEL_GATEWAY", "http://127.0.0.1:18899/v1")
SETTINGS = Path(os.environ.get("APPDATA", "")) / "dsh-desktop" / "harness" / "our-free-model" / "settings.json"


def load_key() -> str:
    key = os.environ.get("FREE_MODEL_KEY", "").strip()
    if key:
        return key
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return str(data.get("forwardKey") or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"cannot read forward key from {SETTINGS}: {exc}")


def image_part(path: str) -> dict:
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    raw = base64.b64encode(p.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{raw}"}}


def call(model: str, prompt: str, images: list[str], *, temperature: float, max_tokens: int, timeout: int = 300) -> str:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img in images:
        content.append(image_part(img))
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content if images else prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        GATEWAY + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + load_key()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise SystemExit(f"gateway HTTP {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"gateway error: {exc}")
    if os.environ.get("FREE_MODEL_RAW"):
        print(json.dumps(payload, ensure_ascii=False)[:4000])
    try:
        message = payload["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        return json.dumps(payload, ensure_ascii=False)[:800]
    text = message.get("content")
    if not text:
        # some gateways put the text in reasoning_content / reasoning
        for key in ("reasoning_content", "reasoning", "text"):
            if message.get(key):
                text = message[key]
                break
    return text if text else json.dumps(payload, ensure_ascii=False)[:1200]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mimo-v2.6-flash-free")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--max-tokens", type=int, default=1200)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        req = urllib.request.Request(GATEWAY + "/models", headers={"Authorization": "Bearer " + load_key()})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("data", []):
            print(item.get("id"))
        return 0

    if not args.prompt:
        raise SystemExit("--prompt is required")
    print(call(args.model, args.prompt, args.image, temperature=args.temperature, max_tokens=args.max_tokens))
    return 0


if __name__ == "__main__":
    sys.exit(main())
