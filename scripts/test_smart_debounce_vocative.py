"""智能防抖快判的针对性自测（2026-10-19 纯呼唤补丁）。

用法（工作目录 wechat-ai-stack）：
    astrbot\\.venv\\Scripts\\python.exe -X utf8 scripts\\test_smart_debounce_vocative.py
"""
from __future__ import annotations

import inspect
import os
import sys
import types

PLUGINS = os.path.join("astrbot", "data", "plugins")
sys.path.insert(0, PLUGINS)

import importlib

mod = importlib.import_module("astrbot_plugin_private_companion.event_dispatch")

cls = None
for _, obj in vars(mod).items():
    if inspect.isclass(obj) and hasattr(obj, "_smart_message_debounce_fast_complete_reason"):
        cls = obj
        break
assert cls is not None, "class with debounce helpers not found"

inst = cls.__new__(cls)
inst.persona_setting = None
inst.persona = {}
for attr in ("persona_config", "config", "_config", "settings"):
    try:
        setattr(inst, attr, {})
    except Exception:
        pass

CASES_VOCATIVE = [
    ("宝", "纯呼唤未说完"),
    ("宝宝", "纯呼唤未说完"),
    ("宝。", "纯呼唤未说完"),
    ("喂？", "纯呼唤未说完"),
    ("欸", "纯呼唤未说完"),
    ("好的", ""),
    ("早上好", ""),
    ("在吗", ""),
    ("宝宝今天吃什么", ""),
]

CASES_FAST = [
    ("宝", ""),                      # 必须不再被「短句完整」放行
    ("早上好呀", "短互动完整"),        # 不再误判成完整疑问句
    ("你吃了吗", "完整疑问句"),
    # 带问号时会在更前面的「句末完整标点」分支就返回，同样 0 秒放行，语义正确。
    ("你好呀？", "句末完整标点"),
    ("好的", "短互动完整"),
    ("我今天下午去了趟超市", "未命中补话特征"),
]

fail = 0
for text, want in CASES_VOCATIVE:
    got = inst._smart_message_debounce_vocative_reason(text)
    ok = got == want
    fail += 0 if ok else 1
    print(f"[{'OK ' if ok else 'BAD'}] vocative({text!r}) = {got!r} (want {want!r})")

for text, want in CASES_FAST:
    got = inst._smart_message_debounce_fast_complete_reason(text)
    ok = got == want
    fail += 0 if ok else 1
    print(f"[{'OK ' if ok else 'BAD'}] fast_complete({text!r}) = {got!r} (want {want!r})")

print("\nFAILURES:", fail)
sys.exit(1 if fail else 0)
