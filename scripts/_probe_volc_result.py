"""查询即梦任务结果：验证 4.0 / 4.6 两条 req_key 都能真的出图。

    python scripts/_probe_volc_result.py <req_key> <task_id> [<req_key> <task_id> ...]

轮询 CVSync2AsyncGetResult，直到 status 完成或超时，打印结果 JSON（含图片 URL）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import sign_request  # noqa: E402


def call(ak: str, sk: str, body: dict) -> tuple[int, str]:
    url, headers, payload = sign_request(
        ak, sk, action="CVSync2AsyncGetResult", version="2022-08-31", body=body
    )
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(4000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(1000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {str(exc)[:300]}"


def poll(ak: str, sk: str, req_key: str, task_id: str, tries: int = 20, wait: float = 3.0) -> str:
    body = {"req_key": req_key, "task_id": task_id, "req_json": json.dumps({"return_url": True})}
    last = ""
    for i in range(1, tries + 1):
        code, text = call(ak, sk, body)
        last = text
        try:
            data = json.loads(text)
        except Exception:  # noqa: BLE001
            print(f"  [{req_key}] #{i} HTTP {code} 非 JSON：{text[:200]}")
            time.sleep(wait)
            continue
        status = str(data.get("status") or data.get("data", {}).get("status") or "")
        print(f"  [{req_key}] #{i} HTTP {code} status={status} code={data.get('code')}")
        if status in {"done", "not_found", "expired"} or data.get("code") not in (10000, None):
            break
        if "data" in data and data["data"] and data["data"].get("binary_data_base64") is not None:
            break
        time.sleep(wait)
    return last


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2 or len(args) % 2:
        print(__doc__)
        return 2
    lines = [ln.strip() for ln in (REPO / "secrets" / "volcengine_ak_sk.txt").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    ak, sk = lines[0], lines[1]
    for i in range(0, len(args), 2):
        req_key, task_id = args[i], args[i + 1]
        print(f"\n=== {req_key} / {task_id} ===")
        out = poll(ak, sk, req_key, task_id)
        print(out[:3000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
