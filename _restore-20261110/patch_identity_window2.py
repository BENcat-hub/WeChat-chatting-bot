# -*- coding: utf-8 -*-
"""重写 _identity_rescue + clamp_prompt 的身份段逻辑（第三版）。

前两版踩到的坑：
1. 按「整句」抽取 —— 插件的 fixed prompt 用逗号拼成几千字符的伪长句，整句截断后关键词被挤掉。
2. 窗口以「标记前 130 字符」为起点 —— 前置填充吃掉预算，且去重判据用填充片段误判成
   「已在头部」直接丢弃（实测 `has_identity=True` 但 `identity_in_sent=False`）。

这一版：窗口以标记为中心（前 60 / 后 110），去重只看「身份段开头是否已落在保留的头部里」。
"""
import io
import shutil
import sys
import time

P = "scripts/jimeng_image_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

NEW = '''def _identity_rescue(text: str, limit: int) -> str:
    """抓身份锚：以每个身份标记为中心取窗口（前 60、后 110 字符）。

    窗口必须围绕标记，否则前置的重复填充会把关键词自己挤出预算（2026-11-10 实测）。
    """
    if not text:
        return ""
    cap = max(80, min(limit // 3, 300))
    pieces: list[str] = []
    used = 0
    last_end = -1
    for match in _IDENTITY_HINT_RE.finditer(text):
        if match.start() < last_end:
            continue
        start = max(0, match.start() - 60)
        end = min(len(text), match.end() + 110)
        piece = re.sub(r"\\s+", " ", text[start:end]).strip(" ,;.\\n")
        if len(piece) < 8:
            continue
        room = cap - used
        if room <= 40:
            break
        pieces.append(piece[:room])
        used += len(pieces[-1]) + 1
        last_end = match.end()
        if len(pieces) >= 3:
            break
    return " ".join(pieces)


def clamp_prompt(prompt: str, limit: int = PROMPT_MAX_CHARS) -> str:
    """把提示词收进上游长度限制内：结尾要求句 + 身份锚优先，场景描写让位。

    预算顺序：结尾要求句 > 身份锚 > 头部场景。身份锚按窗口取（见 `_identity_rescue`）。
    """
    text = str(prompt or "")
    if len(text) <= limit:
        return text
    head_text, tail_text = _requirement_suffix(text, limit)
    tail = f" {tail_text}" if tail_text else ""
    identity = _identity_rescue(text, limit)
    id_part = f" {identity}" if identity else ""
    budget = max(120, limit - len(tail) - len(id_part))
    head = head_text[:budget]
    if identity and identity[:24] in head:
        # 身份锚已经完整落在保留的头部里，不再重复追加。
        id_part = ""
        budget = max(120, limit - len(tail))
        head = head_text[:budget]
    for sep in (". ", "\\u3002", "! ", "\\uff01", "? ", "\\uff1f", "; ", "\\uff1b", ", ", "\\uff0c", " "):
        idx = head.rfind(sep)
        if idx >= int(budget * 0.6):
            head = head[: idx + len(sep)]
            break
    else:
        head = head.rstrip()
    result = (head.strip() + id_part + tail).strip()
    if len(result) > limit:
        keep_tail = (id_part + tail).strip()
        if len(keep_tail) >= limit:
            return keep_tail[:limit]
        room = limit - len(keep_tail) - 1
        result = (head_text[:room].rstrip() + " " + keep_tail).strip()[:limit]
    return result
'''

src = io.open(P, encoding="utf-8").read()
start = src.index("def _identity_rescue(")
end = src.index("MODEL_ALIASES = {")
print("替换区段长度:", end - start)
if "--apply" not in sys.argv:
    print("(dry-run，加 --apply 才写)")
    raise SystemExit(0)
shutil.copy2(P, f"{P}.bak-idwin2-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(src[:start] + NEW + "\n\n" + src[end:])
print("written, backup ->", f"jimeng_image_server.py.bak-idwin2-{STAMP}")
