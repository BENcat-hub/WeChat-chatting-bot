#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只读打印 private_companion 的主动消息运行态（companions.json）。"""
from __future__ import annotations

import json
import sys
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / 'astrbot' / 'data' / 'plugin_data' / 'astrbot_plugin_private_companion' / 'companions.json'


def ts(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if v <= 0:
        return '0'
    return dt.datetime.fromtimestamp(v).strftime('%m-%d %H:%M:%S')


def main() -> int:
    brief = '--brief' in sys.argv
    data = json.loads(CFG.read_text(encoding='utf-8'))
    rt = data.get('proactive_runtime') or {}
    if brief:
        print('{0}  tick={1}  users: {2}'.format(
            dt.datetime.fromtimestamp(CFG.stat().st_mtime).strftime('%H:%M:%S'),
            ts(rt.get('last_tick_finished_at')),
            '; '.join(
                '{0}({1}) next={2} last={3} sent={4} {5}'.format(
                    u.get('display_name') or key, key[:4],
                    ts(u.get('next_proactive_at')), ts(u.get('last_proactive_sent_at')),
                    u.get('proactive_sent_count'), u.get('last_proactive_skip_reason') or '')
                for key, u in (data.get('users') or {}).items() if isinstance(u, dict)
            )))
        return 0
    print('companions.json mtime :', dt.datetime.fromtimestamp(CFG.stat().st_mtime).strftime('%m-%d %H:%M:%S'))
    print('proactive_runtime     :', {
        k: (ts(v) if 'at' in k or 'ts' in k else v) for k, v in rt.items()
    })
    users = data.get('users') or {}
    print(f'\nusers ({len(users)})：')
    for key, u in users.items():
        if not isinstance(u, dict):
            continue
        print(f'  {key}  ({u.get("display_name") or u.get("nickname") or "?"})')
        for f in ('umo', 'proactive_private_enabled', 'daily_limit',
                  'next_proactive_at', 'last_proactive_sent_at', 'proactive_sent_count',
                  'unanswered_proactive_count', 'last_proactive_delivery_umo',
                  'planned_proactive_trigger_umo', 'suspended_proactive'):
            if f in u:
                v = ts(u[f]) if f.endswith('_at') else u[f]
                print(f'      {f} = {v}')
        if u.get('last_proactive_skip_reason'):
            print(f'      last_proactive_skip_reason = {u["last_proactive_skip_reason"]}')
    pool = data.get('proactive_candidate_pool') or []
    print(f'\nproactive_candidate_pool ({len(pool)})：')
    for e in pool:
        if isinstance(e, dict):
            print('  user_id={0}  kind={1}  next={2}'.format(
                e.get('user_id'), e.get('kind') or e.get('source'), ts(e.get('next_proactive_at'))))
    audit = data.get('proactive_audit_log') or []
    print(f'\nproactive_audit_log 末 5 条（共 {len(audit)}）：')
    for e in audit[-5:]:
        if isinstance(e, dict):
            print('  {0}  user={1}  status={2}  reason={3}'.format(
                ts(e.get('ts') or e.get('created_ts')), e.get('user_id'),
                e.get('status'), e.get('reason')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
