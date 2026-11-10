# -*- coding: utf-8 -*-
"""把 A不A 那行写回正确形式（heredoc 吞掉过 \\1，落成了 \\x01 控制符）。"""
import io
import re
import shutil
import time

STAMP = time.strftime("%Y%m%d-%H%M%S")
GOOD = '    if re.search(r"([\\u4e00-\\u9fff])\u4e0d\\1", tail):'

for path in (
    "astrbot/data/plugins/astrbot_plugin_private_companion/helpers.py",
    "astrbot/data/plugins/astrbot_plugin_private_companion/proactive_message.py",
):
    s = io.open(path, encoding="utf-8").read()
    lines = s.split("\n")
    changed = 0
    out = []
    for ln in lines:
        if "re.search(r\"([" in ln and "\u4e0d" in ln and ("\x01" in ln or ln.rstrip().endswith('"\u4e0d", tail):') or "\u4e0d\\1" not in ln and "tail):" in ln and "A\u4e0dA" not in ln and "u4e00" not in ln and "\u4e0d" in ln and "re.search" in ln):
            out.append(GOOD)
            changed += 1
            continue
        out.append(ln)
    if not changed:
        print(path, "no change (check manually)")
        continue
    shutil.copy2(path, f"{path}.bak-abufix-{STAMP}")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    print(path, "fixed lines:", changed)
