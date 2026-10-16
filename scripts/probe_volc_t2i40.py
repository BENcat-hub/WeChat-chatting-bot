r"""确认 jimeng_t2i_v40（即梦 4.0）是否可完整出图：submit → poll GetResult → 下载图片。

用途：4.6（`jimeng_seedream46_cvtob`）被 50400 拒绝时，验证 4.0 是否仍可用作回退通道。

用法：
    astrbot\.venv\Scripts\python.exe scripts\probe_volc_t2i40.py [--out logs\jimeng-out\eval\probe40.png]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from volc_sign import sign_request  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREDS = ROOT / "secrets" / "volcengine_ak_sk.txt"
VERSION = "2022-08-31"


def creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in CREDS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def call(ak: str, sk: str, action: str, body: dict) -> tuple[int, dict]:
    url, headers, payload = sign_request(ak, sk, action=action, version=VERSION, body=body)
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as rsp:
            return rsp.status, json.loads(rsp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, {"raw": raw[:500]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--req-key", default="jimeng_t2i_v40")
    ap.add_argument("--out", default="logs/jimeng-out/eval/probe40.png")
    ap.add_argument("--prompt", default=(
        "Create a single coherent image showing an anime-style teen girl with long blue gradient hair, "
        "whale-fin ears, clean line art, bright colors, upper body, simple bright bedroom background."
    ))
    args = ap.parse_args()

    ak, sk = creds()
    submit_body = {
        "req_key": args.req_key,
        "prompt": args.prompt,
        "width": 1024,
        "height": 1024,
        "force_single": True,
    }
    status, data = call(ak, sk, "CVSync2AsyncSubmitTask", submit_body)
    print(f"submit HTTP {status}  {json.dumps(data, ensure_ascii=False)[:300]}")
    task_id = (data.get("data") or {}).get("task_id") if isinstance(data, dict) else None
    if not task_id:
        return

    deadline = time.time() + 180
    while time.time() < deadline:
        time.sleep(5)
        status, data = call(ak, sk, "CVSync2AsyncGetResult",
                            {"req_key": args.req_key, "task_id": task_id})
        body = data.get("data") or {} if isinstance(data, dict) else {}
        state = body.get("status") if isinstance(body, dict) else None
        print(f"poll HTTP {status}  status={state}")
        if state and str(state).lower() in {"done", "success"}:
            urls = body.get("image_urls") or ([body.get("image_url")] if body.get("image_url") else [])
            print("image_urls:", urls)
            if urls:
                out = ROOT / args.out
                out.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(urls[0], timeout=120) as rsp:
                    out.write_bytes(rsp.read())
                print(f"saved {out}  ({out.stat().st_size/1024:.0f} KB)")
            return
        if state and str(state).lower() in {"failed", "not_found", "expired"}:
            print("terminal:", json.dumps(data, ensure_ascii=False)[:500])
            return
    print("timeout")


if __name__ == "__main__":
    main()
