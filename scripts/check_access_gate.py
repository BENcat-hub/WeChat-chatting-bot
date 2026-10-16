"""离线自测：冷却闸门的「放行探测」与「成功后立即解除」。不需要启动服务。

覆盖：
1. 未封禁时 allow_gate_probe() 不拦截（闸门本身不 active）；
2. note_access_denied() 后第一次 allow_gate_probe() 放行、紧接着第二次拒绝；
3. clear_access_denied() 后 active=False、且写回 last_probe=0；
4. is_access_denied_detail() 的 5 例分类不回归。
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import jimeng_image_server as srv  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


srv.clear_access_denied()
check("clear 后不再是 active", srv.access_denied_snapshot()["active"] is False)
check("未封禁时探测逻辑放行", srv.allow_gate_probe() is True)

srv.clear_access_denied()
srv.note_access_denied("HTTP 401 code 50400 Access Denied")
snap = srv.access_denied_snapshot()
check("note 后 active 且剩余时间 > 0", snap["active"] and snap["seconds_left"] > 0)
check("冷却内第一次真打上游（放行）", srv.allow_gate_probe() is True)
check("冷却内紧接着第二次被拦", srv.allow_gate_probe() is False)
check("count 累计为 1", srv.access_denied_snapshot()["count"] == 1)

srv.clear_access_denied("test recovered")
snap = srv.access_denied_snapshot()
check("成功后立刻解除冷却", snap["active"] is False and snap["count"] == 0)
check("解除后重置探测节流", srv._access_denied_state.get("last_probe") == 0.0)

# is_access_denied_detail 分类回归
cases = [
    ("http 401 {\"code\":50400,\"message\":\"Access Denied\"}", True),
    ("http 400 invalid param: prompt", False),
    ("urllib.error.URLError: [WinError 10060]", False),
    ("http 502 upstream gateway", False),
    ("http 403 Api Forbidden", True),
]
for text, expected in cases:
    got = srv.is_access_denied_detail(text)
    check(f"分类 {text[:38]!r} -> {expected}", got is expected)

print()
if failures:
    print(f"{len(failures)} 项失败：{failures}")
    raise SystemExit(1)
print("全部通过")
