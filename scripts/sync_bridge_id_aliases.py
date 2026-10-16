#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把历史（不稳定）OneBot user_id 合并进 bridge 的 id↔联系人映射。

背景
----
akasha-bridge 早期用 `abs(hash(wxid)) % 2**31` 生成 OneBot user_id，受 PYTHONHASHSEED
影响，每次重启同一个人都会换 id（详见 docs/VERIFICATION.md P1-3）。现在 state.py 已改成
确定性哈希（stable_int + 命名空间），但**存量数据里还留着旧 id**：

  * private_companion 的 companions.json 里，迁移前排好的主动消息
    （proactive_impulses / proactive_candidate_pool / private_delivery_routes /
    last_proactive_delivery_umo）仍然写着旧 id；
  * 调度器到点后会把旧 id 当会话目标发给 bridge → wxui.py 找不到该会话 →
    「⚠ 文字未验证送达（已交重发队列）」。

bridge 用 `akasha-bridge/ob_id_map.json`（id → 联系人名）把 OneBot 数字 id 还原成微信联系人。
本脚本把「旧 id → 同一个联系人」的条目补进这份映射，于是即使插件还拿着旧 id，消息也能落到
正确的人身上（治标，但立即生效、零风险）。真正的清理见 docs 里 Fix B（重写 companions.json
里的旧 id）。

用法
----
    python scripts/sync_bridge_id_aliases.py            # 预览（dry-run，默认）
    python scripts/sync_bridge_id_aliases.py --apply    # 写回 ob_id_map.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SESSION_ID_MAP = SCRIPTS / 'session-id-map.json'
PLUGIN_CONFIG = ROOT / 'astrbot' / 'data' / 'config' / 'astrbot_plugin_private_companion_config.json'
OB_ID_MAP = ROOT / 'akasha-bridge' / 'ob_id_map.json'


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def parse_aliases(raw) -> dict[str, str]:
    """与插件 core_store._parse_private_user_aliases 同口径：alias=canonical。"""
    pairs: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).strip() and str(v).strip():
                pairs[str(k).strip()] = str(v).strip()
        return pairs
    if isinstance(raw, list):
        items = [str(x) for x in raw]
    else:
        items = str(raw or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    for line in items:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        for sep in ('=>', '->', '=', ':', '：'):
            if sep in line:
                a, _, c = line.partition(sep)
                a, c = a.strip(), c.strip()
                if a and c:
                    pairs[a] = c
                break
    return pairs


def resolve(alias: str, pairs: dict[str, str], depth: int = 8) -> str:
    seen = set()
    cur = alias
    while depth and cur in pairs and pairs[cur] != cur and cur not in seen:
        seen.add(cur)
        cur = pairs[cur]
        depth -= 1
    return cur


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='写回 ob_id_map.json（默认只预览）')
    args = ap.parse_args()

    canonical_names: dict[str, str] = {
        str(k): str(v) for k, v in load_json(OB_ID_MAP).items() if not str(k).startswith('_')
    }
    legacy_names: dict[str, str] = {
        str(k): str(v) for k, v in load_json(SESSION_ID_MAP).items() if not str(k).startswith('_')
    }

    cfg = load_json(PLUGIN_CONFIG)
    alias_pairs: dict[str, str] = {}
    alias_pairs.update(parse_aliases(cfg.get('private_user_aliases')))
    basic = cfg.get('basic_config')
    if isinstance(basic, dict):
        alias_pairs.update(parse_aliases(basic.get('private_user_aliases')))

    additions: dict[str, str] = {}
    unresolved: list[str] = []
    for legacy in legacy_names:
        if legacy in canonical_names:
            continue  # 已经是稳定 id
        target = resolve(legacy, alias_pairs)
        name = canonical_names.get(target) or legacy_names.get(legacy)
        if name:
            additions[legacy] = name
        if target == legacy or target not in canonical_names:
            unresolved.append(f'{legacy}(->{target})')

    merged = dict(canonical_names)
    merged.update(additions)
    ordered = {k: merged[k] for k in sorted(merged, key=lambda x: (len(x), x))}

    print(f'bang: ob_id_map 现有 {len(canonical_names)} 条；插件别名 {len(alias_pairs)} 对')
    print(f'待新增 {len(additions)} 条：')
    for k, v in sorted(additions.items()):
        print(f'  + {k} -> {v}')
    if unresolved:
        print('⚠ 未能解析出稳定 id（仅按 session-id-map 的显示名直填）：')
        for u in unresolved:
            print(f'    {u}')
    print(f'合并后共 {len(ordered)} 条')

    if not args.apply:
        print('\n（dry-run，未写入；加 --apply 生效）')
        return 0
    if not additions:
        print('\n无需新增，未写入。')
        return 0
    OB_ID_MAP.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(f'\n已写入 {OB_ID_MAP}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
