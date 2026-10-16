r"""候选 req_key 探测：即梦 401/50400 是「全模型拒绝」还是「仅 4.6/4.0 拒绝」。

对每个候选 req_key 发一次最小 submit，按上游返回分类：
  50200 / not supported  → 该 req_key 名字不被支持（免费，无额度消耗）
  401 / 403 / 50400      → 该模型被拒绝（额度/权限）
  200                    → 可用（会真的提交一张图，消耗一次额度）

用法：astrbot\.venv\Scripts\python.exe scripts\probe_volc_reqkeys.py [--live]
不带 --live 时只打印计划，不真发请求。
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

try:  # Windows 控制台默认 GBK，中文/特殊字符会 UnicodeEncodeError
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from volc_sign import sign_request  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREDS = ROOT / "secrets" / "volcengine_ak_sk.txt"
VERSION = "2022-08-31"

CANDIDATES = [
    "jimeng_seedream46_cvtob",   # 当前 4.6（已知被拒）
    "jimeng_t2i_v40",            # 当前 4.0（已知被拒）
    "jimeng_t2i_v31",            # 3.1
    "jimeng_t2i_v30",            # 3.0
    "jimeng_high_aes_general_v21",  # 2.1 通用
    "jimeng_high_aes_general_v20",  # 2.0 通用
    "jimeng_seedream40_cvtob",   # 4.0 cvtob 变体
]


def _creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in CREDS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def _submit(ak: str, sk: str, req_key: str) -> tuple[int, dict | str]:
    body = {
        "req_key": req_key,
        "prompt": "a small simple icon of a blue whale on white background",
        "width": 1024,
        "height": 1024,
        "force_single": True,
    }
    url, headers, payload = sign_request(ak, sk, action="CVSync2AsyncSubmitTask", version=VERSION, body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as rsp:
            raw = rsp.read().decode("utf-8", "replace")
            status = rsp.status
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"
    try:
        return status, json.loads(raw)
    except Exception:  # noqa: BLE001
        return status, raw


def classify(status: int, data: dict | str) -> str:
    text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data)
    if status == 200:
        return "AVAILABLE（可用）"
    if "not supported" in text:
        return "unknown-key（该 req_key 不支持）"
    if status in (401, 403) or "50400" in text or "Access Denied" in text:
        return "DENIED（支持但被拒：额度/权限）"
    return f"other(http {status})"


def main() -> None:
    live = "--live" in sys.argv
    ak, sk = _creds()
    for req_key in CANDIDATES:
        if not live:
            print(f"[dry-run] 将探测 {req_key}")
            continue
        status, data = _submit(ak, sk, req_key)
        print(f"{req_key:32s} HTTP {status:3d}  {classify(status, data)}")
        if status != 200:
            text = json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else str(data)
            print(f"    {text[:200]}")


if __name__ == "__main__":
    main()
