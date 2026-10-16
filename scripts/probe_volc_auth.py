r"""鉴权层诊断：判断即梦 401/50400 是「账号/服务级拒绝」还是「模型额度级拒绝」。

只发**不消耗额度**的探测请求：
  A. CVSync2AsyncSubmitTask + 不存在的 req_key  → 预期「参数错误」说明鉴权与服务已放行
  B. CVSync2AsyncGetResult  + 不存在的 task_id → 预期「任务不存在」
  C. 同 host 上不存在的 Action                 → 预期「Action 不支持」

判读：
  A 返回 50400 Access Denied → 整个 cv 服务对本 AK 关闭（未开通/欠费/被禁用/额度为负）
  A 返回参数类错误           → 鉴权正常，拒绝发生在模型/额度层（控制台看即梦用量与开通状态）

用法：astrbot\.venv\Scripts\python.exe scripts\probe_volc_auth.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from volc_sign import sign_request  # noqa: E402

try:  # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREDS = ROOT / "secrets" / "volcengine_ak_sk.txt"
VERSION = "2022-08-31"


def _creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in CREDS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def _call(ak: str, sk: str, action: str, body: dict) -> tuple[int, str]:
    url, headers, payload = sign_request(ak, sk, action=action, version=VERSION, body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as rsp:
            return rsp.status, rsp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


PROBES = [
    ("A. submit + 不存在的 req_key", "CVSync2AsyncSubmitTask",
     {"req_key": "probe_nonexistent_req_key", "prompt": "probe", "width": 512, "height": 512}),
    ("B. getresult + 不存在的 task_id", "CVSync2AsyncGetResult",
     {"req_key": "probe_nonexistent_req_key", "task_id": "probe-task-id-0000"}),
    ("C. 不存在的 Action", "CVProbeUnknownAction",
     {"req_key": "probe_nonexistent_req_key"}),
]


def main() -> None:
    ak, sk = _creds()
    print(f"AK 前缀: {ak[:6]}…  长度 {len(ak)}   SK 长度 {len(sk)}")
    for label, action, body in PROBES:
        status, text = _call(ak, sk, action, body)
        try:
            data = json.loads(text)
            msg = json.dumps(data, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            msg = text
        print(f"\n=== {label} ===")
        print(f"HTTP {status}")
        print(msg[:700])


if __name__ == "__main__":
    main()
