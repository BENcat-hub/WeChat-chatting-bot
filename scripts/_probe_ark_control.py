"""临时探针 3：用对照 key 判断 404 是否等价于「鉴权通过但无权限」。

用法：python scripts/_probe_ark_control.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TOKEN = (
    "VxCgNvLTE.ChBQYUMzSXkzNDdHZXprVjhLEOqQj_kHGAEqEIz2M0PPwkgQsVitexNfIIs"
    ".pCMvGdbCJre_MTxiSgER-za067Une6DB34c32wkrLwTaEJGA3ed7So5hDKvwfVsU__r_-t_wpWde4pR--OwEzweZ"
)
FLAT_FAKE = "A" * 128
UUID_FAKE = "12345678-1234-1234-1234-123456789012"


def call(name: str, url: str, *, body: dict | None = None, method: str = "POST",
         token: str = TOKEN) -> None:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            print(f"[{name}] HTTP {resp.status}: {resp.read(260).decode('utf-8', 'replace')}")
    except urllib.error.HTTPError as exc:
        print(f"[{name}] HTTP {exc.code}: {exc.read(260).decode('utf-8', 'replace')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] ERR {type(exc).__name__}: {str(exc)[:150]}")


def main() -> int:
    ark_img = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    body = {"model": "doubao-seedream-4-0-250828", "prompt": "x", "size": "1024x1024"}
    for label, tok in (("real", TOKEN), ("flat-fake", FLAT_FAKE), ("uuid-fake", UUID_FAKE)):
        call(f"ark/{label}", ark_img, body=body, token=tok)

    # 第三方聚合网关 AIFusion：文档用 Bearer <token> 调即梦 4.6
    call("aifusion/jimeng46", "https://aifusion.newtouch.com/ai/api/jimeng_seedream46_cvtob/image",
         body={"req_key": "jimeng_seedream46_cvtob", "prompt": "一只橘猫"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
