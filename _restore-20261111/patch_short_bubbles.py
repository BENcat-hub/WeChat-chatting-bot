# -*- coding: utf-8 -*-
"""人味儿改造：把过长的气泡按语义切短（结构层，不靠模型自觉）。

背景（用真实语料量出来的差距）：
  真人私聊 中位字数 5、≤6 字占 60.6%、无标点占 86.6%、逗号密度 0.15；
  改造前机器人 中位 14、≤6 字 18.8%、逗号密度 0.53 —— 一眼就是「成段说话」。

做法：`_split_proactive_text` 里 `_merge_segments` 之后加一道 `_humanize_shorten`：
  · 气泡超过 humanize_max_chars（默认 16）就在逗号/顿号/分号/空格/句末标点处切开，
    按 max_chars 贪心装回若干个气泡（最多 max_segments 条）；
  · 带引号/书名号/换行的「创作摘录」不动，避免切碎歌词诗句；
  · 开关 humanize_short_bubbles 默认开，关掉即恢复原行为（纯增量、不删逻辑）。

替换器按「缩进自适应」工作：先读回原 return 行的缩进，再生成同缩进的 if 块。
"""
from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "event_dispatch.py")

HELPER = '''        def _humanize_shorten(
            items: list[str],
            max_chars: int = 16,
            max_bubbles: int = 4,
        ) -> list[str]:
            """把偏长的气泡切短，贴近真人「一条一句、短句连发」的节奏。

            只做切分，不改写、不删字：切点优先逗号/顿号/分号/空格，
            其次在句末标点处；带引号书名号或换行的创作摘录不动。
            """
            if max_chars < 8:
                max_chars = 8
            if max_bubbles < 1:
                max_bubbles = 1

            def _protected(text: str) -> bool:
                if "\\n" in text:
                    return True
                return any(mark in text for mark in ("「", "」", "《", "》", "『", "』"))

            def _pieces(text: str) -> list[str]:
                raw = re.split(r"([，,、；;：:]+|\\s+|[。！？!?…~～]+)", text)
                out: list[str] = []
                buf = ""
                for part in raw:
                    if not part:
                        continue
                    if re.fullmatch(r"[，,、；;：:\\s。！？!?…~～]+", part):
                        if buf:
                            out.append(buf)
                            buf = ""
                        continue
                    buf += part
                if buf:
                    out.append(buf)
                return [piece for piece in out if piece.strip()]

            produced: list[str] = []
            for item in items:
                text = str(item or "").strip()
                if not text:
                    continue
                if len(text) <= max_chars or _protected(text):
                    produced.append(text)
                    continue
                buf = ""
                for piece in _pieces(text):
                    candidate = piece if not buf else (buf + " " + piece)
                    if len(candidate) <= max_chars:
                        buf = candidate
                    else:
                        if buf:
                            produced.append(buf)
                        buf = piece
                if buf:
                    produced.append(buf)

            while len(produced) > max_bubbles:
                best = -1
                best_len = 10 ** 9
                for pos in range(len(produced) - 1):
                    size = len(produced[pos]) + len(produced[pos + 1])
                    if size < best_len:
                        best_len = size
                        best = pos
                if best < 0:
                    break
                produced[best] = (produced[best] + " " + produced[best + 1]).strip()
                del produced[best + 1]
            return produced
'''

RETURN_LINE = "return segments if segments and (len(segments) > 1 or cleanup_enabled) else [restore_markdown(normalized)]"
GATE = 'if segments and bool(setting("humanize_short_bubbles", True)) and len(segments) > 0:'


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    if "_humanize_shorten" in src:
        print("已经打过补丁，跳过")
        return 0
    if src.count(RETURN_LINE) != 2:
        print(f"!! 期望 2 处 return，实际 {src.count(RETURN_LINE)} 处")
        return 2

    anchor = '\n        if split_mode == "words":'
    if anchor not in src:
        print("!! 找不到 split_mode == words 锚点")
        return 2
    src = src.replace(anchor, "\n" + HELPER.rstrip("\n") + anchor, 1)

    # 从后往前替换，避免偏移错位；缩进按原行自适应
    positions = [m.start() for m in re.finditer(re.escape(RETURN_LINE), src)]
    for pos in reversed(positions):
        line_start = src.rfind("\n", 0, pos) + 1
        indent = src[line_start:pos]
        block = (
            f"{indent}{GATE}\n"
            f"{indent}    segments = _humanize_shorten(\n"
            f"{indent}        segments,\n"
            f"{indent}        _safe_int(setting(\"humanize_max_chars\", 16), 16, 8, 40),\n"
            f"{indent}        max_segments,\n"
            f"{indent}    )\n"
            f"{indent}{RETURN_LINE}"
        )
        src = src[:line_start] + block + src[pos + len(RETURN_LINE):]

    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print(f"patched {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
