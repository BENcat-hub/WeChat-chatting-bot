# -*- coding: utf-8 -*-
"""收紧「真问句」判定：只看向最后一小句 + 剔掉否定式里的疑问词。

线上样本（20:10:32 主动消息）：
    「我今晚这边没什么事 就是想着你那边别太累 累了就早点歇着？」
`没什么` 里的「什么」被当成疑问词，于是问号被保留。实际上最后一小句
（累了就早点歇着）既没有第二人称也没有疑问，属于自问。
"""
import io, shutil, time

STAMP = time.strftime("%Y%m%d-%H%M%S")
TARGETS = [
    ("astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py", "_is_question_meant_for_user", "_TO_USER_INTERROGATIVE_RE"),
    ("astrbot/data/plugins/astrbot_plugin_private_companion/proactive_message.py", "_is_real_question_to_user", None),
]

NEG = '''_NEGATED_INTERROGATIVE_RE = re.compile(r"没什么|没啥|不怎么|不怎么样|没什么事|没有|不少|差不多|多少有点")


def _tail_clause(text: str) -> str:
    """取最后一小句（用逗号/空格/顿号切），问句判定只看它。"""
    parts = re.split(r"[，,、\s]+", str(text or "").strip())
    return parts[-1] if parts else ""
'''

for path, fn, _ in TARGETS:
    s = io.open(path, encoding="utf-8").read()
    if "_tail_clause" not in s:
        anchor = f"def {fn}(sentence: str) -> bool:"
        assert s.count(anchor) == 1, (path, s.count(anchor))
        s = s.replace(anchor, NEG + "\n" + anchor, 1)
    # 在函数体里插入：否定式剥离 + 只看末句
    old_body = '''    text = str(sentence or "").strip()
    if not text:
        return False
'''
    new_body = '''    text = str(sentence or "").strip()
    if not text:
        return False
    probe = _NEGATED_INTERROGATIVE_RE.sub("", text)
    tail = _tail_clause(probe) or probe
'''
    assert s.count(old_body) == 1, (path, "body", s.count(old_body))
    s = s.replace(old_body, new_body, 1)
    shutil.copy2(path, f"{path}.bak-qjudge2-{STAMP}")
    io.open(path, "w", encoding="utf-8", newline="\n").write(s)
    print("patched", path)
