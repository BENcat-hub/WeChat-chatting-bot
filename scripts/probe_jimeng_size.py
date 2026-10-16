"""探测即梦上游支持的宽高：直接 SubmitTask，打印原始响应。

用来确认 1280x720（文档里的最小尺寸）能否直接用，以及服务端 parse_size 的
1M 像素下限是否过严。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import REQ_KEY_JIMENG_40, REQ_KEY_JIMENG_46, sign_request  # noqa: E402

SECRETS = REPO / "secrets" / "volcengine_ak_sk.txt"
PROMPT = "a calm blue whale swimming in a bright coral reef, natural light, photographic"


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


def main() -> None:
    ak, sk = load_creds()
    sizes = [(1280, 720), (1280, 768), (1366, 768), (1440, 810), (1024, 1024)]
    for req_key in (REQ_KEY_JIMENG_46, REQ_KEY_JIMENG_40):
        for width, height in sizes:
            body = {
                "req_key": req_key,
                "prompt": PROMPT,
                "return_url": True,
                "force_single": True,
                "width": width,
                "height": height,
            }
            status, raw = submit(ak, sk, body)
            try:
                parsed = json.loads(raw)
                code = (parsed.get("code"), (parsed.get("message") or "")[:80])
            except Exception:  # noqa: BLE001
                code = ("?", raw[:120])
            print(f"{req_key:24s} {width}x{height:<6d} HTTP {status} code={code[0]} msg={code[1]}")


if __name__ == "__main__":
    main()
