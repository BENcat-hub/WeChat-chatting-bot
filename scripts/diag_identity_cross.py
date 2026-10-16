"""只读诊断：在 memory_companion.db 里找「用户A ↔ 用户B」交叉/错位的行。

判定口径：一行记录里同时出现「用户A系 id」和「用户B系 id」→ 说明这行
被两个不同的人引用过（归档/改写留下的错位）。

id 归属（已由 data_v4.db 会话内容里的 User ID/Nickname 与微信侧 wxid 双向核对）：
    用户A        : 1000000001（稳定）, 1000000002（迁移前）
    用户B    : 1000000003（稳定）, 1000000004 / 1000000005（迁移前）
    用户C      : 1000000006（稳定）, 1000000007 / 1000000008 / 1000000009（迁移前）

用法：
    python scripts/diag_identity_cross.py            # 全部表
    python scripts/diag_identity_cross.py memories   # 单表
"""
from __future__ import annotations

import os
import sqlite3
import sys

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(
    STACK, "astrbot", "data", "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db"
)

OWNER = {
    "用户A": {"1000000001", "1000000002"},
    "用户B": {"1000000003", "1000000004", "1000000005"},
    "用户C": {"1000000006", "1000000007", "1000000008", "1000000009"},
}
ID2NAME = {i: n for n, ids in OWNER.items() for i in ids}


def owners_of(*values: object) -> set[str]:
    found = set()
    for value in values:
        text = str(value or "")
        if not text:
            continue
        last = text.rsplit(":", 1)[-1]  # umo 尾段
        for token in (text, last):
            name = ID2NAME.get(token)
            if name:
                found.add(name)
    return found


def report(con: sqlite3.Connection, table: str, id_cols: list[str], text_cols: list[str]) -> int:
    cols = [r[1] for r in con.execute(f"pragma table_info({table})")]
    if not cols:
        print(f"  (无 {table} 表)")
        return 0
    picked = ["id"] + [c for c in id_cols if c in cols] + [c for c in text_cols if c in cols]
    sql = f"select {', '.join(picked)} from {table}"
    hits = 0
    for row in con.execute(sql):
        rec = dict(zip(picked, row))
        names = owners_of(*[rec.get(c) for c in id_cols if c in cols])
        if len(names) > 1:
            hits += 1
            preview = ""
            for c in text_cols:
                if rec.get(c):
                    preview = str(rec[c]).replace("\n", " ")[:80]
                    break
            print(f"  !! {table} {rec.get('id')}")
            for c in id_cols:
                if c in cols and rec.get(c) not in (None, "", 0):
                    print(f"       {c} = {rec[c]}")
            if preview:
                print(f"       text: {preview}")
    return hits


def show_counts(con: sqlite3.Connection) -> None:
    print("=== 各表规模 ===")
    for (name,) in con.execute("select name from sqlite_master where type='table' order by name"):
        try:
            n = con.execute(f"select count(*) from {name}").fetchone()[0]
        except sqlite3.Error:
            continue
        if n:
            print(f"   {name:28} {n}")


CHECKS = {
    "memories": (["subject_id", "object_id", "session_id"], ["content"]),
    "timeline": (["subject_id", "object_id", "session_id"], ["content"]),
    "knowledge_nodes": (["session_id"], ["label"]),
    "knowledge_edges": (["session_id"], ["evidence"]),
    "relationship_edges": (["session_id"], ["evidence"]),
    "portrait_facts": (["session_id", "person_id"], ["fact_text", "statement"]),
    "portrait_evidence": (["session_id", "person_id"], ["content", "quote"]),
    "injection_logs": (["session_id"], ["query"]),
    "emotion_events": (["session_id"], ["summary"]),
    "identities": (["id", "entity_id"], ["display_name"]),
}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    try:
        show_counts(con)
        wanted = sys.argv[1:] or list(CHECKS)
        total = 0
        for table in wanted:
            if table not in CHECKS:
                print(f"未知表: {table}（可选: {', '.join(CHECKS)}）")
                continue
            id_cols, text_cols = CHECKS[table]
            print(f"\n=== {table} ===")
            hits = report(con, table, id_cols, text_cols)
            total += hits
            if hits == 0:
                print("   （无跨人引用）")
        print(f"\n合计跨人行数: {total}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
