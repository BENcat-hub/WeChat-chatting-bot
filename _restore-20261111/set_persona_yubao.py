# -*- coding: utf-8 -*-
"""把「default」人格换成鱼宝，让机器人有真正的角色底子。

现状：`personas` 表里有两条 —— `persona_id='1'` 是用户早前写的鱼宝（含群聊串子与脏话设定），
`persona_id='default'` 是 AstrBot 出厂那句 "You are a helpful and friendly assistant."。
所有微信会话的 `conversations.persona_id` 都是 NULL，于是插件与主动引擎拿到的是**出厂人格**，
这也是「说话像 AI」的底子问题（提示词只能补表面）。

做法：把 `default` 这条的 system_prompt 换成 1:1 陪伴版鱼宝 —— 保留身份、傲娇+软、爱吃白饭、
游戏/番/数码/股票的兴趣与口头习惯，去掉串子与脏话（那套适合群聊，不适合私聊真人）。
`persona_id='1'` 保持原样，随时可在面板切回。
"""
from __future__ import annotations

import io
import os
import shutil
import sqlite3
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "astrbot", "data", "data_v4.db")
PROMPT_FILE = os.path.join(ROOT, "_restore-20261111", "persona-yubao.txt")


def main() -> int:
    text = io.open(PROMPT_FILE, encoding="utf-8").read().strip()
    if len(text) < 200:
        print("!! persona-yubao.txt 太短")
        return 2
    if any(x in text for x in ("操你", "妈的")):
        print("!! persona 文本里有脏话，拒绝写入")
        return 2

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = DB + f".bak-persona-{ts}"
    shutil.copy2(DB, bak)
    print("backup:", os.path.basename(bak))

    c = sqlite3.connect(DB)
    try:
        old = c.execute("select system_prompt from personas where persona_id='default'").fetchone()
        print("old default persona:", (old[0][:60] + "…") if old else "(none)")
        c.execute(
            "update personas set system_prompt=?, updated_at=? where persona_id='default'",
            (text, time.strftime("%Y-%m-%d %H:%M:%S.000000")),
        )
        c.commit()
        new = c.execute("select length(system_prompt) from personas where persona_id='default'").fetchone()[0]
        print(f"done: default persona now {new} chars")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
