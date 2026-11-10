# -*- coding: utf-8 -*-
import ast, io, re, sys

P = "astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py"
src = io.open(P, encoding="utf-8").read()
tree = ast.parse(src)
ns = {"re": re, "Any": object}
wanted = {"_is_question_meant_for_user", "_strip_self_directed_questions", "_apply_wechat_chat_style"}
mods, consts = [], []
for node in tree.body:
    if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("_") and ("QUESTION" in t.id or "INTERROGATIVE" in t.id) for t in node.targets):
        consts.append(node)
    if isinstance(node, ast.FunctionDef) and node.name in wanted:
        mods.append(node)
exec(compile(ast.Module(body=consts + mods, type_ignores=[]), P, "exec"), ns)

cases = [
    ("下午这几小时过得有点慢 手边也没急着要做的事 就先这么坐着?", "下午这几小时过得有点慢 手边也没急着要做的事 就先这么坐着"),
    ("冰箱里那袋速冻饺子 我还没想好晚上怎么弄?", "冰箱里那袋速冻饺子 我还没想好晚上怎么弄"),
    ("你晚上想吃点什么吗？", "你晚上想吃点什么吗？"),
    ("要不要一起看会儿剧？", "要不要一起看会儿剧？"),
    ("真的吗？好耶~", "真的吗？好耶~"),
    ("行吧。", "行吧"),
]
bad = 0
for raw, expect in cases:
    got = ns["_apply_wechat_chat_style"](raw)
    ok = got == expect
    bad += 0 if ok else 1
    print("PASS" if ok else "FAIL", repr(raw), "->", repr(got), "" if ok else f"(期望 {expect!r})")
print("失败:", bad)
sys.exit(1 if bad else 0)
