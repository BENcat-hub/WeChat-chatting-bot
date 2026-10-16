# -*- coding: utf-8 -*-
"""归档体检：① companions.json 全量（不只 users）里的关键词落在哪；② memory_companion 里还有多少旧 id 会话。

只读。用法：akasha-bridge\\.venv\\Scripts\\python.exe scripts\\diag_archive_gaps.py [关键词]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = sys.argv[1] if len(sys.argv) > 1 else "体育"
COMPANIONS = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json")
MEM_DB = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db")
ID_MAP = os.path.join(W, "scripts", "session-id-map.json")


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, obj


def main() -> None:
    id_map = json.load(open(ID_MAP, encoding="utf-8")) if os.path.exists(ID_MAP) else {}
    legacy = set(id_map.keys())

    with open(COMPANIONS, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    print(f"### companions.json 顶层键：{list(data.keys())}")
    for top, val in data.items():
        if top in ("users", "groups"):
            continue
        hits = [(p, v) for p, v in walk(val, top) if isinstance(v, str) and KEY in v]
        if hits:
            print(f"\n--- 顶层「{top}」命中 {len(hits)} 处 ---")
            for p, v in hits[:10]:
                print(f"  {p[:120]} = {v[:160]}")
    for section in ("users", "groups"):
        for k, rec in (data.get(section) or {}).items():
            legacy_hits = [(p, v) for p, v in walk(rec, f"{section}.{k}") if isinstance(v, str) and any(lg in v for lg in legacy)]
            if legacy_hits:
                print(f"\n--- {section}[{k}] 内仍引用旧 id 的字段：{len(legacy_hits)} 处 ---")
                for p, v in legacy_hits[:8]:
                    print(f"  {p[:120]} = {v[:120]}")

    if not os.path.exists(MEM_DB):
        return
    con = sqlite3.connect(f"file:{MEM_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    print("\n### memory_companion.db：按 session_id 统计")
    tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'")]
    counter: Counter = Counter()
    for table in tables:
        cols = [r[1] for r in cur.execute(f'pragma table_info("{table}")')]
        if "session_id" not in cols:
            continue
        for (sid, n) in cur.execute(f'select session_id, count(*) from "{table}" group by session_id'):
            counter[str(sid)] += n
    for sid, n in counter.most_common():
        tail = sid.rsplit(":", 1)[-1]
        tag = "旧id" if tail in legacy else "  "
        print(f"  [{tag}] {sid}  ×{n}")
    con.close()


if __name__ == "__main__":
    main()
