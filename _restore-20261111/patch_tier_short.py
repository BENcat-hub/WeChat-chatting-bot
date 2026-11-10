# -*- coding: utf-8 -*-
"""把「≤3 字」这一档的概率提上来：真人 29% 的消息是 1~3 个字，模型目前 0%。"""

from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")

OLD_START = '    _REPLY_BUDGET_TIERS'
NEW_TIERS = '''    _REPLY_BUDGET_TIERS: tuple[tuple[float, str], ...] = (
        (0.25, "只回一个字或两个字：好 / 嗯 / 哦 / 行 / 嗯嗯 / 好嘞 / 没有 / 知道"),
        (0.40, "≤6 字：只说一两个词也行，像\\"那早点睡\\"\\"这么累\\"\\"真好啊\\""),
        (0.25, "≤12 字：一句短话，说完就停"),
        (0.10, "≤25 字：最多两句，中间用空格断，不要写成一段"),
    )
'''


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    start = src.find(OLD_START)
    if start < 0:
        print("!! 找不到 _REPLY_BUDGET_TIERS")
        return 2
    end = src.find("\n    )", start)
    if end < 0:
        print("!! 找不到档位结尾")
        return 2
    end += len("\n    )")
    src = src[:start] + NEW_TIERS.rstrip("\n") + src[end:]
    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print("tiers updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
