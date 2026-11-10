# -*- coding: utf-8 -*-
"""helpers.py：出站文本去掉自问自答的问号（覆盖普通回复与主动消息两条链路）。"""
import io, shutil, time

P = "astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

FUNC = '''_SELF_QUESTION_TAIL_RE = re.compile(r"[？?]\s*$")
_TO_USER_QUESTION_HEAD_RE = re.compile(
    r"^(?:要不要|是不是|想不想|有没有|好不好|行不行|在吗|睡了|醒了|忙吗|方便)"
)
_TO_USER_INTERROGATIVE_RE = re.compile(
    r"吗|么|呢|什么|怎么|咋|哪|谁|几点|多少|要不要|是不是|想不想|有没有|好不好|行不行|对不对|可以吗|方便吗"
)


def _is_question_meant_for_user(sentence: str) -> bool:
    """判断一句带问号的话是不是真的在问对方（而不是自问自答）。"""
    text = str(sentence or "").strip()
    if not text:
        return False
    if _TO_USER_QUESTION_HEAD_RE.match(text):
        return True
    if len(text) <= 12 and re.search(r"(?:吗|么|呢)[？?]\s*$", text):
        return True
    if re.search(r"你|您|妳", text) and _TO_USER_INTERROGATIVE_RE.search(text):
        return True
    return False


def _strip_self_directed_questions(text) -> str:
    """把没有对象的自问/反问（「就先这么坐着？」）的句末问号改成句号。"""
    source = str(text or "")
    if not source or not re.search(r"[？?]", source):
        return source
    out = []
    for unit in re.split(r"(?<=[。！？!?])", source):
        piece = unit
        if re.search(r"[？?]\s*$", piece) and not _is_question_meant_for_user(piece):
            piece = _SELF_QUESTION_TAIL_RE.sub("。", piece)
        out.append(piece)
    return "".join(out)


'''

lines = io.open(P, encoding="utf-8").read().split("\n")
out = []
inserted_func = False
inserted_call = False
for line in lines:
    if (not inserted_func) and line.startswith("def _apply_wechat_chat_style("):
        out.extend(FUNC.rstrip("\n").split("\n"))
        out.append("")
        inserted_func = True
    out.append(line)
    if (not inserted_call) and "re.sub(" in line and "ue000" in line:
        out.append("    # 自问自答式问号（无对象的「就先这么坐着？」）按陈述句处理，避免句末莫名一个问号")
        out.append("    normalized = _strip_self_directed_questions(normalized)")
        inserted_call = True

assert inserted_func, "func anchor missing"
assert inserted_call, "call anchor missing"
new = "\n".join(out)
shutil.copy2(P, f"{P}.bak-selfq-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(new)
print("written; backup", f"helpers.py.bak-selfq-{STAMP}")
