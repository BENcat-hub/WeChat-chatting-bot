#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线自测 wxui.py 的会话名匹配（不需要微信登录，也不做 OCR）。

背景：群名里带 emoji（例如 示例群聊B）时 Windows OCR 只读得出文字部分，
侧边栏又会把长名截断，备注名还会和真实群名分成两行显示 —— 原来「整串互相包含」的判断
在这种情况下永远匹配不上，于是 wxui 报 could not locate target row by OCR。

这里固定住三条性质：
  1. 去掉 emoji/符号后能对上（部分相同）；
  2. 备注名 + 群名分两行时，联合起来能对上；
  3. 「只是有点像」的别的会话在安全闸门（聊天头部校验）里不算命中。

用法：python scripts\\test_wxui_name_match.py
"""
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
sys.modules["wmcp"] = types.ModuleType("wmcp")          # 不连 Windows-MCP
sys.modules["wmcp"].Client = object

spec = importlib.util.spec_from_file_location("wxui", os.path.join(SCRIPTS, "wxui.py"))
wxui = importlib.util.module_from_spec(spec)
sys.modules["wxui"] = wxui
spec.loader.exec_module(wxui)

FAILS = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILS.append(label)


TARGET = "示例群聊B"


def test_name_key():
    check("emoji 会被规范化掉", wxui.name_key(TARGET) == "情vip508")
    check("标点/空格/大小写等价", wxui.name_key(" A B，c ") == "abc")


def test_partial_match():
    check("OCR 只剩文字部分（去掉 emoji）算完全一致", wxui.name_match(TARGET, "情VIP508") == 1.0)
    check("OCR 只读出一段也算命中（包含关系）", wxui.name_match(TARGET, "情VIP") == wxui.NAME_MATCH_CLEAR)
    check("原样命中", wxui.name_match(TARGET, TARGET) == 1.0)


def test_header_lines():
    check("备注名 + 群名两行联合命中", wxui.name_matches_any(TARGET, ["老群备注", "㊙情VIP508"]))
    check("单行就能命中", wxui.name_matches_any(TARGET, ["情VIP508"]))


def test_header():
    check("备注名 + 真群名（只读到一部分）两行也算命中",
          wxui.header_names_target("某某读书分享交流群", ["老群备注", "读书分享交流"]))
    check("emoji 群名 OCR 掉 emoji 后命中",
          wxui.header_names_target(TARGET, ["情VIP508", "在线 ①"]))
    check("只差一两个字的同一类群，单独出现时算命中（唯一候选）",
          wxui.header_names_target("某某读书分享交流群", ["某某读书分享交流会"]))
    check("头部只有无关会话时不命中",
          not wxui.header_names_target("某某读书分享交流群", ["文件传输助手"]))
    check("头部候选互相很像时不命中（不敢判断）",
          not wxui.header_names_target("某某读书分享交流群", ["某某读书分享交流会", "某某读书分享交流部"]))


def test_safety_gate():
    check("完全无关的会话不命中", not wxui.name_matches_any(TARGET, ["文件传输助手"]))
    check("只是有点像的别的会话不通过安全闸门",
          not wxui.name_matches_any(TARGET, ["文件VIP508"]))
    check("只是有点像的别的会话不通过安全闸门（反向亦然）",
          not wxui.name_matches_any("文件VIP508", [TARGET]))


def _find_row_with(lines, target):
    """Run find_row against stub OCR output; return (click_point, debug)."""
    cfg = wxui.config()
    rect = (0, 0, 3072, 1728)
    wxui.shot = lambda *a, **kw: "stub.png"
    wxui.image_size = lambda path: (620, 1000)
    wxui.ocr_lines = lambda path: {"ok": True, "engine": "stub", "lines": lines}
    return wxui.find_row(None, cfg, rect, target)


def _line(text, y):
    return {"text": text, "x": 10, "y": y, "words": [{"t": text, "x": 10, "y": y, "w": 90, "h": 18}]}


def test_find_row():
    point, dbg = _find_row_with(
        [_line("文件传输助手", 10), _line("情VIP508", 140), _line("项目组", 270)], TARGET)
    check("模糊命中时点中正确的那一行", bool(point) and dbg["fuzzy"] and dbg["picked"]["text"] == "情VIP508")

    point2, dbg2 = _find_row_with(
        [_line("文件传输助手", 10), _line("项目组", 140)], TARGET)
    check("没有候选时不乱点", point2 is None and not dbg2.get("picked"))

    point3, dbg3 = _find_row_with(
        [_line("文件VIP508", 10), _line("图片VIP508", 140)], TARGET)
    check("两个都「有点像」时不乱点", point3 is None)

    point4, dbg4 = _find_row_with(
        [_line("文件传输助手", 10), _line(TARGET, 140)], TARGET)
    check("原样命中优先（不走模糊）", bool(point4) and not dbg4["fuzzy"])


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(name)
            fn()
    if FAILS:
        print("\n%d 项失败: %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("\n全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
