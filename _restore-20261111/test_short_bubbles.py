# -*- coding: utf-8 -*-
"""离线验证 `_humanize_shorten`：从 event_dispatch.py 里抠出函数体，用 stub 跑。

检查点：
  1. 长句被切短，且切完每段 ≤ max_chars
  2. 条数不超过 max_bubbles，超了就合并最短的相邻两条
  3. 引号/书名号/换行的创作摘录原样保留
  4. 短句（≤max_chars）不被改动
"""
from __future__ import annotations

import io
import os
import re
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "event_dispatch.py")

src = io.open(TARGET, encoding="utf-8").read()
match = re.search(r"\n( +)def _humanize_shorten\(.*?\n\1    return produced\n", src, re.S)
assert match, "找不到 _humanize_shorten 定义"
body = textwrap.dedent(match.group(0))
ns: dict = {"re": re, "list": list, "str": str, "len": len, "range": range, "int": int, "bool": bool}
exec(compile(body, "<humanize>", "exec"), ns)
fn = ns["_humanize_shorten"]

cases = [
    (["没干嘛 躺着等睡意"], 16, 4, ["没干嘛 躺着等睡意"]),
    (["差就差着吧这会儿也不用硬撑着高兴面吃了 雨也听着 剩下的事明天再想"], 16, 4, None),
    (["这个点适合听慢的"], 16, 4, ["这个点适合听慢的"]),
    (["她说「雨一直下，气氛不算融洽，在同个屋檐下」别接话"], 16, 4, None),
    (["第一句很长很长很长很长的句子啊，第二句也不短也不短，第三句继续也很长，第四句还在继续"], 16, 4, None),
]

ok = True
for items, mx, mb, expect in cases:
    out = fn(items, mx, mb)
    print(f"in : {items}")
    print(f"out: {out}")
    if expect is not None and out != expect:
        print("   !! 期望", expect)
        ok = False
    if len(out) > mb:
        print(f"   !! 条数 {len(out)} > {mb}")
        ok = False
    # 允许两类「该长就长」：创作摘录、以及本身没有分隔符的一整块
    protected = any("「" in x or "\n" in x for x in items)
    if not protected:
        over = [x for x in out if len(x) > mx and not re.search(r"[，,、；;：:\s]", x)]
        hard_over = [x for x in out if len(x) > mx and re.search(r"[，,、；;：:\s]", x)]
        if hard_over:
            print("   !! 有分隔符却仍超长:", hard_over)
            ok = False
        if over:
            print("   · 无分隔符整块保留（可接受）:", over)
    print()

print("ALL PASS" if ok else "FAILED")
raise SystemExit(0 if ok else 1)
