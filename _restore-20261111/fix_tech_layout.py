# -*- coding: utf-8 -*-
"""修回 helpers.py 里被 shell 转义吃掉的技术排版分支。

背景：上一步用 bash heredoc 写补丁，反斜杠-n 被解释成真换行、反斜杠-b 变成退格符，
helpers.py 的 `_apply_wechat_chat_style` 直接语法错误。这里按注释锚点整段重写。
所有模板都用 raw 字符串，避免再被转义。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "helpers.py")

BLOCK = r'''    # 技术内容（命令、包名、路径、报错）单独排版：句号/分号当换行，逗号保留，
    # 否则「python -m py_compile 你的脚本.py它会把出错的行号打出来」会糊成一整行。
    if _TECH_HINT_RE.search(normalized):
        tech = _strip_self_directed_questions(normalized)
        tech = tech.replace("。", _NL).replace("．", _NL)
        tech = _TECH_SEMI_RE.sub(_NL, tech)
        tech = re.sub(r"[ \t]{2,}", " ", tech)
        tech = _TECH_BLANK_RE.sub(_NL * 2, tech)
        tech = _PRIVATE_GLYPH_RE.sub("", tech)
        return tech.strip()
'''

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
    start = src.find("    # 技术内容（命令、包名、路径、报错）单独排版")
    if start < 0:
        print("!! 找不到被写坏的注释行")
        return 2
    end = src.find("    # 自问自答式问号", start)
    if end < 0:
        print("!! 找不到分支结束位置")
        return 2
    src = src[:start] + BLOCK + src[end:]

    anchor = "def _apply_wechat_chat_style(text: Any) -> str:"
    if "_TECH_HINT_RE" not in src:
        if anchor not in src:
            print("!! 找不到 _apply_wechat_chat_style")
            return 2
        src = src.replace(anchor, CONSTS + anchor, 1)

    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print("repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
