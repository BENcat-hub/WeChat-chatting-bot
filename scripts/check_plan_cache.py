r"""查看最近 daily_plan 调用的真实缓存命中情况。

用法：astrbot\.venv\Scripts\python.exe -X utf8 scripts\check_plan_cache.py [--limit 8]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--task", default="daily_plan")
    args = ap.parse_args()

    with open(DATA, encoding="utf-8") as fh:
        data = json.load(fh)
    usage = data.get("token_usage") or {}
    recent = usage.get("recent") or []
    rows = [r for r in recent if (r.get("task") or "") == args.task]
    if not rows:
        print(f"没有 task={args.task} 的记录")
        return 1

    print(f"最近 {min(len(rows), args.limit)} 次 {args.task}（总计 {len(rows)} 次）")
    print(f"{'时间':<20}{'成功':<6}{'prompt':>9}{'cached':>9}{'命中率':>9}{'completion':>12}{'估算':>6}")
    for r in rows[-args.limit:]:
        prompt = int(r.get("prompt_tokens") or 0)
        cached = int(r.get("cache_read_tokens") or r.get("cached_tokens") or 0)
        ratio = f"{cached / prompt * 100:.0f}%" if prompt else "-"
        ts = str(r.get("time") or r.get("ts") or "")[:19]
        print(f"{ts:<20}{str(bool(r.get('success'))):<6}{prompt:>9}{cached:>9}{ratio:>9}"
              f"{int(r.get('completion_tokens') or 0):>12}{str(bool(r.get('estimated'))):>6}")

    by_task = (usage.get("by_task") or {}).get(args.task) or {}
    prompt = int(by_task.get("prompt_tokens") or 0)
    cached = int(by_task.get("cache_read_tokens") or by_task.get("cached_tokens") or 0)
    if prompt:
        print(f"\n累计：prompt {prompt} / cached {cached} = {cached / prompt * 100:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())

