#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探查 memory_companion.db：表结构 + 最近记忆条目（看主语/归属是否串味）。

用法：
  python probe_memory_companion.py <db 路径> [--table 表名] [--like 关键词] [--limit 20]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--table", default="")
    ap.add_argument("--like", default="")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"文件不存在: {args.db}")
        return 1

    uri = f"file:{args.db}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cur.fetchall()]
    print(f"=== 表（{len(tables)}） ===")
    for name in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM '{name}'")
            count = cur.fetchone()[0]
        except sqlite3.Error as exc:
            count = f"err:{exc}"
        print(f"  {name:<44} {count}")

    targets = [args.table] if args.table else tables
    for name in targets:
        print(f"\n=== 表 {name} ===")
        try:
            cur.execute(f"PRAGMA table_info('{name}')")
            cols = [row[1] for row in cur.fetchall()]
            print("  列: " + ", ".join(cols))
            if not cols:
                continue
            order_col = next(
                (c for c in cols if c.lower() in {"updated_at", "created_at", "timestamp", "id"}),
                cols[0],
            )
            sql = f"SELECT * FROM '{name}'"
            params: list[object] = []
            if args.like:
                where = " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in cols)
                sql += f" WHERE {where}"
                params = [f"%{args.like}%"] * len(cols)
            sql += f" ORDER BY {order_col} DESC LIMIT ?"
            params.append(args.limit)
            cur.execute(sql, params)
            rows = cur.fetchall()
            print(f"  最近 {len(rows)} 行（按 {order_col} 倒序）：")
            for row in rows:
                parts = []
                for col in cols:
                    value = row[col]
                    text = "" if value is None else str(value)
                    text = text.replace("\n", " ⏎ ")
                    if len(text) > 300:
                        text = text[:300] + "…"
                    parts.append(f"{col}={text}")
                print("   - " + " | ".join(parts))
        except sqlite3.Error as exc:
            print(f"  读取失败: {exc}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
