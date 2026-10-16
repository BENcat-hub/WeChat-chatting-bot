# -*- coding: utf-8 -*-
"""追踪「体育课」是怎么从用户A串到桃桃的：看 11:55 给桃桃的主动提示词里有什么、以及全局候选池的结构。

只读。用法：akasha-bridge\\.venv\\Scripts\\python.exe scripts\\diag_pool_leak.py
"""
from __future__ import annotations

import json
import os

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIONS = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json")

with open(COMPANIONS, encoding="utf-8-sig") as fh:
    d = json.load(fh)

print("### proactive_candidate_pool 条目结构（前 3 个键 + 是否带归属字段）")
for i, item in enumerate(d.get("proactive_candidate_pool") or []):
    if not isinstance(item, dict):
        continue
    owner = {k: item.get(k) for k in ("user_id", "person_id", "target", "umo", "session_id", "owner", "scope") if k in item}
    print(f"  [{i}] keys={list(item.keys())[:12]}")
    print(f"      归属字段={owner}  topic={str(item.get('topic'))[:60]}")

print("\n### recent_prompt_injections.proactive 里提到「体育 / 用户A」的段落")
inj = (d.get("recent_prompt_injections") or {}).get("proactive") or []
for i, entry in enumerate(inj):
    if not isinstance(entry, dict):
        continue
    blob = json.dumps(entry, ensure_ascii=False)
    if "体育" not in blob:
        continue
    print(f"\n--- proactive[{i}]  目标={str(entry.get('target') or entry.get('umo') or '?')[:60]} ---")
    for line in blob.split("\\n"):
        if "体育" in line or "用户A" in line:
            print("   ", line[:220])
