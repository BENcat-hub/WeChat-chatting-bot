"""探测即梦 4.6 是否接受官方文档里的 `scale` 参数（以及 50500 Internal Error 的来源）。

用法：python scripts/probe_jimeng_scale.py
逐个变体串行提交并轮询到结束（上游有并发上限，不能并行）。
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import REQ_KEY_JIMENG_46, sign_request  # noqa: E402
import urllib.request  # noqa: E402
import urllib.error  # noqa: E402

REF = REPO / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_private_companion" / "photo_reference_images" / "persona_yubao_reference.png"
PROMPT = ("Create a single coherent image showing a young anime girl with long blue gradient hair "
          "standing in a bright living room, waving at the camera, clean cel-shaded illustration, "
          "natural proportions. Avoid chibi, oversized head, photorealistic skin.")


def creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in (REPO / "secrets" / "volcengine_ak_sk.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def call(ak: str, sk: str, action: str, body: dict) -> dict:
    url, headers, payload = sign_request(ak, sk, action=action, version="2022-08-31", body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return {"http_error": exc.code, "body": exc.read().decode("utf-8", "replace")[:400]}


def run(ak: str, sk: str, label: str, extra: dict) -> None:
    blob = base64.b64encode(REF.read_bytes()).decode("ascii")
    body = {
        "req_key": REQ_KEY_JIMENG_46,
        "return_url": True,
        "force_single": True,
        "prompt": PROMPT,
        "width": 1024,
        "height": 1024,
        "binary_data_base64": [blob],
        **extra,
    }
    print(f"\n--- {label} extra={extra}")
    sub = call(ak, sk, "CVSync2AsyncSubmitTask", body)
    print("submit:", json.dumps(sub, ensure_ascii=False)[:300])
    task_id = str((sub.get("data") or {}).get("task_id") or "")
    if not task_id:
        return
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        res = call(ak, sk, "CVSync2AsyncGetResult", {
            "req_key": REQ_KEY_JIMENG_46, "task_id": task_id,
            "req_json": json.dumps({"return_url": True}),
        })
        data = res.get("data") or {}
        status = str(data.get("status") or "")
        if data.get("image_urls") or status in ("done", "failed", "expired", "not_found"):
            print("result:", json.dumps(res, ensure_ascii=False)[:400])
            return
        if res.get("code") not in (10000, None):
            print("poll error:", json.dumps(res, ensure_ascii=False)[:300])
            return


def main() -> int:
    ak, sk = creds()
    run(ak, sk, "带 scale=0.7（文档参数）", {"scale": 0.7})
    run(ak, sk, "带 scale=0.5（文档默认值）", {"scale": 0.5})
    run(ak, sk, "不带 scale", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
