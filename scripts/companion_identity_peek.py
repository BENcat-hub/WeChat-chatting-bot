#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打印陪伴面板用户记录的身份戳字段（排查「临时会话」= identity 不匹配）。"""
from __future__ import annotations

import json

P = (
    r"C:\Users\<YOU>\Documents\<WORKSPACE>"
    r"\default-workspace\wechat-ai-stack\astrbot\data\plugin_data"
    r"\astrbot_plugin_private_companion\companions.json"
)

FIELDS = (
    "nickname",
    "umo",
    "last_inbound_umo",
    "bound_delivery_umo",
    "preferred_delivery_umo",
    "delivery_umo",
    "identity_subject_id",
    "identity_platform_kind",
    "identity_adapter_instance_id",
    "identity_bot_id",
    "profile_origin",
    "auto_profile_created",
    "last_seen",
)


def main() -> int:
    data = json.load(open(P, encoding="utf-8-sig"))
    users = data.get("users") or {}
    for key, rec in users.items():
        if not isinstance(rec, dict):
            continue
        print(f"== {key}")
        for field in FIELDS:
            if field in rec:
                value = rec[field]
                if isinstance(value, (dict, list)):
                    value = f"<{type(value).__name__} len={len(value)}>"
                print(f"     {field} = {value!r}")
        extra = [k for k in rec if k.startswith("identity_") and k not in FIELDS]
        if extra:
            print(f"     (其他 identity_*: {extra})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
