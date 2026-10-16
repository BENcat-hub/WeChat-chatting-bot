#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「同一个人被拆成多个会话」的旧数据归档到新的稳定 ID 下。

背景
----
akasha-bridge 过去用 ``abs(hash(wxid)) % 2**31`` 生成 OneBot user_id，
受 PYTHONHASHSEED 影响：bridge 每重启一次，同一个人就换一个 ID，
AstrBot 于是把他当成新用户，会话/记忆全部重开（用户报的「每次隔几个小时
对话，同一个人的 id 就会不一样，会创建一个新用户」）。

现在 bridge 改成 sha256 派生（见 ``akasha-bridge/state.py`` 的 ``stable_int``），
本脚本负责把历史碎片按**微信显示名**归并到新的稳定 ID：

* ``astrbot/data/data_v4.db``：``conversations``（对话上下文）、``umo_aliases``
  （会话名）、``provider_stats.umo``
* ``astrbot/data/plugin_data/astrbot_plugin_memory_companion/memory_companion.db``：
  ``identities`` 与所有按 ``session_id`` / ``subject_id`` 记录的表

``portrait_*`` / ``relationship_*`` 里的 ``person_id`` 是对身份字段做哈希得到的
（含 bot_account_id 等运行时字段），无法离线重算；那部分请用插件自带的
「私聊用户别名合并」（``private_user_aliases``，插件会自己带备份地合并）。
本脚本会顺带把需要填的别名映射打印成 JSON。

默认 dry-run，只打印计划；``--apply`` 才写库，且每个库先备份。

用法
----
    python merge_sessions.py                # 先看计划
    python merge_sessions.py --apply        # 落库（建议先停 AstrBot）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import urllib.request
import uuid
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "akasha-bridge"))

import config  # noqa: E402  (bridge 目录里的配置，含 WeFlow token)
import state   # noqa: E402  (复用同一套稳定 ID 派生)

ASTRBOT_DATA = os.path.join(ROOT, "astrbot", "data")
CORE_DB = os.path.join(ASTRBOT_DATA, "data_v4.db")
MEM_DB = os.path.join(
    ASTRBOT_DATA, "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db"
)
# 前次运行会把旧 umo_aliases 删掉；用这份「历史数字 id → 显示名」清单兜底
ID_MAP = os.path.join(ROOT, "scripts", "session-id-map.json")

# memory_companion.db 里所有按会话/主体记录的表（缺表会被跳过）
MEM_SESSION_TABLES = (
    "memories",
    "timeline",
    "knowledge_nodes",
    "knowledge_edges",
    "injection_logs",
    "emotion_events",
    "relationship_edges",
)


def fetch_sessions() -> list[dict]:
    """读 WeFlow 会话列表，拿到「显示名 → 稳定 wxid/chatroom id」。"""
    url = f"{config.WE_FLOW_BASE_URL}/api/v1/sessions?access_token={config.ACCESS_TOKEN}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    sessions = payload.get("sessions") if isinstance(payload, dict) else payload
    return sessions if isinstance(sessions, list) else []


def build_canonical(sessions: list[dict]) -> dict[str, dict]:
    """显示名 → {umo, message_type, subject_id, entity_kind, wxid}。"""
    canonical: dict[str, dict] = {}
    for item in sessions:
        if not isinstance(item, dict):
            continue
        name = str(item.get("displayName") or "").strip()
        username = str(item.get("username") or "").strip()
        kind = str(item.get("sessionType") or "").strip().lower()
        if not name or not username:
            continue
        if kind == "group" or username.endswith("@chatroom"):
            msg_type, entity_kind = "GroupMessage", "group"
            subject_id = state._group_id_int(username)
        elif kind == "private":
            msg_type, entity_kind = "FriendMessage", "user"
            subject_id = state._wxid_to_int(username)
        else:                      # channel 等不参与归档
            continue
        canonical[name] = {
            "umo": f"default:{msg_type}:{subject_id}",
            "message_type": msg_type,
            "subject_id": str(subject_id),
            "entity_kind": entity_kind,
            "wxid": username,
        }
    return canonical


def backup(path: str, stamp: str) -> str | None:
    if not os.path.exists(path):
        return None
    target = f"{path}.bak-{stamp}"
    shutil.copy2(path, target)
    return target


def split_umo(umo: str) -> tuple[str, str, str]:
    parts = str(umo or "").split(":")
    if len(parts) != 3:
        return "", "", ""
    return parts[0], parts[1], parts[2]


# ----------------------------------------------------------------- data_v4.db


def norm_umo(umo: str) -> str:
    """AstrBot 的 conversations.user_id 存的就是完整 UMO；历史脏数据可能缺 platform 前缀。"""
    text = str(umo or "")
    parts = text.split(":")
    if len(parts) == 3:
        return text
    if len(parts) == 2:
        return f"default:{text}"
    return text


def row_messages(row: sqlite3.Row | None) -> list:
    if row is None or not row["content"]:
        return []
    try:
        data = json.loads(row["content"])
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def plan_core(core: sqlite3.Connection, canonical: dict[str, dict], id_map: dict[str, str] | None = None) -> dict:
    con = core
    con.row_factory = sqlite3.Row
    id_map = id_map or {}
    aliases = {r["umo"]: r["auto_name"] for r in con.execute("SELECT umo, auto_name FROM umo_aliases")}
    creators = {
        r["umo"]: (r["creator_sender_id"] or "")
        for r in con.execute("SELECT umo, creator_sender_id FROM umo_aliases")
    }
    convs: dict[str, list[sqlite3.Row]] = {}
    for r in con.execute("SELECT rowid AS rid, * FROM conversations"):
        convs.setdefault(norm_umo(r["user_id"]), []).append(r)

    # 名称来源：① umo_aliases.auto_name ② 历史 id 映射文件（别名已被前次运行删掉时的兜底）
    name_by_umo: dict[str, str] = {umo: str(name or "").strip() for umo, name in aliases.items()}
    for umo in list(convs):
        if name_by_umo.get(umo):
            continue
        _platform, _msg_type, subject = split_umo(umo)
        guess = id_map.get(umo) or id_map.get(subject)
        if guess:
            name_by_umo[umo] = str(guess).strip()

    moves: list[tuple[str, str, str]] = []          # (old_umo, new_umo, name)
    for umo, name in name_by_umo.items():
        target = canonical.get(str(name or "").strip())
        if not target or umo == target["umo"]:
            continue
        _, msg_type, _ = split_umo(umo)
        if msg_type != target["message_type"]:      # 私聊/群聊不能混
            continue
        moves.append((umo, target["umo"], str(name).strip()))

    groups: dict[str, dict] = {}                    # new_umo → {old: [...], name}
    for old_umo, new_umo, name in moves:
        group = groups.setdefault(new_umo, {"old": [], "name": name})
        group["old"].append(old_umo)
        group["name"] = name

    # 同一个 UMO 可能有多行（旧 bug 追加的空行、或改名前的 AstrBot 缓存行）——
    # AstrBot 面板会把这些行显示成多个会话，所以即使没有改名也要合并成一行。
    for umo, rows in convs.items():
        if len(rows) > 1:
            group = groups.setdefault(umo, {"old": [], "name": ""})
            group["dedupe_only"] = True
    return {"moves": moves, "groups": groups, "convs": convs, "aliases": aliases, "creators": creators}


def apply_core(core: sqlite3.Connection, plan: dict) -> list[str]:
    con = core
    con.row_factory = sqlite3.Row
    log: list[str] = []
    convs = plan["convs"]
    for new_umo, group in plan["groups"].items():
        name = group["name"]
        rows: list[sqlite3.Row] = []
        seen_rid: set[int] = set()
        for umo in [new_umo] + list(group["old"]):
            for row in convs.get(norm_umo(umo), []):
                if row["rid"] in seen_rid:
                    continue
                seen_rid.add(row["rid"])
                rows.append(row)

        ordered = sorted(rows, key=lambda r: str(r["created_at"] or ""))
        merged: list = []
        for row in ordered:
            for msg in row_messages(row):
                if merged and json.dumps(merged[-1], ensure_ascii=False, sort_keys=True) == json.dumps(
                    msg, ensure_ascii=False, sort_keys=True
                ):
                    continue
                merged.append(msg)

        created = min([str(r["created_at"]) for r in ordered] or [datetime.now().isoformat()])
        updated = max([str(r["updated_at"]) for r in ordered] or [created])
        persona = next((r["persona_id"] for r in reversed(ordered) if r["persona_id"]), None)
        tokens = sum(int(r["token_usage"] or 0) for r in ordered)
        platform, msg_type, subject_id = split_umo(new_umo)
        platform = platform or "default"
        blob = json.dumps(merged, ensure_ascii=False)

        keep = next((r for r in ordered if norm_umo(r["user_id"]) == new_umo), None)
        if keep is None:
            con.execute(
                "INSERT INTO conversations (created_at, updated_at, inner_conversation_id,"
                " conversation_id, platform_id, user_id, content, title, persona_id, token_usage)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    created,
                    updated,
                    None,
                    str(uuid.uuid4()),
                    platform,
                    new_umo,
                    blob,
                    None,
                    persona,
                    tokens,
                ),
            )
        else:
            con.execute(
                "UPDATE conversations SET content=?, updated_at=?, created_at=?, persona_id=?,"
                " token_usage=?, user_id=?, platform_id=? WHERE rowid=?",
                (blob, updated, created, persona, tokens, new_umo, platform, keep["rid"]),
            )
        dropped = 0
        for row in ordered:
            if keep is not None and row["rid"] == keep["rid"]:
                continue
            con.execute("DELETE FROM conversations WHERE rowid=?", (row["rid"],))
            dropped += 1

        alias_row = con.execute("SELECT umo FROM umo_aliases WHERE umo=?", (new_umo,)).fetchone()
        if name:
            if alias_row is None:
                creator = plan["creators"].get(new_umo) or "merge_sessions"
                con.execute(
                    "INSERT INTO umo_aliases (created_at, updated_at, umo, creator_sender_id, auto_name)"
                    " VALUES (?,?,?,?,?)",
                    (created, updated, new_umo, creator, name),
                )
            else:
                con.execute(
                    "UPDATE umo_aliases SET auto_name=?, updated_at=? WHERE umo=?",
                    (name, updated, new_umo),
                )
        for old_umo in group["old"]:
            if old_umo != new_umo:
                con.execute("DELETE FROM umo_aliases WHERE umo=?", (old_umo,))
                con.execute("UPDATE provider_stats SET umo=? WHERE umo=?", (new_umo, old_umo))
        if group.get("dedupe_only") and not group["old"]:
            log.append(f"{new_umo}（同 UMO 多行去重，保留 {len(merged)} 条消息，清理 {dropped} 行）")
        else:
            log.append(
                f"{name}: {len(group['old'])} 个旧会话 → {new_umo}（{len(merged)} 条消息，清理 {dropped} 行）"
            )
    return log


# -------------------------------------------------------- memory_companion.db


def apply_memory(mem: sqlite3.Connection, canonical: dict[str, dict], umo_moves: dict[str, str]) -> list[str]:
    con = mem
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    log: list[str] = []

    # 1) identities：按显示名把旧 entity_id 归到稳定 id
    if "identities" in tables:
        rows = [dict(r) for r in con.execute("SELECT * FROM identities")]
        by_name: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            by_name.setdefault((str(row.get("display_name") or ""), str(row.get("entity_kind") or "")), []).append(row)
        for (name, kind), group in by_name.items():
            target = canonical.get(name)
            if not target or target["entity_kind"] != kind:
                continue
            new_id = f'aiocqhttp:{kind}:{target["subject_id"]}'
            newest = max(group, key=lambda r: str(r.get("updated_at") or ""))
            aliases: list[str] = []
            for row in group:
                try:
                    aliases += json.loads(row.get("aliases") or "[]")
                except (TypeError, ValueError):
                    pass
            profile = newest.get("profile") or "{}"
            try:
                profile_obj = json.loads(profile)
            except (TypeError, ValueError):
                profile_obj = {}
            last_session = str(profile_obj.get("last_session") or "")
            if last_session:
                _p, msg_type, _sid = split_umo(last_session)
                if msg_type in ("FriendMessage", "GroupMessage"):
                    profile_obj["last_session"] = f'default:{msg_type}:{target["subject_id"]}'
            for row in group:
                if row["id"] != new_id:
                    con.execute("DELETE FROM identities WHERE id=?", (row["id"],))
            con.execute(
                "INSERT OR REPLACE INTO identities (id, platform, entity_kind, entity_id, display_name,"
                " role, aliases, profile, confidence, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id,
                    newest.get("platform") or "aiocqhttp",
                    kind,
                    target["subject_id"],
                    name,
                    newest.get("role"),
                    json.dumps(sorted(set(a for a in aliases if a)), ensure_ascii=False),
                    json.dumps(profile_obj, ensure_ascii=False),
                    newest.get("confidence"),
                    min(str(r.get("created_at") or "") for r in group),
                    max(str(r.get("updated_at") or "") for r in group),
                ),
            )
            log.append(f"identities: {kind} {name} → {new_id}（合并 {len(group)} 条）")

    # 2) 按 session_id / subject_id 记录的表：把旧 id 换成稳定 id
    id_map: dict[str, str] = dict(umo_moves)
    for old_umo, new_umo in umo_moves.items():
        _p1, _t1, old_id = split_umo(old_umo)
        _p2, _t2, new_id = split_umo(new_umo)
        if old_id and new_id and old_id != new_id:
            id_map[old_id] = new_id
    if not id_map:
        return log
    for table in MEM_SESSION_TABLES:
        if table not in tables:
            continue
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        touched = 0
        for old, new in id_map.items():
            for col in ("session_id", "subject_id"):
                if col not in cols:
                    continue
                if col == "session_id" and ":" not in old:
                    continue
                if col == "subject_id" and ":" in old:
                    continue
                cur = con.execute(f"UPDATE {table} SET {col}=? WHERE {col}=?", (new, old))
                touched += cur.rowcount or 0
        if touched:
            log.append(f"{table}: 改写 {touched} 行")
    return log


def apply_cleanup(core: sqlite3.Connection, canonical: dict[str, dict]) -> list[str]:
    """删掉没有内容的会话行：插件/旧 bug 会留下指向旧 ID 的空壳会话，面板上看着像多出来的会话。"""
    con = core
    con.row_factory = sqlite3.Row
    keep = {info["umo"] for info in canonical.values()}
    dropped: list[str] = []
    for r in con.execute("SELECT rowid AS rid, user_id, content FROM conversations"):
        umo = norm_umo(r["user_id"])
        if umo in keep:
            continue
        try:
            empty = not json.loads(r["content"] or "[]")
        except (TypeError, ValueError):
            empty = False
        if not empty:
            continue
        con.execute("DELETE FROM conversations WHERE rowid=?", (r["rid"],))
        dropped.append(umo)
    return dropped


def main() -> int:
    ap = argparse.ArgumentParser(description="把按显示名拆散的旧会话归并到稳定 ID")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只打印计划）")
    ap.add_argument("--core-db", default=CORE_DB)
    ap.add_argument("--mem-db", default=MEM_DB)
    ap.add_argument("--id-map", default=ID_MAP, help="历史数字 id → 显示名 的映射文件")
    args = ap.parse_args()

    id_map: dict[str, str] = {}
    if os.path.exists(args.id_map):
        try:
            loaded = json.load(open(args.id_map, encoding="utf-8"))
            if isinstance(loaded, dict):
                id_map = {str(k): str(v) for k, v in loaded.items()}
        except (OSError, ValueError) as exc:
            print(f"id 映射读取失败（忽略）：{exc}")
    print(f"历史 id 映射：{len(id_map)} 条（{os.path.basename(args.id_map)}）")

    canonical = build_canonical(fetch_sessions())
    print(f"WeFlow 会话（规范 ID）：{len(canonical)} 个")
    for name, info in sorted(canonical.items()):
        print(f"   {name:16s} → {info['umo']}")

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dry = not args.apply
    plan: dict = {"moves": []}

    if os.path.exists(args.core_db):
        core = sqlite3.connect(args.core_db)
        try:
            plan = plan_core(core, canonical, id_map)
            print(f"\n== AstrBot 核心库：待归并 {len(plan['moves'])} 个旧会话")
            for old, new, name in plan["moves"]:
                print(f"   {name:16s} {old} → {new}")
            if not dry:
                if backup(args.core_db, stamp):
                    print(f"   备份 → {os.path.basename(args.core_db)}.bak-{stamp}")
                done = apply_core(core, plan)
                stale = apply_cleanup(core, canonical)
                core.commit()
                for line in done:
                    print(f"   ✓ {line}")
                for umo in stale:
                    print(f"   ✓ 删除空壳会话行：{umo}")
        finally:
            core.close()
    else:
        print(f"跳过（不存在）：{args.core_db}")

    if os.path.exists(args.mem_db):
        mem = sqlite3.connect(args.mem_db)
        try:
            if not dry and not backup(args.mem_db, stamp):
                print("   备份失败，跳过 memory_companion")
            else:
                if not dry:
                    print(f"   备份 → {os.path.basename(args.mem_db)}.bak-{stamp}")
                done = apply_memory(mem, canonical, {old: new for old, new, _ in plan["moves"]})
                if not dry:
                    mem.commit()
                print(f"\n== memory_companion：{len(done)} 处改写")
                for line in done:
                    print(f"   ✓ {line}")
        finally:
            mem.close()
    else:
        print(f"跳过（不存在）：{args.mem_db}")

    # 插件自带「私聊用户别名合并」用的映射（插件会自己带备份地合并用户档案）
    alias_map: dict[str, str] = {}
    for old_umo, new_umo, _name in (plan["moves"] if os.path.exists(args.core_db) else []):
        _p1, msg_type, old_id = split_umo(old_umo)
        _p2, _t2, new_id = split_umo(new_umo)
        if msg_type == "FriendMessage" and old_id != new_id:
            alias_map[old_id] = new_id
    if os.path.exists(args.mem_db):
        mem = sqlite3.connect(f"file:{args.mem_db}?mode=ro", uri=True)
        try:
            for row in mem.execute("SELECT entity_id, display_name, profile FROM identities WHERE entity_kind='user'"):
                target = canonical.get(str(row[1] or "").strip())
                if not target:
                    continue
                try:
                    last = str((json.loads(row[2] or "{}") or {}).get("last_session") or "")
                except (TypeError, ValueError):
                    last = ""
                _p, msg_type, old_id = split_umo(last)
                if msg_type == "FriendMessage" and old_id and old_id != target["subject_id"]:
                    alias_map.setdefault(old_id, target["subject_id"])
        finally:
            mem.close()

    print("\n== portrait/relationship（person_id 是身份哈希，离线无法重算）")
    print("   用插件自带的「私聊用户别名合并」：把下面这段填进 private_user_aliases，重启后插件会带备份地合并档案")
    print("   " + json.dumps(alias_map, ensure_ascii=False, sort_keys=True))
    print("   注：群成员 id 由「chatroom + 群昵称」派生，本脚本不猜——重新收一条该群消息即可生成新 id。")

    if dry:
        print("\n（dry-run：没有写任何文件；加 --apply 生效）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
