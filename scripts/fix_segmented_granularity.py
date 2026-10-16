#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调整 private_companion 分段粒度：让"只要是完整句子都拆开"。

用法：
    python fix_segmented_granularity.py            # 只读预览
    python fix_segmented_granularity.py --apply    # 写入（自动备份）

改动（顶层 + legacy_compat_config 两处都改，避免面板/运行时不一致）：
    segmented_proactive_min_segment_chars          8 -> 1
    segmented_proactive_max_segments               6 -> 8
    segmented_proactive_private_min_segment_chars  8 -> 1   （若存在）
    segmented_proactive_private_max_segments       3 -> 8   （若存在）

背景：`_merge_segments` 会把短于 min_segment_chars 的片重新合并（并插入
generated-comma 标记），所以原来的 8 会把 "行吧"、"对吗" 这类短句又粘回上一条，
用户看到的就是一长条。min=1 表示不再因为"太短"而合并。

配置文件是 UTF-8 **带 BOM**，必须以 utf-8-sig 读取。
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "astrbot" / "data" / "config" / "astrbot_plugin_private_companion_config.json"

CHANGES = {
    "segmented_proactive_min_segment_chars": 1,
    "segmented_proactive_max_segments": 8,
    "segmented_proactive_private_min_segment_chars": 1,
    "segmented_proactive_private_max_segments": 8,
}


def collect_targets(cfg: dict) -> list[tuple[str, dict, str, object, object]]:
    """返回 [(位置描述, 字典对象, 键, 旧值, 新值)]，只包含真正需要改的。"""
    out = []
    scopes = [("顶层", cfg)]
    legacy = cfg.get("legacy_compat_config")
    if isinstance(legacy, dict):
        scopes.append(("legacy_compat_config", legacy))
    for scope_name, scope in scopes:
        for key, new_value in CHANGES.items():
            if key not in scope:
                continue
            old_value = scope[key]
            if old_value != new_value:
                out.append((scope_name, scope, key, old_value, new_value))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写入配置文件（不加则只预览）")
    args = ap.parse_args()

    if not CONFIG.exists():
        print(f"[ERR] 配置不存在: {CONFIG}")
        return 2

    with CONFIG.open(encoding="utf-8-sig") as fh:
        cfg = json.load(fh)

    targets = collect_targets(cfg)
    print(f"配置文件: {CONFIG}")
    print(f"当前：min_segment_chars={cfg.get('segmented_proactive_min_segment_chars')!r} "
          f"max_segments={cfg.get('segmented_proactive_max_segments')!r} "
          f"regex={cfg.get('segmented_proactive_regex')!r}")
    if not targets:
        print("无需改动（值已经是目标值）。")
        return 0

    for scope_name, _scope, key, old_value, new_value in targets:
        print(f"  [改] {scope_name}.{key}: {old_value!r} -> {new_value!r}")

    if not args.apply:
        print("\n（预览模式，未写入；加 --apply 生效）")
        return 0

    backup_dir = ROOT / "logs" / f"segmented-fix-{time.strftime('%Y%m%d-%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (CONFIG.name + ".bak")
    shutil.copy2(CONFIG, backup)
    print(f"\n备份: {backup}")

    for _scope_name, scope, key, _old_value, new_value in targets:
        scope[key] = new_value

    with CONFIG.open("w", encoding="utf-8-sig", newline="\n") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # 复读校验
    with CONFIG.open(encoding="utf-8-sig") as fh:
        check = json.load(fh)
    ok = all(
        check.get(key, check.get("legacy_compat_config", {}).get(key)) == value
        for key, value in CHANGES.items()
    )
    print(f"写入完成，复读校验: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
