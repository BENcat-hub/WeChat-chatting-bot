# -*- coding: utf-8 -*-
"""技术类回复（命令/报错/路径）不切条、不动排版。

背景：02:48 实测「pip 命令」类回答被切成 4 条，气泡里的词序都被拆乱
（「如果提示 pip 就先跑 本身不存在 -m python」）。
原因是为聊天设计的一系列转换（分段、标点改写、按字数合并）都在动这段文本。
技术内容的正确性比「像手打」重要，所以这里直接绕开全部整形步骤：
`_split_proactive_text` 见到技术特征就原样返回单条，`_apply_wechat_chat_style` 也原样返回。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion")
EVENT = os.path.join(PLUGIN, "event_dispatch.py")
HELPERS = os.path.join(PLUGIN, "helpers.py")

TECH_RE = (
    'r"(?:pip |python |python3 |py -|npm |sudo |cd |git |bash |powershell |\\.py\\b|\\\\|/|\\.exe\\b'
    '|--?\\w{2,}|ModuleNotFoundError|SyntaxError|Traceback|No module named)"'
)


def patch_event() -> None:
    src = io.open(EVENT, encoding="utf-8", newline="").read()
    if "_looks_technical_reply" in src:
        print("event_dispatch 已打过补丁")
        return
    anchor = '''        normalized = str(text or "").strip()
        if not normalized:
            return []
        tts_normalizer = getattr(self, "_normalize_tts_tags", None)'''
    guard = '''        normalized = str(text or "").strip()
        if not normalized:
            return []
        if re.search(''' + TECH_RE + ''', normalized):
            # 技术内容整段直出：不切条、不改写、不合并，避免命令被拆散
            return [normalized]
        tts_normalizer = getattr(self, "_normalize_tts_tags", None)'''
    if anchor not in src:
        print("!! event_dispatch 锚点没找到")
        return
    src = src.replace(anchor, guard, 1)
    io.open(EVENT, "w", encoding="utf-8", newline="").write(src)
    print("patched event_dispatch")


def patch_helpers() -> None:
    src = io.open(HELPERS, encoding="utf-8", newline="").read()
    start = src.find("    # 技术内容（命令、包名、路径、报错）单独排版")
    if start < 0:
        print("helpers 里没有技术分支（可能已回滚）")
        return
    end = src.find("    # 自问自答式问号", start)
    new_block = '''    # 技术内容（命令、报错、路径）原样保留：不做标点改写，也不把句子拼成一行，
    # 否则「python -m py_compile 你的脚本.py」这种会被空格化后读不出来。
    if _TECH_HINT_RE.search(normalized):
        return normalized.strip()
'''
    src = src[:start] + new_block + src[end:]
    io.open(HELPERS, "w", encoding="utf-8", newline="").write(src)
    print("patched helpers")


def main() -> int:
    patch_helpers()
    patch_event()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
