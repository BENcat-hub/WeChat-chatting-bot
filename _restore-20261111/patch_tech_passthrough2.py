# -*- coding: utf-8 -*-
"""给 `_split_proactive_text` 加技术内容直出闸（按位置插入，不依赖整段锚点）。"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "event_dispatch.py")

TECH_RE_LINE = (
    '        if re.search(r"(?:pip |python |python3 |py -|npm |sudo |cd |git |bash |powershell '
    '|\\.py\\b|/|\\.exe\\b|--?\\w{2,}|ModuleNotFoundError|SyntaxError|Traceback|No module named)", '
    'normalized):\n'
    '            # 技术内容整段直出：不切条、不改写、不合并，避免命令被拆散\n'
    '            return [normalized]\n'
)


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    if "技术内容整段直出" in src:
        print("已打过补丁")
        return 0
    fn = src.find("def _split_proactive_text(")
    if fn < 0:
        print("!! 找不到 _split_proactive_text")
        return 2
    needle = "        normalized = str(text or \"\").strip()\n"
    pos = src.find(needle, fn)
    if pos < 0:
        print("!! 找不到 normalized 赋值行")
        return 2
    insert_at = pos + len(needle)
    src = src[:insert_at] + TECH_RE_LINE + src[insert_at:]
    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print("patched event_dispatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
