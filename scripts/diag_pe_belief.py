# -*- coding: utf-8 -*-
"""排查「体育课」这个错误归属：谁记住了、记在谁名下、从哪来。

只读脚本，不改任何文件。
用法：akasha-bridge\\.venv\\Scripts\\python.exe scripts\\diag_pe_belief.py [关键词]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYWORD = sys.argv[1] if len(sys.argv) > 1 else "体育"

COMPANIONS = os.path.join(
    W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json"
)
MEM_DB = os.path.join(
    W, "astrbot", "data", "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db"
)

NAME_BY_ID = {
    "1000000001": "用户A",
    "1000000006": "用户C",
    "1000000003": "用户B",
    "1000000013": "用户D",
    "1000000015": "群 508",
    "1966047993": "用户E(自己)",
}


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def companions_hits() -> None:
    with open(COMPANIONS, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    print(f"=== companions.json（关键词「{KEYWORD}」）===")
    for uid, rec in (data.get("users") or {}).items():
        hits = [(k, v) for k, v in walk(rec) if isinstance(v, str) and KEYWORD in v]
        if not hits:
            continue
        who = NAME_BY_ID.get(uid, "?")
        print(f"\n--- 用户 {uid}（{who}）命中 {len(hits)} 处 ---")
        for path, val in hits[:20]:
            short = path.replace("root.", "")
            print(f"  {short[:110]} = {val[:130]}")
    groups = data.get("groups") or {}
    for gid, rec in groups.items():
        hits = [(k, v) for k, v in walk(rec) if isinstance(v, str) and KEYWORD in v]
        if hits:
            print(f"\n--- 群 {gid} 命中 {len(hits)} 处（前 5）---")
            for path, val in hits[:5]:
                print(f"  {path[:110]} = {val[:130]}")


def memory_hits() -> None:
    if not os.path.exists(MEM_DB):
        print("\n（没有 memory_companion.db）")
        return
    con = sqlite3.connect(f"file:{MEM_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    print(f"\n=== memory_companion.db（关键词「{KEYWORD}」）===")
    tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
    for table in tables:
        cols = [r[1] for r in cur.execute(f'pragma table_info("{table}")')]
        text_cols = [
            c
            for c in cols
            if c.lower()
            in ("content", "text", "summary", "value", "name", "description", "label",
                "evidence", "fact", "body", "title", "note")
        ]
        for col in text_cols:
            try:
                rows = cur.execute(
                    f'select * from "{table}" where "{col}" like ? limit 40', (f"%{KEYWORD}%",)
                ).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                keep = {}
                for k in row.keys():
                    if k in ("id", "memory_id", "session_id", "session", "subject_id", "user_id",
                             "person_id", "created_at", "updated_at", "title", "node_id", "source",
                             "identity_key", col):
                        v = row[k]
                        keep[k] = (str(v)[:150] if v is not None else None)
                print(f"[{table}.{col}] {json.dumps(keep, ensure_ascii=False)}")
    con.close()


if __name__ == "__main__":
    print(f"# 关键词：{KEYWORD}")
    companions_hits()
    memory_hits()
