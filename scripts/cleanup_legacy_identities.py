#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清理「稳定 ID 迁移」留下的四类残渣（默认 dry-run）。

1. ``memory_companion.db``：群身份老行（群 id 从「群名 hash」改成「chatroom hash」后剩下的
   ``identities`` 行 + 引用它们的会话表行）→ 归并到当前群 id。
2. ``companions.json``：``target_user_ids`` 里被误写成**显示名**的档案（插件会给每个条目建一份
   「主人」档案）→ 合并到该名字对应的稳定 id 档案，并删掉名字键档案。
3. 插件配置：``target_user_ids``（扁平键 + basic_config 分组键）里的显示名/占位符 → 稳定 id。
4. ``data_v4.db``：``preferences.sel_conv_id`` 指向已被合并掉的会话 → 改指存活会话；
   老 umo 的 preference 行、``umo_aliases.creator_sender_id`` 的老 id → 清理 / 改成稳定 id。

用法
----
    python cleanup_legacy_identities.py            # 只打印计划
    python cleanup_legacy_identities.py --apply    # 落库（先停 AstrBot；每个文件先备份）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "akasha-bridge"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import merge_sessions as ms  # noqa: E402
from fix_companion_identity import merge_into  # noqa: E402

COMPANIONS = os.path.join(
    ROOT,
    "astrbot",
    "data",
    "plugin_data",
    "astrbot_plugin_private_companion",
    "companions.json",
)
PLUGIN_CONFIG = os.path.join(
    ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json"
)
ID_MAP = os.path.join(ROOT, "scripts", "session-id-map.json")


def backup(path: str, stamp: str) -> None:
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.bak-{stamp}")


def scan_refs(con: sqlite3.Connection, needle: str, tables: list[str]) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    for table in tables:
        try:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        except sqlite3.Error:
            continue
        for col in cols:
            try:
                n = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE CAST({col} AS TEXT)=?", (needle,)
                ).fetchone()[0]
            except sqlite3.Error:
                continue
            if n:
                found.append((table, col, n))
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description="清理稳定 ID 迁移残渣")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--companions", default=COMPANIONS)
    ap.add_argument("--plugin-config", default=PLUGIN_CONFIG)
    ap.add_argument("--id-map", default=ID_MAP)
    args = ap.parse_args()

    id_map = json.load(open(args.id_map, encoding="utf-8"))
    canonical = ms.build_canonical(ms.fetch_sessions())
    name_to_id = {name: info["subject_id"] for name, info in canonical.items()}
    group_targets = {
        name: info["subject_id"]
        for name, info in canonical.items()
        if info.get("message_type") == "GroupMessage"
    }
    old_group_ids = {
        old: group_targets[name]
        for old, name in id_map.items()
        if not old.startswith("_") and name in group_targets and old != group_targets[name]
    }
    name_to_umo = {name: info["umo"] for name, info in canonical.items()}
    print(f"群规范 id：{group_targets}")
    print(f"待归并的老群 id：{old_group_ids}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    todo: list[str] = []

    # ------------------------------------------------------- 1) memory_companion 群残渣
    mem = sqlite3.connect(ms.MEM_DB)
    mem.row_factory = sqlite3.Row
    mem_tables = {r[0] for r in mem.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    groups_scan = [t for t in ms.MEM_SESSION_TABLES if t in mem_tables] + ["identities"]
    print("\n== 1) memory_companion：老群 id 引用 ==")
    group_rewrites: list[tuple[str, str, str, str, int]] = []
    for old_id, new_id in old_group_ids.items():
        for needle in (old_id, f"aiocqhttp:group:{old_id}"):
            for table, col, n in scan_refs(mem, needle, groups_scan):
                group_rewrites.append((table, col, needle, needle.replace(old_id, new_id), n))
                print(f"   {table}.{col}: {needle!r} → {needle.replace(old_id, new_id)!r}（{n} 行）")
    if not group_rewrites:
        print("   无")

    # ------------------------------------------------------- 2) companions.json 名字键档案
    data = json.load(open(args.companions, encoding="utf-8-sig"))
    users = data.get("users") or {}
    print("\n== 2) companions.json：按显示名建的档案 ==")
    record_moves: list[tuple[str, str]] = []      # (名字键, 目标数字键)
    record_drops: list[str] = []
    for key, rec in list(users.items()):
        if not isinstance(rec, dict) or str(key).isdigit():
            continue
        if str(rec.get("user_id") or "").strip().isdigit():
            continue
        target_id = name_to_id.get(str(key).strip())
        if target_id and str(target_id) in users:
            record_moves.append((str(key), str(target_id)))
            print(f"   {key!r}（role={rec.get('relationship_role')!r}）→ 合并进 {target_id}")
        else:
            record_drops.append(str(key))
            print(f"   {key!r}（role={rec.get('relationship_role')!r}）→ 直接删（没有对应的稳定 id）")

    # ------------------------------------------------------- 3) 插件配置 target_user_ids
    cfg = json.load(open(args.plugin_config, encoding="utf-8-sig"))
    print("\n== 3) 插件配置 target_user_ids ==")
    cfg_changes: list[tuple[str, object, object]] = []
    for path in (("target_user_ids",), ("basic_config", "target_user_ids")):
        node = cfg
        for part in path:
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, list):
            continue
        new_list: list[str] = []
        for item in node:
            text = str(item or "").strip()
            if not text:
                continue
            if text.isdigit():
                new_list.append(text)
                continue
            mapped = name_to_id.get(text)
            if mapped:
                new_list.append(str(mapped))
            else:
                print(f"   {'.'.join(path)}: 去掉无对应稳定 id 的条目 {text!r}")
        if new_list != [str(x) for x in node]:
            cfg_changes.append((".".join(path), list(node), new_list))
            print(f"   {'.'.join(path)}: {list(node)} → {new_list}")
    if not cfg_changes:
        print("   无")

    # ------------------------------------------------------- 4) data_v4.db 零散引用
    core = sqlite3.connect(ms.CORE_DB)
    core.row_factory = sqlite3.Row
    surviving = {
        ms.norm_umo(r["user_id"]): r["conversation_id"]
        for r in core.execute("SELECT user_id, conversation_id FROM conversations")
    }
    valid_conv_ids = set(surviving.values())
    canonical_umos = set(name_to_umo.values())
    print("\n== 4) data_v4.db ==")
    pref_fix: list[tuple[int, str, str]] = []      # (rowid, 老值, 新值)
    pref_drop: list[int] = []
    seen_umo: set[str] = set()
    for r in core.execute("SELECT rowid AS rid, scope_id, key, value FROM preferences WHERE scope='umo'"):
        umo = ms.norm_umo(r["scope_id"])
        if r["key"] != "sel_conv_id":
            if umo not in canonical_umos:
                pref_drop.append(r["rid"])
                print(f"   preferences#{r['rid']}（{r['scope_id']} / {r['key']}）→ 删（老 umo）")
            continue
        try:
            cur = str((json.loads(r["value"] or "{}") or {}).get("val") or "")
        except (TypeError, ValueError):
            cur = ""
        want = surviving.get(umo)
        if umo not in canonical_umos or not want or umo in seen_umo:
            pref_drop.append(r["rid"])
            print(f"   preferences#{r['rid']}（{r['scope_id']} → {cur[:8]}）→ 删（老 umo / 重复 / 无存活会话）")
            continue
        seen_umo.add(umo)
        if cur != want:
            pref_fix.append((r["rid"], cur, want))
            print(f"   preferences#{r['rid']}（{umo}）sel_conv_id {cur[:8]} → {want[:8]}")
    alias_fix: list[tuple[str, str, str]] = []
    for old_id in id_map:
        if old_id.startswith("_"):
            continue
        for r in core.execute(
            "SELECT umo, creator_sender_id FROM umo_aliases WHERE creator_sender_id=?", (old_id,)
        ).fetchall():
            target = name_to_umo.get(id_map[old_id], "")
            new_id = target.split(":")[-1] if target else ""
            if new_id:
                alias_fix.append((r["umo"], old_id, new_id))
                print(f"   umo_aliases[{r['umo']}].creator_sender_id {old_id} → {new_id}")

    if not args.apply:
        print("\n（dry-run：没有写任何文件；加 --apply 生效）")
        mem.close()
        core.close()
        return 0

    # ------------------------------------------------------------ apply
    if group_rewrites:
        backup(ms.MEM_DB, stamp)
        print(f"\n备份 → {os.path.basename(ms.MEM_DB)}.bak-{stamp}")
        for old_id in old_group_ids:
            for rid in [r[0] for r in mem.execute("SELECT id FROM identities WHERE id=? OR entity_id=?",
                                                  (f"aiocqhttp:group:{old_id}", old_id))]:
                mem.execute("DELETE FROM identities WHERE id=?", (rid,))
                print(f"   ✓ 删除旧群身份行 {rid}")
        for table, col, old, new, _n in [x for x in group_rewrites if x[0] != "identities"]:
            cur = mem.execute(f"UPDATE {table} SET {col}=? WHERE CAST({col} AS TEXT)=?", (new, old))
            print(f"   ✓ {table}.{col}: {cur.rowcount} 行 → {new!r}")
        mem.commit()

    if record_moves or record_drops:
        backup(args.companions, stamp)
        print(f"   备份 → {os.path.basename(args.companions)}.bak-{stamp}")
        for src, dst in record_moves:
            merge_into(users[dst], users.pop(src), src)
            print(f"   ✓ 合并 {src!r} → {dst!r}")
        for key in record_drops:
            users.pop(key, None)
            print(f"   ✓ 删除 {key!r}")
        tmp = args.companions + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, args.companions)

    if cfg_changes:
        backup(args.plugin_config, stamp)
        print(f"   备份 → {os.path.basename(args.plugin_config)}.bak-{stamp}")
        for path, _old, new_list in cfg_changes:
            node = cfg
            parts = path.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = new_list
            print(f"   ✓ {path} → {new_list}")
        with open(args.plugin_config, "w", encoding="utf-8-sig") as fh:   # 该文件本来带 BOM
            json.dump(cfg, fh, ensure_ascii=False, indent=2)

    if pref_fix or pref_drop or alias_fix:
        backup(ms.CORE_DB, stamp)
        print(f"   备份 → {os.path.basename(ms.CORE_DB)}.bak-{stamp}")
        for rid, _cur, want in pref_fix:
            core.execute(
                "UPDATE preferences SET value=? WHERE rowid=?", (json.dumps({"val": want}), rid)
            )
        for rid in pref_drop:
            core.execute("DELETE FROM preferences WHERE rowid=?", (rid,))
        for umo, _old, new_id in alias_fix:
            core.execute("UPDATE umo_aliases SET creator_sender_id=? WHERE umo=?", (new_id, umo))
        core.commit()
        print(f"   ✓ preferences 改指 {len(pref_fix)} 行、删 {len(pref_drop)} 行；creator_sender_id 改 {len(alias_fix)} 行")

    mem.close()
    core.close()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
