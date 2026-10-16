"""修「消息不分段」：把逗号等句内标点纳入切分点，并统一 LLM 分段开关。

背景（2026-10-16 取证）：
  * `segmented_proactive_regex` 原值 `，,.*?[。？！~…\\n]+|.+$` 的第一个分支
    要求「全角逗号 + 半角逗号」两个字符连续出现，正常聊天文本里永远不匹配，
    于是永远落到 `.+$`，整条消息被当成一段 → 规则切分等于失效。
  * `enable_llm_controlled_segmenting` 顶层是 False，而
    `legacy_compat_config` 副本是 True；运行时按 True 走（日志里出现过
    llm_segments=2），也就是「配置界面看到的」和「实际生效的」不一致。

用法：
  python fix_segmented_reply.py            # 只读：打印相关键的当前值
  python fix_segmented_reply.py --apply    # 写入（先自动备份）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.normpath(
    os.path.join(
        HERE,
        "..",
        "astrbot",
        "data",
        "config",
        "astrbot_plugin_private_companion_config.json",
    )
)

# 切分点：句末标点 + 逗号/分号/顿号（这才是「短气泡」的关键）
NEW_REGEX = r".*?[。！？!?…~～；;\n，,、]+|.+$"

REGEX_KEYS = (
    "segmented_proactive_regex",
    "segmented_proactive_private_regex",
    "segmented_proactive_group_regex",
)
BOOL_KEYS = ("enable_llm_controlled_segmenting",)

SECTIONS = ("", "legacy_compat_config")


def _targets(data: dict) -> list[dict]:
    """返回需要改写的配置层（顶层 + legacy 兼容副本）。"""
    layers = [data]
    legacy = data.get("legacy_compat_config")
    if isinstance(legacy, dict):
        layers.append(legacy)
    return layers


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    if not os.path.isfile(CFG):
        print(f"配置文件不存在: {CFG}")
        return 2
    with open(CFG, encoding="utf-8-sig") as handle:
        data = json.load(handle)

    changes: list[str] = []
    for layer in _targets(data):
        for key in REGEX_KEYS:
            if key not in layer:
                continue
            old = layer[key]
            if old != NEW_REGEX:
                layer[key] = NEW_REGEX
                changes.append(f"{key}: {old!r} -> {NEW_REGEX!r}")
        for key in BOOL_KEYS:
            if key not in layer:
                continue
            if layer[key] is not True:
                layer[key] = True
                changes.append(f"{key}: -> True")

    print(f"配置: {CFG}")
    if not changes:
        print("无需改动，当前值已是目标值。")
        return 0
    print("将做以下改动:")
    for line in changes:
        print("  -", line)
    if not apply:
        print("\n（未加 --apply，未写入）")
        return 0

    backup_dir = os.path.normpath(
        os.path.join(HERE, "..", "logs", f"segmented-fix-{time.strftime('%Y%m%d-%H%M%S')}")
    )
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(CFG) + ".bak")
    shutil.copy2(CFG, backup)
    print(f"\n已备份: {backup}")

    with open(CFG, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print("已写入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
