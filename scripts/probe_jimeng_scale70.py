"""单次探测：即梦 4.6 + 参考图 + scale=0.7 是否能正常出图（打印每次轮询状态）。

用法：python scripts/probe_jimeng_scale70.py
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import urllib.request  # noqa: E402

from volc_sign import REQ_KEY_JIMENG_46, sign_request  # noqa: E402

REF = (REPO / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_private_companion"
       / "photo_reference_images" / "persona_yubao_reference.png")


def creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in (REPO / "secrets" / "volcengine_ak_sk.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def call(ak: str, sk: str, action: str, body: dict) -> dict:
    url, headers, payload = sign_request(ak, sk, action=action, version="2022-08-31", body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    ak, sk = creds()
    blob = base64.b64encode(REF.read_bytes()).decode("ascii")
    body = {
        "req_key": REQ_KEY_JIMENG_46,
        "return_url": True,
        "force_single": True,
        "prompt": ("A young anime girl with long blue hair standing in a bright living room, "
                   "clean cel-shaded illustration. Avoid chibi, oversized head."),
        "width": 1024,
        "height": 1024,
        "binary_data_base64": [blob],
        "scale": 0.7,
    }
    sub = call(ak, sk, "CVSync2AsyncSubmitTask", body)
    print("submit:", json.dumps(sub, ensure_ascii=False)[:220], flush=True)
    tid = str((sub.get("data") or {}).get("task_id") or "")
    if not tid:
        return 1
    start = time.time()
    while time.time() - start < 200:
        time.sleep(6)
        res = call(ak, sk, "CVSync2AsyncGetResult", {
            "req_key": REQ_KEY_JIMENG_46, "task_id": tid,
            "req_json": json.dumps({"return_url": True}),
        })
        data = res.get("data") or {}
        print("t+%ds code=%s status=%s urls=%d msg=%s" % (
            int(time.time() - start), res.get("code"), data.get("status"),
            len(data.get("image_urls") or []), str(res.get("message"))[:90]), flush=True)
        if data.get("image_urls") or str(data.get("status")) in ("done", "failed", "expired", "not_found"):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
