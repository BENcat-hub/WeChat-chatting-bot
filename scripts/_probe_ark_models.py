"""临时探针 2：判断火山方舟 token 的账号权限，并找出可用的 Seedream 模型 ID。

用法：python scripts/_probe_ark_models.py
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
ARK = "https://ark.cn-beijing.volces.com/api/v3"
MODELS = (
    # 文档中确认存在的 Seedream 系列真实 Model ID
    "doubao-seedream-4-0-250828",
    "doubao-seedream-4-5-251128",
    "doubao-seedream-5-0-260128",
    "doubao-seedream-5-0-lite-260128",
    "doubao-seedream-5-0-flash-260915",
    "doubao-seedream-5-0-pro-260628",
    # 文本模型（用于判断账号是否完全没有权限）
    "doubao-seed-1-6-250615",
)


def call(name: str, url: str, *, method: str = "POST", body: dict | None = None,
         token: str = TOKEN) -> None:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"[{name}] HTTP {resp.status}: {resp.read(400).decode('utf-8', 'replace')}")
    except urllib.error.HTTPError as exc:
        print(f"[{name}] HTTP {exc.code}: {exc.read(400).decode('utf-8', 'replace')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] ERR {type(exc).__name__}: {str(exc)[:180]}")


def main() -> int:
    for model in MODELS:
        call(f"img/{model}", f"{ARK}/images/generations",
             body={"model": model, "prompt": "一只橘猫坐在窗台上", "size": "1024x1024",
                   "response_format": "url"})
    # models 列表：真实 key vs 伪造 key 的差异（判断 key 是否被认证）
    call("models/real-key", f"{ARK}/models", method="GET")
    call("models/bogus-key", f"{ARK}/models", method="GET", token="sk-bogus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
