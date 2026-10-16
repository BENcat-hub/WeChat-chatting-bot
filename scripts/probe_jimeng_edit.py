"""排查即梦（火山智能视觉）参考图/改图接口：逐个变体提交任务，原样打印上游响应。

用法：
    python scripts/probe_jimeng_edit.py --image <path> [--prompt "..."]

只做 SubmitTask，不轮询，目的是看清上游 400 到底在抱怨哪个字段。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import sign_request  # noqa: E402

SECRETS = REPO / "secrets" / "volcengine_ak_sk.txt"


def load_creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in SECRETS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def submit(ak: str, sk: str, body: dict) -> tuple[int, str]:
    url, headers, payload = sign_request(ak, sk, action="CVSync2AsyncSubmitTask",
                                         version="2022-08-31", body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", default="keep the character, change the background to a sunny beach")
    ap.add_argument("--size", default="2048x2048")
    args = ap.parse_args()

    image_bytes = Path(args.image).read_bytes()
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url_png = "data:image/png;base64," + b64
    data_url_jpeg = "data:image/jpeg;base64," + b64
    width, _, height = args.size.partition("x")

    base = {
        "prompt": args.prompt,
        "return_url": True,
        "force_single": True,
        "width": int(width),
        "height": int(height),
    }

    variants: list[tuple[str, dict]] = [
        ("seedream46_cvtob + image_urls[data:image/png]",
         {"req_key": "jimeng_seedream46_cvtob", **base, "image_urls": [data_url_png]}),
        ("seedream46_cvtob + image_urls[data:image/jpeg]",
         {"req_key": "jimeng_seedream46_cvtob", **base, "image_urls": [data_url_jpeg]}),
        ("seedream46_cvtob + binary_data_base64",
         {"req_key": "jimeng_seedream46_cvtob", **base, "binary_data_base64": [b64]}),
        ("seedream46_cvtob + image_urls + binary_data_base64",
         {"req_key": "jimeng_seedream46_cvtob", **base,
          "image_urls": [data_url_jpeg], "binary_data_base64": [b64]}),
        ("seedream46_cvtob + no image (control)",
         {"req_key": "jimeng_seedream46_cvtob", **base}),
        ("jimeng_i2i_v30 + binary_data_base64",
         {"req_key": "jimeng_i2i_v30", **base, "binary_data_base64": [b64]}),
        ("jimeng_i2i_v30 + image_urls[data:image/jpeg]",
         {"req_key": "jimeng_i2i_v30", **base, "image_urls": [data_url_jpeg]}),
        ("jimeng_seededit_v2 + binary_data_base64",
         {"req_key": "jimeng_seededit_v2", **base, "binary_data_base64": [b64]}),
    ]

    ak, sk = load_creds()
    print(f"image={args.image} bytes={len(image_bytes)}")
    for label, body in variants:
        status, text = submit(ak, sk, body)
        try:
            parsed = json.loads(text)
            text = json.dumps(parsed, ensure_ascii=False)[:600]
        except Exception:  # noqa: BLE001
            text = text[:600]
        print(f"\n=== {label}\nHTTP {status}: {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
