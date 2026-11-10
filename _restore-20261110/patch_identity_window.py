# -*- coding: utf-8 -*-
"""把 _identity_rescue 改成「关键词窗口」抽取：整句可能长到几千字符（插件用逗号拼接），
整句截断会把关键词本身挤出去。改为围绕每个身份标记取 ±130 字符窗口。

用法：python patch_identity_window.py [--apply]
"""
import io
import shutil
import sys
import time

P = "scripts/jimeng_image_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

NEW = '''def _identity_rescue(text: str, limit: int) -> str:
    """抓身份锚：围绕每个身份标记取一个窗口，再拼成一段。

    2026-11-10 实测：插件的 fixed prompt 用逗号拼成一条超长「句子」，按句抽取再整体
    截断会把关键词本身挤出预算（`has_identity=True` 但 `identity_in_sent=False`）。
    所以改为按标记定位取窗口。
    """
    if not text:
        return ""
    windows: list[tuple[int, int]] = []
    for match in _IDENTITY_HINT_RE.finditer(text):
        start = max(0, match.start() - 130)
        end = min(len(text), match.end() + 130)
        if any(not (end <= s or start >= e) for s, e in windows):
            continue
        windows.append((start, end))
        if len(windows) >= 3:
            break
    if not windows:
        return ""
    cap = max(80, min(limit // 3, 320))
    pieces: list[str] = []
    used = 0
    for start, end in windows:
        piece = re.sub(r"\\s+", " ", text[start:end]).strip(" ,;.\\n")
        if not piece:
            continue
        room = cap - used
        if room <= 40:
            break
        pieces.append(piece[:room])
        used += len(pieces[-1]) + 1
    return " ".join(pieces)
'''

src = io.open(P, encoding="utf-8").read()
start = src.index("def _identity_rescue(")
end = src.index("def clamp_prompt(")
print("旧函数长度:", end - start)
if "--apply" not in sys.argv:
    print("(dry-run，加 --apply 才写)")
    raise SystemExit(0)
shutil.copy2(P, f"{P}.bak-idwin-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(src[:start] + NEW + "\n\n" + src[end:])
print("written, backup ->", f"jimeng_image_server.py.bak-idwin-{STAMP}")
