# -*- coding: utf-8 -*-
"""让生图代理的 754 字符裁剪保住「身份锚句」。

问题：插件拼出的提示词约 4700 字符，代理按上游 800 上限裁剪时只保「头部场景 +
结尾要求句」，而插件把中文身份锚（人设特征）放在中段 ⇒ 被整段丢掉，模型只剩英文
泛化描述 + 参考图，长相就飘。

做法：新增 `_identity_rescue()`，把任意位置含身份标记（同一个人/鲸鳍/渐变长发/
天蓝色大眼睛/identity 等）的整句捞出来，插在头部与结尾要求句之间，并计入预算。

用法：python patch_prompt_identity.py [--apply]
"""
from __future__ import annotations

import io
import shutil
import sys
import time

PATH = "scripts/jimeng_image_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

HELPER = '''_IDENTITY_HINT_RE = re.compile(
    r"同一个人|始终是|鲸鳍|渐变长发|发梢浅蓝|发根深蓝|天蓝色大眼睛|识别特征|identity",
    re.IGNORECASE,
)


def _identity_rescue(text: str, limit: int) -> str:
    """抽出提示词里任意位置的身份锚句（中段也会被抓到）。

    插件把「画面里的女孩始终是同一个人：…鲸鳍发饰…」写在提示词中段，纯头尾裁剪会
    整段丢掉，导致生成出来的人只是「蓝发动漫少女」。这里按句捞取并限长。
    """
    picked: list[str] = []
    seen: set[str] = set()
    for start, end in _sentence_spans(text):
        piece = text[start:end].strip(" ,;\\n")
        if not piece or len(piece) < 8 or not _IDENTITY_HINT_RE.search(piece):
            continue
        key = piece[:40]
        if key in seen:
            continue
        seen.add(key)
        picked.append(piece)
        if sum(len(p) for p in picked) > max(0, limit // 3):
            break
    if not picked:
        return ""
    joined = " ".join(picked)
    cap = max(0, limit // 3)
    if len(joined) > cap:
        joined = joined[:cap].rstrip(" ,;.")
    return joined


'''

NEW_CLAMP = '''def clamp_prompt(prompt: str, limit: int = PROMPT_MAX_CHARS) -> str:
    """把提示词收进上游长度限制内：身份锚 + 结尾要求句优先，场景描写让位。

    2026-11-10：除结尾要求句，还要捞中段的身份锚句（见 `_identity_rescue`），否则
    人设特征会被裁掉。三段预算顺序：结尾要求句 > 身份锚 > 头部场景。
    """
    text = str(prompt or "")
    if len(text) <= limit:
        return text
    head_text, tail_text = _requirement_suffix(text, limit)
    tail = f" {tail_text}" if tail_text else ""
    identity = _identity_rescue(text, limit)
    if identity and identity in head_text:
        identity = ""
    id_part = f" {identity}" if identity else ""
    budget = max(120, limit - len(tail) - len(id_part))
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

src = io.open(PATH, encoding="utf-8").read()
if "def _identity_rescue(" in src:
    print("already patched")
    raise SystemExit(0)

start = src.index("def clamp_prompt(")
end = src.index("MODEL_ALIASES = {")
old = src[start:end]
print("旧 clamp_prompt 段落长度:", len(old))
head_ok = old.count("def clamp_prompt(") == 1
print("校验:", head_ok)

new_src = src[:start] + HELPER + NEW_CLAMP + "\n\n" + src[end:]
if not ("--apply" in sys.argv):
    print("(dry-run，加 --apply 才写)")
    raise SystemExit(0)

shutil.copy2(PATH, f"{PATH}.bak-idclamp-{STAMP}")
io.open(PATH, "w", encoding="utf-8", newline="\n").write(new_src)
print("written, backup ->", f"jimeng_image_server.py.bak-idclamp-{STAMP}")
