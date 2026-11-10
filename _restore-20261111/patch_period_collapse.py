# -*- coding: utf-8 -*-
"""收口：把「。 。」这种重复/带空格的句号捏回一个。

真实故障（06:11 技术回答）：正文里出现「一般是网的事。 。换国内源试。」，
说明同一段文字在两处被排版过（一处把换行折成句号，另一处又留了空格），
最终用户看到的就是句号套空格。这里在 `_apply_wechat_chat_style` 收尾处统一捏一次，
不改变其它标点策略。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "helpers.py")

OLD = """    normalized = re.sub(""" + '"' + """[\\ue000-\\uf8ff]|[\\ufe00-\\ufe0f]|[\\u200b-\\u200f\\u2060\\ufeff]""" + '"' + """, "", normalized)
    normalized = re.sub(r"[ \\t]{2,}", " ", normalized)
    return normalized.strip()"""

NEW = """    normalized = re.sub(""" + '"' + """[\\ue000-\\uf8ff]|[\\ufe00-\\ufe0f]|[\\u200b-\\u200f\\u2060\\ufeff]""" + '"' + """, "", normalized)
    normalized = re.sub(r"[ \\t]{2,}", " ", normalized)
    # 重复句号收口：'。 。' / '。  ' / '。。' 统一成一个句号（同一段被排版两次时会出现）
    normalized = re.sub(r"[。．]\\s*[。．]+", "。", normalized)
    normalized = re.sub(r"[。．]\\s+(?=[^\\s])", "。", normalized)
    return normalized.strip()"""


def main() -> int:
    src = io.open(P, encoding="utf-8", newline="").read()
    if "重复句号收口" in src:
        print("已打过补丁")
        return 0
    if OLD not in src:
        print("!! 锚点未命中")
        return 2
    src = src.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="").write(src)
    print("helpers.py patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
