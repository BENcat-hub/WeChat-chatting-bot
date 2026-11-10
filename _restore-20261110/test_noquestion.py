# -*- coding: utf-8 -*-
"""用 AST 抽出 _is_real_question_to_user / _strip_meaningless_question_tail 单独验证。"""
import ast, logging, re, sys, types

P = "astrbot/data/plugins/astrbot_plugin_private_companion/proactive_message.py"
src = open(P, encoding="utf-8").read()
tree = ast.parse(src)
wanted = {"_is_real_question_to_user", "_strip_meaningless_question_tail"}
funcs = []
for node in tree.body:
    if isinstance(node, ast.ClassDef):
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in wanted:
                funcs.append(item)
print("找到函数:", [f.name for f in funcs])
assert len(funcs) == 2

mod = ast.Module(body=funcs, type_ignores=[])
ns = {
    "re": re,
    "logger": logging.getLogger("t"),
    "_single_line": lambda v, n=80: (str(v or "")[:n]),
}
code = compile(mod, P, "exec")
exec(code, ns)

class T:
    _single_line = staticmethod(lambda v, n=80: str(v or "")[:n])

for name in wanted:
    setattr(T, name, ns[name])
T._MEANINGLESS_QUESTION_TAIL_RE = re.compile(r"[？?]\s*$")
t = T()

cases = [
    ("下午这几小时过得有点慢 手边也没急着要做的事 就先这么坐着?", "下午这几小时过得有点慢 手边也没急着要做的事 就先这么坐着。"),
    ("下午过去一大半了 冰箱里那袋速冻饺子 我还没想好晚上怎么弄?", "下午过去一大半了 冰箱里那袋速冻饺子 我还没想好晚上怎么弄。"),
    ("下午剩下这点时间 我打算慢慢过 你也别太累着自己。", "下午剩下这点时间 我打算慢慢过 你也别太累着自己。"),
    ("你晚上想吃点什么吗？", "你晚上想吃点什么吗？"),
    ("要不要一起看会儿剧？", "要不要一起看会儿剧？"),
    ("桌边空的那块地方 我把书摊开看了看。", "桌边空的那块地方 我把书摊开看了看。"),
    ("今天就先这样吧?", "今天就先这样吧。"),
]
bad = 0
for raw, expect in cases:
    got = t._strip_meaningless_question_tail(raw)
    ok = got == expect
    bad += 0 if ok else 1
    print(("PASS " if ok else "FAIL "), raw, "->", got, "" if ok else f"(期望 {expect})")
print("失败:", bad)
sys.exit(1 if bad else 0)
