r"""离线自测：jimeng_image_server 的「模型级回退链」逻辑。

不打上游，只验证候选排序、拒绝标记与恢复清除：
  python -X utf8 scripts\check_req_key_fallback.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jimeng_image_server", ROOT / "scripts" / "jimeng_image_server.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

K46 = mod.REQ_KEY_JIMENG_46
K40 = mod.REQ_KEY_JIMENG_40
failures: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got} want={want}")
    if not ok:
        failures.append(name)


# 1) 没有拒绝记录时，主模型在前
check("candidates(4.6) 初始顺序", mod.candidate_req_keys(K46), [K46, K40])
check("candidates(4.0) 初始顺序", mod.candidate_req_keys(K40), [K40, K46])
check("初始 snapshot 为空", mod.req_key_denied_snapshot(), {})
check("初始未标记拒绝", mod.req_key_denied(K46), False)

# 2) 标记 4.6 被拒 → 4.0 排到最前（fresh 优先），但仍保留一次 4.6 尝试机会
mod.mark_req_key_denied(K46, "http 401 50400 Access Denied")
check("标记后候选顺序", mod.candidate_req_keys(K46), [K40, K46])
check("标记后 req_key_denied", mod.req_key_denied(K46), True)
check("snapshot 含 4.6", K46 in mod.req_key_denied_snapshot(), True)

# 3) 4.6 恢复 → 顺序回到主模型在前
mod.clear_req_key_denied(K46, "test")
check("清除后候选顺序", mod.candidate_req_keys(K46), [K46, K40])
check("清除后 req_key_denied", mod.req_key_denied(K46), False)

# 4) TTL 到期自动解除（避免把暂时的拒绝当成永久；实现里有 1s 下限）
mod.mark_req_key_denied(K46, "ttl test", ttl=1.0)
check("TTL 内仍标记", mod.req_key_denied(K46), True)
time.sleep(1.15)
check("TTL 到期后未标记", mod.req_key_denied(K46), False)
check("TTL 到期 snapshot 为空", mod.req_key_denied_snapshot(), {})

# 5) 两个模型都被拒绝时，候选仍返回两个（调用方据此走冷却闸门）
mod.mark_req_key_denied(K46, "both denied")
mod.mark_req_key_denied(K40, "both denied")
check("双拒绝仍返回候选", mod.candidate_req_keys(K46), [K46, K40])
check("双拒绝 snapshot 两条", len(mod.req_key_denied_snapshot()), 2)
mod.clear_req_key_denied(K46, "cleanup")
mod.clear_req_key_denied(K40, "cleanup")

print()
if failures:
    print(f"=== {len(failures)} 项失败：{failures}")
    sys.exit(1)
print("=== 全部通过 ===")
