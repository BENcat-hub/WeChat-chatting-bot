# -*- coding: utf-8 -*-
"""单元测试：语气词软断点（从 main.py 抠源码，不加载 AstrBot）。"""
import io
import re
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")
src = io.open(MAIN, encoding="utf-8").read()
m = re.search(r"^def _soft_break_after_particle.*?(?=^    def |\Z)", src, re.S | re.M)
assert m, "找不到 _soft_break_after_particle"
ns: dict = {"re": re}
exec(m.group(0), ns)
f = ns["_soft_break_after_particle"]

cases = [
    ("那就这么定了我这会儿就去把靠垫摆好", "那就这么定了，我这会儿就去把靠垫摆好"),
    ("反正提醒到就够了你自己心里有数就行", "反正提醒到就够了，你自己心里有数就行"),
    ("那我就不多嘴了傍晚了你那边也开始冷下来了吧", None),
    ("知道了就好", "知道了就好"),
    ("别熬了早点睡", "别熬了早点睡"),
    ("你今天怎么这么晚还没睡啊", "你今天怎么这么晚还没睡啊"),
    ("知道了知道了谢谢你了", None),
]
ok = True
for text, want in cases:
    got = f(text)
    flag = "OK " if (want is None or got == want) else "FAIL"
    if flag == "FAIL":
        ok = False
    print(f"{flag} {text!r} -> {got!r}")
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
