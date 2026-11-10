# -*- coding: utf-8 -*-
"""补上 helpers.py 缺失的模块级常量（上一次替换把常量也一并算作「已存在」了）。"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "helpers.py")

CONSTS = r'''_NL = chr(10)
_TECH_HINT_RE = re.compile(
    r"(?:pip |python |npm |sudo |cd |git |\.py|/|\.exe|--?\w{2,}"
    r"|ModuleNotFoundError|SyntaxError|Traceback)"
)
_TECH_SEMI_RE = re.compile(r"[；;]\s*")
_TECH_BLANK_RE = re.compile(r"\n{3,}")
_PRIVATE_GLYPH_RE = re.compile("[\ue000-\uf8ff]|[\ufe00-\ufe0f]|[\u200b-\u200f\u2060\ufeff]")


'''


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    if "_TECH_HINT_RE = re.compile(" in src:
        print("常量已存在，跳过")
        return 0
    anchor = "def _apply_wechat_chat_style(text: Any) -> str:"
    if anchor not in src:
        print("!! 找不到锚点")
        return 2
    src = src.replace(anchor, CONSTS + anchor, 1)
    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print("consts inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
