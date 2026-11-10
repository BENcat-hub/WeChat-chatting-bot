# -*- coding: utf-8 -*-
"""技术回复：保留标点，把换行折成句号（微信输入框里的换行会被发送逻辑丢掉）。"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "helpers.py")

NEW_BLOCK = '''    # 技术内容（命令、报错、路径）原样保留标点；换行折成句号——
    # 微信输入框里的换行会被发送逻辑丢掉，折成句号才读得出步骤。
    if _TECH_HINT_RE.search(normalized):
        tech = re.sub(r"\\s*\\n+\\s*", "。", normalized).strip()
        tech = re.sub(r"。[。\\s]*", "。", tech)
        return tech.strip()
'''


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    start = src.find("    # 技术内容（命令、报错、路径）原样保留")
    if start < 0:
        start = src.find("    # 技术内容（命令、包名、路径、报错）单独排版")
    if start < 0:
        print("!! 找不到技术分支")
        return 2
    end = src.find("    # 自问自答式问号", start)
    if end < 0:
        print("!! 找不到分支结尾")
        return 2
    src = src[:start] + NEW_BLOCK + src[end:]
    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print("patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
