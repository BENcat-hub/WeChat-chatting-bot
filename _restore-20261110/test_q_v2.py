# -*- coding: utf-8 -*-
"""线上样本回归：20:10 那条「…累了就早点歇着？」必须被降级成陈述句。"""
import ast, io, re, sys

P = "astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py"
src = io.open(P, encoding="utf-8").read()
tree = ast.parse(src)
ns = {"re": re, "Any": object}
mods, consts = [], []
for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and (
        "QUESTION" in node.targets[0].id or "INTERROGATIVE" in node.targets[0].id
    ):
        consts.append(node)
    if isinstance(node, ast.FunctionDef) and node.name in {
        "_is_question_meant_for_user", "_strip_self_directed_questions",
        "_tail_clause", "_apply_wechat_chat_style",
    }:
        mods.append(node)
exec(compile(ast.Module(body=consts + mods, type_ignores=[]), P, "exec"), ns)

cases = [
    ("我今晚这边没什么事 就是想着你那边别太累 累了就早点歇着？",
     "我今晚这边没什么事 就是想着你那边别太累 累了就早点歇着"),
    ("要不要一起看会儿剧？", "要不要一起看会儿剧？"),
    ("你那边冷不冷？", "你那边冷不冷？"),
    ("冰箱里那袋速冻饺子 我还没想好晚上怎么弄?", "冰箱里那袋速冻饺子 我还没想好晚上怎么弄"),
]
bad = 0
for raw, expect in cases:
    got = ns["_apply_wechat_chat_style"](raw)
    ok = got == expect
    bad += 0 if ok else 1
    print("PASS" if ok else "FAIL", repr(raw), "->", repr(got), "" if ok else f"(期望 {expect!r})")
print("失败:", bad)
sys.exit(1 if bad else 0)
