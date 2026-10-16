#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查 AstrBot 里其他可能承载「会话」的表，定位用户看到的「临时对话」。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile

SRC = r"<REPO_ROOT>\astrbot\data\data_v4.db"


def snap() -> str:
    tmp = tempfile.mkdtemp(prefix="dbsnap-")
    for suffix in ("", "-wal", "-shm"):
        src = SRC + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "data_v4.db" + suffix))
    return os.path.join(tmp, "data_v4.db")


def show(con: sqlite3.Connection, table: str, limit: int = 12, cols: str = "*") -> None:
    try:
        rows = list(con.execute(f"SELECT {cols} FROM {table} LIMIT {limit}"))
    except sqlite3.Error as exc:
        print(f"-- {table}: {exc}")
        return
    print(f"-- {table}（{len(rows)} 行样例）")
    for row in rows:
        text = json.dumps(row, ensure_ascii=False, default=str)
        print("   " + text[:220])


def main() -> int:
    con = sqlite3.connect(snap())
    con.row_factory = sqlite3.Row
    for table in ("platform_sessions", "platform_message_history", "webchat_threads", "chatui_projects",
                  "session_project_relations", "personas", "command_configs"):
        show(con, table, 6)
    print("\n== 全库搜「临时」字符串 ==")
    for (name, sql) in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        except sqlite3.Error:
            continue
        text_cols = [c for c in cols if c not in ("content", "data", "value")]
        for col in text_cols:
            try:
                hits = list(con.execute(f"SELECT {col} FROM {name} WHERE CAST({col} AS TEXT) LIKE '%临时%' LIMIT 5"))
            except sqlite3.Error:
                continue
            for h in hits:
                print(f"   {name}.{col}: {str(h[0])[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
