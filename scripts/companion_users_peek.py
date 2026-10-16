#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打印陪伴面板里每个用户的关键字段（键 / 昵称 / umo / 档案来源），用于排查「临时会话」。"""
from __future__ import annotations

import json

P = (
    r"C:\Users\<YOU>\Documents\<WORKSPACE>"
    r"\default-workspace\wechat-ai-stack\astrbot\data\plugin_data"
    r"\astrbot_plugin_private_companion\companions.json"
)


def main() -> int:
    data = json.load(open(P, encoding="utf-8-sig"))
    users = data.get("users") or {}
    print(f"users: {len(users)}")
    for key, rec in users.items():
        if not isinstance(rec, dict):
            print(f"  {key!r}: {type(rec).__name__}")
            continue
        print(
            f"  key={key!r:<24} nickname={rec.get('nickname')!r:<16} umo={rec.get('umo')!r:<34}"
            f" origin={rec.get('profile_origin')!r} enabled={rec.get('enabled')}"
            f" auto_profile={rec.get('auto_profile_created')} last_seen={rec.get('last_seen')}"
        )
    other = [k for k in data if k not in ("users", "private_user_alias_merge_backups")]
    print(f"\n其他顶层键 {len(other)} 个：{other[:24]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
