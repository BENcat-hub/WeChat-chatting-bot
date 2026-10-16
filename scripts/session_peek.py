#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 data_v4.db(+WAL) 复制出来只读，打印当前的会话清单（含 WAL 里的最新状态）。"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile

SRC = r"<REPO_ROOT>\astrbot\data\data_v4.db"


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="dbsnap-")
    for suffix in ("", "-wal", "-shm"):
        src = SRC + suffix
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(tmp, "data_v4.db" + suffix))
    con = sqlite3.connect(os.path.join(tmp, "data_v4.db"))
    con.row_factory = sqlite3.Row
    print("== conversations（含 WAL） ==")
    for r in con.execute(
        "SELECT rowid AS rid, user_id, conversation_id, created_at, updated_at, content"
        " FROM conversations ORDER BY user_id, created_at"
    ):
        try:
            msgs = json.loads(r["content"] or "[]")
            n = len(msgs)
            last = ""
            if msgs:
                c = msgs[-1].get("content")
                if isinstance(c, list):
                    c = " ".join(str(x.get("text", x)) for x in c)
                last = str(c)[:46].replace("\n", " ")
        except Exception:
            n, last = -1, ""
        print(f"  {r['user_id']:<38} rid={r['rid']:<3} {n:>3}条 upd={str(r['updated_at'])[:19]} | {last}")
    print()
    print("tables:", [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type='table'")])
    print("db:", SRC)
    print("snapshot:", tmp)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
