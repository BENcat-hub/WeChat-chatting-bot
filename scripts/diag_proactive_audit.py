# -*- coding: utf-8 -*-
"""打印 companions.json 的主动消息审计日志全文（谁、发到哪个 umo、什么话题、什么文本、状态）。

只读。用法：akasha-bridge\\.venv\\Scripts\\python.exe scripts\\diag_proactive_audit.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPANIONS = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json")
CANON = {
    "1000000001": "用户A", "1000000006": "用户C", "1000000003": "用户B",
    "1000000013": "用户D", "1000000015": "群508", "1966047993": "用户E",
}
LEGACY = {
    "1000000002": "用户A(旧)", "1000000007": "用户C(旧)", "1000000008": "用户C(旧)",
    "1000000009": "用户C(旧)", "1000000010": "用户C(旧)", "1000000005": "用户B(旧)",
    "1000000004": "用户B(旧)", "1000000011": "用户D(旧)",
    "1000000016": "群(旧)", "1000000017": "群(旧)",
}


def name(uid) -> str:
    s = str(uid)
    return CANON.get(s) or LEGACY.get(s) or s


with open(COMPANIONS, encoding="utf-8-sig") as fh:
    d = json.load(fh)

print("### proactive_audit_log")
for i, e in enumerate(d.get("proactive_audit_log") or []):
    ts = e.get("ts") or e.get("created_ts") or 0
    when = datetime.fromtimestamp(float(ts) + 8 * 3600).strftime("%m-%d %H:%M:%S") if ts else "?"
    print(f"\n[{i}] {when}  user_id={e.get('user_id')} ({name(e.get('user_id'))})  status={e.get('status')}")
    for k in ("reason", "kind", "umo", "target", "session_id", "action", "topic", "text_preview", "text", "topic_source"):
        if k in e:
            print(f"     {k} = {str(e[k])[:160]}")

print("\n### 每次主动的发送目标（按用户）")
for uid, rec in (d.get("users") or {}).items():
    route = rec.get("private_delivery_routes") or {}
    print(f"\n-- {uid} ({name(uid)}) 路由 {len(route)} 条 --")
    for k, v in list(route.items())[:4]:
        print(f"     {k} -> umo={str(v.get('umo'))[:60]} session_id={v.get('session_id')} last={str(v.get('last_sent_at') or v.get('updated_at'))[:20]}")
    for key in ("last_proactive_delivery_umo", "umo", "last_inbound_umo", "preferred_delivery_umo", "planned_proactive_umo"):
        if key in rec:
            print(f"     {key} = {str(rec[key])[:70]}")
