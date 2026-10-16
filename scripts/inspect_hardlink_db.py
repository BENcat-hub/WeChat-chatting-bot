#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探查 WeChat 4.x 的 hardlink.db（消息 md5 -> 本地文件 映射）。

用途：判断“用户发来的图片为什么导不出来”——如果消息里的 md5 不在这个库里，
WeFlow 的 hardlinkOnly 解析就找不到文件，API 也就没有 mediaUrl。

用法：
    python inspect_hardlink_db.py [关键字...]
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

DB = Path(os.environ.get("HARDLINK_DB") or (
    r"C:\Users\<YOU>\Documents\xwechat_files"
    r"\wxid_BOTACCOUNT_e398\db_storage\hardlink\hardlink.db"
))

DEFAULT_KEYS = [
    "1be980f455272ad91480c6d5ef8c52ed",  # 消息 231/235 的 md5
    "cd914280a19d0c766e1cb8f0529ac24b",  # 消息 237 的 md5
    "3a484a6cb93292ceca3400a723b8b16f",  # 本地实际存在的 .dat 基名
    "69d35252b77f34239b5b57299d0ccff9",
    "e6562f89bb4f4688683acc719489fd94",
    "6ce16a68cd2394f3e2a2f6262391786a",
]


def main() -> int:
    keys = sys.argv[1:] or DEFAULT_KEYS
    if not DB.exists():
        print(f"找不到 {DB}")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"库: {DB}")
    print(f"表: {tables}")
    for name in tables:
        try:
            n = cur.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except sqlite3.Error as exc:
            n = f"<err {exc}>"
        cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{name}")').fetchall()]
        print(f"  {name}: {n} 行  列={cols}")
        try:
            rows = cur.execute(f'SELECT * FROM "{name}" LIMIT 2').fetchall()
            for r in rows:
                print(f"    样例: {dict(r)}")
        except sqlite3.Error as exc:
            print(f"    样例读取失败: {exc}")

    print("\n=== 关键字命中 ===")
    for key in keys:
        hit_total = 0
        for name in tables:
            cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{name}")').fetchall()]
            text_cols = [c for c in cols]
            if not text_cols:
                continue
            where = " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in text_cols)
            params = [f"%{key}%"] * len(text_cols)
            try:
                rows = cur.execute(
                    f'SELECT * FROM "{name}" WHERE {where} LIMIT 5', params).fetchall()
            except sqlite3.Error:
                continue
            if rows:
                hit_total += len(rows)
                print(f"  {key} -> {name}: {len(rows)} 行")
                for r in rows[:3]:
                    print(f"      {dict(r)}")
        if hit_total == 0:
            print(f"  {key} -> 无命中")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
