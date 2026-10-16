#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""修复陪伴面板把老用户显示成「临时会话 · xxx」的问题。

背景
----
``akasha-bridge/state.py`` 换成 sha256 稳定 ID 后，两件事变了：

1. 私聊用户的 OneBot user_id（旧值受 ``PYTHONHASHSEED`` 影响，每次重启都变）；
2. 机器人自己的 self_id（同上）。

private_companion 的用户档案里存着创建时的身份戳
（``identity_subject_id`` / ``identity_bot_id``）。事件进来时
``core_store._private_user_matches_event_identity`` 要求「主体 + 平台 + 适配器 + 机器人账号」
四项都一致，老档案带着旧值必然不匹配，插件于是退回
``{platform}:{canonical}:{digest}`` 这种命名空间键，面板把它渲染成
「临时会话 · <前 8 字>」（``page_api.py:17763``），新消息也就进了那份空档案。

本脚本做的就是把老档案的身份戳与 umo 同步到新的稳定 ID，并把已经产生的
「临时会话」档案合并回主档案（合并规则与插件 ``_merge_user_record_values`` 一致），
让下一次事件直接命中主档案。

用法
----
    python fix_companion_identity.py            # 只看计划（dry-run）
    python fix_companion_identity.py --apply    # 真写（先备份，建议先停 AstrBot）
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "akasha-bridge"))

import config  # noqa: E402  （bridge 目录里的配置，取 bot_wxid）
import state  # noqa: E402  （复用同一套稳定 ID 派生）

COMPANIONS = os.path.join(
    ROOT,
    "astrbot",
    "data",
    "plugin_data",
    "astrbot_plugin_private_companion",
    "companions.json",
)

ADDITIVE_KEYS = {
    "inbound_count",
    "private_inbound_count",
    "reply_count",
    "proactive_sent_count",
    "relationship_score",
    "sent_today",
    "ignored_streak",
    "unanswered_proactive_count",
    "poke_count",
}
MAX_KEYS = {
    "last_seen",
    "last_sent",
    "last_active_at",
    "last_user_message_at",
    "last_memory_refresh_at",
    "last_episode_refresh_at",
}
UMO_FIELDS = ("umo", "last_inbound_umo", "bound_delivery_umo", "preferred_delivery_umo", "delivery_umo")

# 面板把非数字键当「临时会话」；这类键形如 onebot:<id>:<digest>
TEMP_KEY_PREFIX = "onebot:"


def _num(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def merge_into(target: dict, source: dict, alias_id: str) -> None:
    """与插件 _merge_user_record_values 同口径地把 source 并进 target。"""
    for key, value in source.items():
        if key == "user_id":
            continue
        if key in ADDITIVE_KEYS:
            target[key] = _int(target.get(key)) + _int(value)
        elif key == "req041_relationship_source_revision":
            target[key] = max(_int(target.get(key)), _int(value))
        elif key in MAX_KEYS or key.endswith("_at") or key.endswith("_ts"):
            target[key] = max(_num(target.get(key)), _num(value))
        elif isinstance(value, list):
            existing = target.get(key)
            if not isinstance(existing, list):
                existing = []
                target[key] = existing
            for item in value:
                if item not in existing:
                    existing.append(copy.deepcopy(item))
        elif isinstance(value, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                existing = {}
                target[key] = existing
            for sub_key, sub_value in value.items():
                if sub_key not in existing or existing.get(sub_key) in (None, "", [], {}):
                    existing[sub_key] = copy.deepcopy(sub_value)
        elif target.get(key) in (None, "", [], {}):
            target[key] = copy.deepcopy(value)
    aliases = target.setdefault("alias_user_ids", [])
    if not isinstance(aliases, list):
        aliases = []
        target["alias_user_ids"] = aliases
    for alias in [alias_id, *(source.get("alias_user_ids") or [])]:
        text = str(alias or "").strip()
        if text and text not in aliases:
            aliases.append(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="修正陪伴面板用户档案的身份戳 / 合并临时会话档案")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    ap.add_argument("--companions", default=COMPANIONS)
    args = ap.parse_args()

    self_id = state._self_id_of(config.BOT_WXID)
    data = json.load(open(args.companions, encoding="utf-8-sig"))
    users = data["users"]
    print(f"自检：stable self_id = {self_id}")
    print(f"users: {len(users)} 个，其中数字键 {sum(1 for k in users if str(k).isdigit())} 个")

    todo_stamp: list[tuple[str, list[str]]] = []
    todo_merge: list[tuple[str, str]] = []
    for key, rec in users.items():
        if not isinstance(rec, dict):
            continue
        if str(key).isdigit():
            problems: list[str] = []
            if str(rec.get("identity_subject_id") or "").strip() != str(key):
                problems.append("identity_subject_id")
            if self_id and _int(rec.get("identity_bot_id")) != int(self_id):
                problems.append("identity_bot_id")
            want_umo = f"default:FriendMessage:{key}"
            for field in UMO_FIELDS:
                if field in rec and str(rec.get(field) or "").strip() != want_umo:
                    problems.append(field)
            if problems:
                todo_stamp.append((key, problems))
        elif str(key).startswith(TEMP_KEY_PREFIX):
            canonical = str(rec.get("identity_subject_id") or "").strip()
            if canonical.isdigit() and canonical in users:
                todo_merge.append((key, canonical))

    print("\n== 需要重打身份戳的老档案 ==")
    for key, fields in todo_stamp:
        print(f"   {key:<40} nickname={users[key].get('nickname')!r} 待修：{fields}")
    print("\n== 需要并回主档案的「临时会话」档案 ==")
    for temp, canonical in todo_merge:
        print(f"   {temp} → {canonical}（nickname={users[canonical].get('nickname')!r}）")

    if not args.apply:
        print("\n（dry-run：没有写任何文件；加 --apply 生效）")
        return 0

    stamp_now = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{args.companions}.bak-{stamp_now}"
    shutil.copy2(args.companions, backup)
    print(f"\n备份 → {os.path.basename(backup)}")

    for key, _fields in todo_stamp:
        rec = users[key]
        rec["identity_subject_id"] = str(key)
        if self_id:
            rec["identity_bot_id"] = str(int(self_id))
        want_umo = f"default:FriendMessage:{key}"
        for field in UMO_FIELDS:
            if field in rec:
                rec[field] = want_umo
    for temp, canonical in todo_merge:
        source = users.pop(temp)
        merge_into(users[canonical], source, temp)
        print(f"   已合并 {temp} → {canonical}")

    # 不要给被合并的临时键留 private_user_alias_merge_backups 记录：
    # 插件启动时会为「备份里有、但 private_user_aliases 里没有」的键**重建**一条空档案，
    # 那条空档案在面板上又会显示成「临时会话 · onebot:1」。
    backups = data.get("private_user_alias_merge_backups")
    if isinstance(backups, dict):
        for temp, _canonical in todo_merge:
            if backups.pop(temp, None) is not None:
                print(f"   已清理临时键的备份记录：{temp}")
    # 顺手清掉空壳临时档案（昵称/umo 都空的）
    for key in list(users):
        rec = users.get(key)
        if not isinstance(rec, dict) or str(key).isdigit():
            continue
        if not str(rec.get("nickname") or "").strip() and not str(rec.get("umo") or "").strip():
            users.pop(key, None)
            print(f"   已清理空壳档案：{key}")
    # 别名表里不能留「显示名」这种非数字条目：插件启动时会为它们重建空壳档案
    # （core_store.py:4515-4536：alias_user_ids 里不在 private_user_aliases 的条目会被还原成模板记录）
    for key, rec in users.items():
        if not isinstance(rec, dict):
            continue
        aliases = rec.get("alias_user_ids")
        if not isinstance(aliases, list):
            continue
        kept = [a for a in aliases if str(a or "").strip().isdigit()]
        if kept != aliases:
            rec["alias_user_ids"] = kept
            print(f"   {key}: 别名表去掉非数字条目 {[a for a in aliases if a not in kept]}")

    tmp = args.companions + ".tmp"
    # 插件用 json_backend 以**纯 utf-8** 读这份文件：带 BOM 会让它整份放弃加载
    # （日志：读取 JSON 失败… Unexpected UTF-8 BOM … StoryAuthorityError）。
    # 原文件是 CRLF + 无 BOM，这里保持一致。
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, args.companions)
    print(f"写入完成：{args.companions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
