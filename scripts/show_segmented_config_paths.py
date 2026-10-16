#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""打印分段相关键在 private_companion 配置里的**所有出现位置**（含嵌套路径）。

用于回答"到底哪一份在生效"：AstrBot 写回配置后会同时留下顶层键与
legacy_compat_config 副本，甚至可能追加第三份，必须按路径打印。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "astrbot" / "data" / "config" / "astrbot_plugin_private_companion_config.json"

KEYS = [
    "enable_segmented_proactive_reply",
    "enable_llm_controlled_segmenting",
    "enable_segmented_plugin_rules",
    "segmented_proactive_regex",
    "segmented_proactive_scope",
    "segmented_proactive_min_segment_chars",
    "segmented_proactive_max_segments",
    "segmented_proactive_private_min_segment_chars",
    "segmented_proactive_private_max_segments",
    "segmented_proactive_threshold",
]


def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    else:
        yield path, node


def main() -> int:
    print(f"配置文件: {CONFIG}")
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    print(f"顶层键数={len(cfg)}  顶层键: {', '.join(list(cfg)[:12])}{' ...' if len(cfg) > 12 else ''}")

    flat = list(walk(cfg))
    print("\n--- 分段键的所有出现位置 ---")
    for key in KEYS:
        hits = [(p, v) for p, v in flat if p.split(".")[-1] == key]
        if not hits:
            print(f"  {key}: (无)")
            continue
        for p, v in hits:
            print(f"  {p} = {v!r}")

    # 结构形状：各"作用域"的键数
    print("\n--- 容器结构 ---")
    for k, v in cfg.items():
        if isinstance(v, dict):
            print(f"  {k}: dict, {len(v)} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
