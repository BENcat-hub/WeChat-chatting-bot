"""即梦上游封禁恢复看门狗（无人值守用）。

背景：2026-10-16 02:20 起火山 CVSync2Async 接口对所有请求返回 HTTP 401 / code 50400
（Access Denied），本地服务 `scripts/jimeng_image_server.py` 因此进入了 300s 冷却窗口。
本脚本周期性地观察冷却状态，把状态变化追加写入 `logs/jimeng-out/eval/ban-watch.jsonl`，
一旦服务端记录到一次成功的上游调用就判定恢复并用退出码 0 结束。

默认**只读**：恢复判定用 `/v1/status` 的 `last_upstream_success_ts`（服务端每次拿到上游 2xx
都会更新），不再自己发出图请求——本地闸门每 90s 已经放行一个真请求，重复探测只会多烧额度。
需要旧行为（自己发真实请求确认）时加 `--probe`。

用法（长驻）：
    python scripts/jimeng_ban_watch.py            # 默认每 120s 只读检查，最长 4 小时
    python scripts/jimeng_ban_watch.py --every 60 --max-hours 6
    python scripts/jimeng_ban_watch.py --probe    # 冷却清除后自己发一次真实出图请求

只读（除非显式 --probe）；不修改任何配置。
"""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

REPO = pathlib.Path(__file__).resolve().parent.parent
OUT = REPO / "logs" / "jimeng-out" / "eval" / "ban-watch.jsonl"
SERVER = "http://127.0.0.1:8790"
PROBE_PROMPT = "a simple blue whale swimming in a calm sea, soft morning light"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def append(row: dict) -> None:
    row = {"ts": now_text(), **row}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def http_json(url: str, *, body: dict | None = None, timeout: float = 180.0) -> tuple[int, dict | str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except Exception as exc:  # 网络层错误：本地服务没起来等
        return 0, f"{type(exc).__name__}: {exc}"


def status() -> tuple[bool, float, str, str]:
    """返回 (是否仍在冷却, 剩余秒数, 原始 detail, 最近一次上游成功时间戳)。"""
    code, payload = http_json(f"{SERVER}/v1/status", timeout=15.0)
    if code != 200 or not isinstance(payload, dict):
        return False, 0.0, f"status endpoint code={code} payload={str(payload)[:200]}", ""
    info = payload.get("access_denied") or {}
    return (
        bool(info.get("active")),
        float(info.get("seconds_left") or 0.0),
        str(info.get("detail") or ""),
        str(payload.get("last_upstream_success_ts") or ""),
    )


def probe_once() -> dict:
    """发一次真实文生图请求，确认上游是否恢复。"""
    code, payload = http_json(
        f"{SERVER}/v1/images/generations",
        body={"model": "jimeng-4.6", "prompt": PROBE_PROMPT, "size": "1024x1024", "n": 1, "response_format": "b64_json"},
        timeout=300.0,
    )
    if code == 200 and isinstance(payload, dict):
        data = (payload.get("data") or [{}])[0]
        has_image = bool(data.get("b64_json") or data.get("url"))
        if has_image:
            raw = data.get("b64_json")
            size_bytes = len(base64.b64decode(raw)) if raw else -1
            target = OUT.parent / f"ban-watch-recovered-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
            if raw:
                target.write_bytes(base64.b64decode(raw))
            return {"event": "recovered", "http": code, "size_bytes": size_bytes, "saved": str(target)}
        return {"event": "http200_without_image", "http": code, "preview": json.dumps(payload, ensure_ascii=False)[:300]}
    preview = payload.get("error") if isinstance(payload, dict) else str(payload)
    return {"event": "still_failing", "http": code, "preview": json.dumps(preview, ensure_ascii=False)[:400]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--every", type=float, default=120.0, help="检查间隔秒数（默认 120）")
    parser.add_argument("--max-hours", type=float, default=4.0, help="最长运行小时数（默认 4）")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="恢复判定改用「自己发一次真实出图请求」（旧行为）。默认只读 /v1/status 的 "
             "last_upstream_success_ts，避免与本地闸门的 90s 放行探测重复烧额度。",
    )
    args = parser.parse_args()

    started = time.time()
    deadline = started + max(60.0, args.max_hours * 3600.0)
    append({"event": "watch_start", "every": args.every, "max_hours": args.max_hours, "probe": args.probe})
    last_active: bool | None = None
    _, _, _, baseline_success = status()
    append({"event": "watch_baseline", "last_upstream_success_ts": baseline_success})
    while time.time() < deadline:
        active, left, detail, success_ts = status()
        if active != last_active:
            append({"event": "cooldown_active" if active else "cooldown_cleared", "active": active, "seconds_left": round(left, 1), "detail": detail[:200]})
            last_active = active
        if not active:
            if not args.probe:
                # 只读判定：服务端记录的「最近一次上游 2xx」比观察窗口的基线新，就是真的恢复了。
                if success_ts and success_ts != baseline_success:
                    append({"event": "recovered", "via": "status.last_upstream_success_ts",
                            "last_upstream_success_ts": success_ts})
                    append({"event": "watch_done", "reason": "upstream recovered (observed via server status)"})
                    return 0
                continue
            # 冷却已清除：上游没有再被判定为拒绝，发一次真实请求确认。
            row = probe_once()
            append(row)
            if row.get("event") == "recovered":
                append({"event": "watch_done", "reason": "upstream recovered"})
                return 0
            if "access" in json.dumps(row, ensure_ascii=False).lower():
                last_active = True  # 又被拒，等下一轮
        time.sleep(max(15.0, args.every))
    append({"event": "watch_timeout", "hours": args.max_hours})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
