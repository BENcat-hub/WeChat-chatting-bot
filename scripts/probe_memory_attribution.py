# -*- coding: utf-8 -*-
"""只读排查 memory_companion 记忆的"主语归属"。

用途：确认某条事实（例如"嗓子不舒服"）是以谁为主语被存下来的，
用于定位"把对方是主语的话套到自己身上"这类串味问题。

用法：
    python probe_memory_attribution.py --like 嗓
    python probe_memory_attribution.py --like 喉咙 --like 感冒 --limit 30
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(
    r"C:\Users\<YOU>\Documents\<WORKSPACE>"
    r"\wechat-ai-stack\astrbot\data\plugin_data\astrbot_plugin_memory_companion\memory_companion.db"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--like", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    keywords = args.like or ["嗓", "喉咙", "感冒", "不舒服"]
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    where = " or ".join("content like ?" for _ in keywords)
    params = [f"%{kw}%" for kw in keywords]
    rows = cur.execute(
        f"""
        select id, memory_type, subject_kind, subject_id, subject_name, subject_role,
               object_kind, object_id, object_name, object_role,
               scope, content, created_at, injection_count, reality_level, lifecycle
        from memories
        where {where}
        order by created_at desc
        limit ?
        """,
        (*params, args.limit),
    ).fetchall()

    print(f"关键词: {keywords}  命中: {len(rows)}")
    for r in rows:
        content = (r["content"] or "").replace("\n", " ")
        print(
            f"#{r['id']} type={r['memory_type']} "
            f"subj={r['subject_kind']}/{r['subject_id']}/{r['subject_name']}/{r['subject_role']} "
            f"obj={r['object_kind']}/{r['object_id']}/{r['object_name']}/{r['object_role']} "
            f"inj={r['injection_count']} life={r['lifecycle']} reality={r['reality_level']} at={r['created_at']}"
        )
        print(f"    {content[:260]}")

    print()
    print("=== 最近 10 条记忆（看主语分布）===")
    for r in cur.execute(
        """
        select id, memory_type, subject_kind, subject_id, subject_name, object_kind, object_id,
               object_name, content, created_at
        from memories order by created_at desc limit 10
        """
    ):
        content = (r["content"] or "").replace("\n", " ")
        print(
            f"#{r['id']} {r['memory_type']} subj={r['subject_kind']}/{r['subject_name']} "
            f"obj={r['object_kind']}/{r['object_name']} at={r['created_at']}"
        )
        print(f"    {content[:180]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
