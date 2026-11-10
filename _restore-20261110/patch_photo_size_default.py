# -*- coding: utf-8 -*-
"""让陪伴插件生图默认用配置里的 4K 尺寸，而不是硬编码 1024x1024。

三处：
1) main.py 工具文档串里「如 1024x1024」→ 改成 4693x3520，并说明留空走配置；
2) llm_tool_actions.py 的 image_size 归一化：空值/1024x1024 走配置默认；
3) proactive_message.py 每日穿搭硬编码 image_size="1024x1024" → 留空走配置。
"""
import io
import os
import re
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion")
STAMP = time.strftime("%Y%m%d-%H%M%S")


def patch(rel, pairs, expect=None):
    path = os.path.join(PLUG, rel)
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    original = src
    for old, new in pairs:
        if old not in src:
            raise SystemExit(f"!! anchor missing in {rel}: {old[:60]!r}")
        src = src.replace(old, new, 1)
    if src == original:
        print(f"= {rel} 无变化")
        return
    shutil.copy2(path, f"{path}.bak-4ksize-{STAMP}")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print(f"✓ {rel} 已改（备份 .bak-4ksize-{STAMP}）")


# 1) 工具文档串
patch(
    "main.py",
    [
        (
            "            image_size(string): 可选，在线图片 API 尺寸，如 1024x1024。",
            "            image_size(string): 可选，在线图片 API 尺寸，默认留空即用配置（当前为 4693x3520 竖幅 4K）；"
            "只有在用户明确要求别的比例/尺寸时才填写，例如 2304x1728。",
        )
    ],
)

# 2) image_size 归一化：空 或 1024x1024 → 留空，交给下游用配置默认（4693x3520）
patch(
    "llm_tool_actions.py",
    [
        (
            '            "image_size": _single_line(image_size or kwargs.get("size"), 40),',
            '            "image_size": _photo_size_or_default(image_size or kwargs.get("size")),',
        )
    ],
)

# 2b) 注入辅助函数
_helper = '''

def _photo_size_or_default(value: object) -> str:
    """模型常照抄工具文档示例的 1024x1024；这里把「示例默认值」视为未指定，
    交给图片插件按配置的 external_image_api_size（当前 4693x3520）出图。"""
    text = _single_line(value, 40)
    if text.lower().replace(" ", "").replace("*", "x") in {"", "1024x1024"}:
        return ""
    return text

'''
path = os.path.join(PLUG, "llm_tool_actions.py")
with io.open(path, encoding="utf-8") as f:
    src = f.read()
if "_photo_size_or_default" not in src.split('"image_size": _photo_size_or_default')[0]:
    m = re.search(r"^(?:from|import) .*$", src, flags=re.M)
    if not m:
        raise SystemExit("!! llm_tool_actions.py 找不到 import 行")
    insert_at = src.find("\n\n\n", m.end())
    if insert_at == -1:
        raise SystemExit("!! 找不到插入点")
    src = src[:insert_at] + _helper + src[insert_at:]
    shutil.copy2(path, f"{path}.bak-4ksize-{STAMP}")
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    print(f"✓ llm_tool_actions.py 已注入 _photo_size_or_default")

# 3) 每日穿搭
patch(
    "proactive_message.py",
    [
        (
            '            session_key="daily_outfit",\n            image_size="1024x1024",',
            '            session_key="daily_outfit",\n            image_size="",',
        )
    ],
)
