#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 private_companion 运行态里残留的历史 user_id 改写成稳定 id（Fix B）。

背景
----
id 漂移修复后（state.py 的 stable_int + 命名空间）新写入的数据都是稳定 id，但**迁移前**
已经写进 companions.json 的运行态仍带着旧 id，例如：

  * `proactive_candidate_pool[].user_id`（1000000005 / 1000000011 / 1000000002）；
  * `users[*].last_proactive_delivery_umo`（default:FriendMessage:1000000004 …）；
  * `private_delivery_routes` 的 umo 键、`groups` 的键等。

后果：调度器到点后把旧 id 当会话目标发给 bridge，wxui.py 找不到会话 → 消息没送到微信
（插件却记 status=sent）。bridge 侧已用 ob_id_map.json 兜底（Fix A），本脚本做根因清理。

不改动的东西（刻意保留历史）：
  * `private_user_alias_merge_backups`、任何 `*_backups`；
  * `alias_user_ids`（插件靠它把旧记录折进新记录）；
  * `proactive_audit_log` / `emotion_event_ledger` / `unified_profile_capability_audit` /
    `token_usage` 等审计类数据。

用法（AstrBot 必须先停，否则内存状态会覆盖回旧值）：
    python scripts/normalize_companion_legacy_ids.py            # dry-run（默认）
    python scripts/normalize_companion_legacy_ids.py --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from sync_bridge_id_aliases import (  # noqa: E402
    OB_ID_MAP, PLUGIN_CONFIG, SESSION_ID_MAP, load_json, parse_aliases, resolve,
)

COMPANIONS = ROOT / 'astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_private_companion' / 'companions.json'

SKIP_KEYS = {
    'private_user_alias_merge_backups',
    'alias_user_ids',
    'proactive_audit_log',
    'emotion_event_ledger',
    'unified_profile_capability_audit',
    'token_usage',
}


def build_id_map() -> dict[str, str]:
    # 注意：ob_id_map.json 现在同时收录了稳定 id 与旧 id（Fix A 的别名），
    # 所以「哪些是稳定 id」要用 session-id-map.json（只列旧 id）来排除。
    all_ids = {str(k) for k in load_json(OB_ID_MAP) if not str(k).startswith('_')}
    legacy_names = {str(k) for k in load_json(SESSION_ID_MAP) if not str(k).startswith('_')}
    canonical_names = all_ids - legacy_names
    cfg = load_json(PLUGIN_CONFIG)
    pairs = dict(parse_aliases(cfg.get('private_user_aliases')))
    basic = cfg.get('basic_config')
    if isinstance(basic, dict):
        pairs.update(parse_aliases(basic.get('private_user_aliases')))
    # 旧 id → 稳定 id（能解析出稳定 id 的才进映射；群 id 不在插件别名表里）
    out: dict[str, str] = {}
    for legacy in legacy_names:
        if legacy in canonical_names:
            continue
        target = resolve(legacy, pairs)
        if target in canonical_names:
            out[legacy] = target
    return out


def rewrite_str(value: str, id_map: dict[str, str]) -> str:
    if value in id_map:
        return id_map[value]
    # umo 形如 default:FriendMessage:1000000005 / default:GroupMessage:1000000016
    for sep in ('FriendMessage:', 'GroupMessage:'):
        marker = sep
        idx = value.rfind(marker)
        if idx >= 0:
            tail = value[idx + len(marker):]
            if tail in id_map:
                return value[:idx + len(marker)] + id_map[tail]
    return value


def walk(node, id_map, changes, path, skip=False):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            key = str(k)
            new_k = key
            if key in id_map:
                new_k = id_map[key]
                changes.append(f'{path}.{key} -> {new_k}（键）')
            if key in SKIP_KEYS:
                out[new_k] = v
                continue
            out[new_k] = walk(v, id_map, changes, f'{path}.{new_k}')
        return out
    if isinstance(node, list):
        return [walk(v, id_map, changes, f'{path}[{i}]') for i, v in enumerate(node)]
    if isinstance(node, str):
        new_v = rewrite_str(node, id_map)
        if new_v != node:
            changes.append(f'{path}: {node} -> {new_v}')
        return new_v
    return node


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='写回 companions.json（默认只预览）')
    args = ap.parse_args()

    id_map = build_id_map()
    print(f'旧 id → 稳定 id 映射（{len(id_map)} 条）：')
    for k, v in sorted(id_map.items()):
        print(f'  {k} -> {v}')

    data = json.loads(COMPANIONS.read_text(encoding='utf-8'))
    changes: list[str] = []
    new_data = walk(data, id_map, changes, '$')

    print(f'\n待改写 {len(changes)} 处：')
    for c in changes[:60]:
        print('  ' + c)
    if len(changes) > 60:
        print(f'  …（其余 {len(changes) - 60} 处省略）')

    if not args.apply:
        print('\n（dry-run，未写入；加 --apply 生效。注意：先停 AstrBot）')
        return 0
    if not changes:
        print('\n无需改写，未写入。')
        return 0
    COMPANIONS.write_text(
        json.dumps(new_data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n'
    )
    print(f'\n已写入 {COMPANIONS}（UTF-8 无 BOM）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
