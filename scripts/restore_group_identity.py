#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把群身份行补回稳定 id（一次性修复）。

cleanup_legacy_identities.py 删掉了两条老群身份行（``aiocqhttp:group:1000000016`` /
``...:1000000017``），但当时的库*没有* ``aiocqhttp:group:1000000015`` 这一行，
于是 timeline / relationship_edges 会指向一个不存在的身份。

这里从最近的备份里取出那条老行，改 id / entity_id 为稳定 id 后插回去。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

ROOT = r"<REPO_ROOT>"
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import merge_sessions as ms  # noqa: E402

CANONICAL = "1000000015"
OLD_IDS = ["1000000017", "1000000016"]


def main() -> int:
    live = sqlite3.connect(ms.MEM_DB)
    live.row_factory = sqlite3.Row
    exists = live.execute(
        "SELECT 1 FROM identities WHERE entity_id=?", (CANONICAL,)
    ).fetchone()
    if exists:
        print(f"identities 里已有 group {CANONICAL}，无需处理")
        return 0

    backups = sorted(
        (
            os.path.join(os.path.dirname(ms.MEM_DB), name)
            for name in os.listdir(os.path.dirname(ms.MEM_DB))
            if name.startswith("memory_companion.db.bak-")
        ),
        reverse=True,
    )
    for path in backups:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        for old in OLD_IDS:
            row = con.execute(
                "SELECT * FROM identities WHERE id=? OR entity_id=?",
                (f"aiocqhttp:group:{old}", old),
            ).fetchone()
            if row is None:
                continue
            data = dict(row)
            con.close()
            data["id"] = f"aiocqhttp:group:{CANONICAL}"
            data["entity_id"] = CANONICAL
            cols = list(data)
            live.execute(
                f"INSERT INTO identities ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in data.values()],
            )
            live.commit()
            print(f"已从 {os.path.basename(path)} 恢复群身份 → {data['id']}（display_name={data.get('display_name')!r}）")
            return 0
        con.close()
    print("备份里也没找到老群身份行，跳过")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
