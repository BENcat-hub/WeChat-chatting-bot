"""临时探针：确认用户给的「即梦」token 属于哪个接口、用哪种鉴权。

用法：python scripts/_probe_jimeng_api.py
只读式探测（不会创建生图任务成功后的副作用可忽略）。
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


def call(name: str, url: str, *, method: str = "POST", body: dict | None = None,
         headers: dict | None = None) -> None:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read(700).decode("utf-8", "replace")
            print(f"[{name}] HTTP {resp.status}: {text}")
    except urllib.error.HTTPError as exc:
        text = exc.read(700).decode("utf-8", "replace")
        print(f"[{name}] HTTP {exc.code}: {text}")
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] ERR {type(exc).__name__}: {str(exc)[:220]}")


def bearer() -> dict:
    return {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}


def main() -> int:
    ark = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
    for model in (
        "doubao-seedream-4-0-250828",
        "doubao-seedream-4-0-250828 ",
        "seedream-4-0",
        "seedream-4.0",
        "doubao-seedream-4-6-260101",
        "doubao-seedream-4-6-251015",
        "doubao-seedream-4-5-251015",
        "seedream-4-6",
        "seedream-4.6",
        "jimeng-4.6",
    ):
        call(
            f"ark/{model!r}",
            ark,
            body={"model": model, "prompt": "一只橘猫坐在窗台上", "size": "1024x1024",
                  "response_format": "url"},
            headers=bearer(),
        )

    # 对照组：伪造 key，用于判断真实 key 是被认证通过还是同样 404
    bogus = {"Authorization": "Bearer sk-bogus-key-for-comparison", "Content-Type": "application/json"}
    call(
        "ark/BOGUS-KEY",
        ark,
        body={"model": "doubao-seedream-4-0-250828", "prompt": "x", "size": "1024x1024"},
        headers=bogus,
    )
    call("ark/list-models", "https://ark.cn-beijing.volces.com/api/v3/models",
         method="GET", headers=bearer())
    call("ark/chat-completions", "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
         body={"model": "doubao-seed-1-6-250615", "messages": [{"role": "user", "content": "hi"}]},
         headers=bearer())

    # 火山视觉（即梦官方接口）走签名鉴权，这里只验证 Bearer 一定被拒
    call(
        "volc-visual/t2i_v40",
        "https://visual.volcengineapi.com?Action=CVSync2AsyncSubmitTask&Version=2022-08-31",
        body={"req_key": "jimeng_t2i_v40", "prompt": "一只橘猫坐在窗台上", "force_single": True},
        headers=bearer(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
