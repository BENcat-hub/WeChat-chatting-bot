# -*- coding: utf-8 -*-
"""把自问自答降级挪到「。→空格」之前，否则 ？→。 会留下句末句号。"""
import io, shutil, time

P = "astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")
s = io.open(P, encoding="utf-8").read()

call_block = (
    "    # 自问自答式问号（无对象的「就先这么坐着？」）按陈述句处理，避免句末莫名一个问号\n"
    "    normalized = _strip_self_directed_questions(normalized)\n"
)
assert s.count(call_block) == 1, s.count(call_block)
s = s.replace(call_block, "")

anchor = '''    if "`" in normalized:
        return normalized
'''
assert s.count(anchor) == 1, s.count(anchor)
s = s.replace(anchor, anchor + call_block)

# 顺手补一个空行，保持两行间隔的 PEP8 观感
s = s.replace("\n\ndef _apply_wechat_chat_style(", "\n\n\ndef _apply_wechat_chat_style(")

shutil.copy2(P, f"{P}.bak-selfqorder-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("fixed; backup", STAMP)
