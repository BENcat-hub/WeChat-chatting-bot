# -*- coding: utf-8 -*-
"""P0-B：把「历史 id 的档案」并进「当前 id 的档案」，清掉空壳。

背景：bridge 早期用 `abs(hash(wxid)) % 2**31` 生成 OneBot user_id（受
PYTHONHASHSEED 影响），后来改成 sha256 派生（`akasha-bridge/state.py`
`stable_int("user", wxid)`）。改法之后同一个人拿到的是新 id，但 AstrBot 里
旧 id 的档案还在，于是面板上出现「同一个人两条记录」，主动消息也会按两条
记录各发一次。

现在能唯一对应的四组（当前 id 由 wxid 现算，见脚本内 ID_MAP 注释）：

    旧 1905794693  ->  新 1031792701   不曾为世界整除 (wxid_qvj04bh9wqhl22)
    旧 2119307100  ->  新 1007787221   陈奥琦        (wxid_ml4l1e98nlyn22)
    旧  16250560   ->  新 1043793717   天文学家      (wxid_iwfcw218p6pq22)
    旧 1012022627  ->  新 1057020421   文件传输助手  (filehelper)

另清 4 个纯残留壳：`桃桃真可爱` / `.` / `onebot:1045254607:...` /
`onebot:1905794693:...`（episodes=0、memory=0、主动关闭）。

必须先停 AstrBot（11229 未监听），否则内存态会写回。

用法：
  python _restore-20261110/merge_dup_20261110.py            # 预览
  python _restore-20261110/merge_dup_20261110.py --apply    # 落盘
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "astrbot", "data", "data_v4.db")
CP = os.path.join(
    ROOT, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json"
)

# (旧 id, 当前 id, 展示名)
PAIRS = [
    ("1905794693", "1031792701", "不曾为世界整除"),
    ("2119307100", "1007787221", "陈奥琦"),
    ("16250560", "1043793717", "天文学家"),
    ("1012022627", "1057020421", "文件传输助手"),
]

SHELLS = [
    "桃桃真可爱",
    ".",
    "onebot:1045254607:53dc1b0c46fc7c80",
    "onebot:1905794693:970ad57471314a9d",
]

UMO_PREFIX = "wechat-ob11:FriendMessage:"


def port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def as_list(raw: str | None) -> list:
    try:
        v = json.loads(raw or "[]")
    except Exception:
        return []
    return v if isinstance(v, list) else []


def msg_key(m):
    if not isinstance(m, dict):
        return ("raw", str(m)[:120])
    c = m.get("content")
    if isinstance(c, list):
        parts = [str(p.get("text") or "") for p in c if isinstance(p, dict) and p.get("type") == "text"]
        c = " ".join(parts)
    return (str(m.get("role") or ""), str(c or "")[:300])


def merge_history(old: list, new: list) -> list:
    out, seen = [], set()
    for m in list(old) + list(new):
        k = msg_key(m)
        if k in seen:
            continue
        seen.add(k)
        out.append(m)
    return out


def merge_dict_list(dst: list, src: list, fields: tuple[str, ...]) -> int:
    def key(x):
        return tuple(str(x.get(f) or "")[:80] for f in fields)

    seen = {key(x) for x in dst if isinstance(x, dict)}
    added = 0
    for x in src:
        if not isinstance(x, dict):
            continue
        k = key(x)
        if k in seen:
            continue
        seen.add(k)
        dst.append(x)
        added += 1
    return added


def episodes_of(u: dict) -> list:
    return u.get("dialogue_episodes") or []


def memory_of(u: dict) -> list:
    return (u.get("companion_memory") or {}).get("items") or []


def merge_profile(dst: dict, src: dict) -> dict:
    stats = {
        "episodes": merge_dict_list(
            dst.setdefault("dialogue_episodes", []), src.get("dialogue_episodes") or [], ("date", "summary")
        ),
        "open_loops": merge_dict_list(
            dst.setdefault("open_loops", []), src.get("open_loops") or [], ("text", "created_ts")
        ),
    }
    dm = src.get("companion_memory") or {}
    km = dst.setdefault("companion_memory", {})
    stats["memory_items"] = merge_dict_list(km.setdefault("items", []), dm.get("items") or [], ("text",))
    if str(dm.get("updated_at") or "") > str(km.get("updated_at") or ""):
        km["updated_at"] = dm.get("updated_at")
    stats["learned_rules"] = merge_dict_list(
        dst.setdefault("expression_profile", {}).setdefault("learned_rules", []),
        (src.get("expression_profile") or {}).get("learned_rules") or [],
        ("kind", "situation", "pattern", "instruction"),
    )
    stats["relationship_ledger"] = merge_dict_list(
        dst.setdefault("relationship_ledger", []),
        src.get("relationship_ledger") or [],
        ("reason", "delta", "created_ts"),
    )
    stats["recent_proactive_topics"] = merge_dict_list(
        dst.setdefault("recent_proactive_topics", []),
        src.get("recent_proactive_topics") or [],
        ("text", "signature", "ts"),
    )
    dst["ignored_streak"] = max(int(dst.get("ignored_streak") or 0), int(src.get("ignored_streak") or 0))
    stamps = [int(v) for v in (dst.get("next_proactive_at"), src.get("next_proactive_at")) if int(v or 0) > 0]
    if stamps:
        dst["next_proactive_at"] = min(stamps)
    for flag in ("proactive_private_enabled",):
        if src.get(flag) is True:
            dst[flag] = True
    return stats


def main() -> int:
    apply = "--apply" in sys.argv
    if port_open(11229):
        print("!! AstrBot 还在跑（11229 在听），先停再合并；内存态会把改动写回去")
        return 2

    with io.open(CP, encoding="utf-8") as fh:
        data = json.load(fh)
    users = data.get("users") or {}

    shell_report, bad_shells = [], []
    for key in SHELLS:
        u = users.get(key)
        if not isinstance(u, dict):
            continue
        ep, mem = len(episodes_of(u)), len(memory_of(u))
        shell_report.append((key, ep, mem))
        if ep or mem:
            bad_shells.append(key)
    if bad_shells:
        print("!! 空壳里带数据，中止：", bad_shells)
        return 3

    plan = []
    for old_id, new_id, name in PAIRS:
        old_u = users.get(old_id)
        new_u = users.get(new_id)
        if not isinstance(old_u, dict):
            print(f"-- {name}: 旧档案 {old_id} 不存在，跳过")
            continue
        if isinstance(new_u, dict):
            plan.append(("merge", old_id, new_id, name))
            print(
                f"-- {name}: 合并 {old_id}(ep{len(episodes_of(old_u))}/mem{len(memory_of(old_u))}) "
                f"-> {new_id}(ep{len(episodes_of(new_u))}/mem{len(memory_of(new_u))})"
            )
        else:
            plan.append(("rename", old_id, new_id, name))
            print(
                f"-- {name}: 迁移键 {old_id}(ep{len(episodes_of(old_u))}/mem{len(memory_of(old_u))}) -> {new_id}（当前无档案）"
            )
    print("shells:", shell_report)
    if not plan and not shell_report:
        print("== 没有可合并项")
        return 0

    # ---------- DB ----------
    conn = sqlite3.connect(DB)
    db_plan = []
    for old_id, new_id, name in PAIRS:
        uo, un = UMO_PREFIX + old_id, UMO_PREFIX + new_id
        ro = conn.execute("select conversation_id,content,updated_at from conversations where user_id=?", (uo,)).fetchone()
        rn = conn.execute("select conversation_id,content,updated_at from conversations where user_id=?", (un,)).fetchone()
        if not ro:
            continue
        if rn:
            db_plan.append(("merge", uo, un, len(as_list(ro[1])), len(as_list(rn[1]))))
            print(f"   db {name}: 会话合并 {len(as_list(ro[1]))} + {len(as_list(rn[1]))} 条")
        else:
            db_plan.append(("rename", uo, un, len(as_list(ro[1])), 0))
            print(f"   db {name}: 会话迁移 {len(as_list(ro[1]))} 条 -> {un}")

    if not apply:
        conn.close()
        print("(预览，未写盘；加 --apply 生效)")
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, f"{DB}.bak-dupmerge-{ts}")
    shutil.copy2(CP, f"{CP}.bak-dupmerge-{ts}")
    print("backup:", os.path.basename(DB) + f".bak-dupmerge-{ts}", "/", os.path.basename(CP) + f".bak-dupmerge-{ts}")

    for kind, uo, un, n_old, n_new in db_plan:
        if kind == "merge":
            ro = conn.execute("select content,updated_at from conversations where user_id=?", (uo,)).fetchone()
            rn = conn.execute("select content,updated_at from conversations where user_id=?", (un,)).fetchone()
            merged = merge_history(as_list(ro[0]), as_list(rn[0]))
            conn.execute(
                "update conversations set content=?, updated_at=? where user_id=?",
                (json.dumps(merged, ensure_ascii=False), max(ro[1] or "", rn[1] or ""), un),
            )
            conn.execute("delete from conversations where user_id=?", (uo,))
            print(f"   db merged {uo} -> {un}: {len(merged)} msgs")
        else:
            conn.execute("update conversations set user_id=? where user_id=?", (un, uo))
            print(f"   db renamed {uo} -> {un}")
        conn.execute("delete from umo_aliases where umo=?", (uo,))
    try:
        for _old, new_id, name in PAIRS:
            conn.execute(
                "update umo_aliases set auto_name=? where umo=?",
                (name, UMO_PREFIX + new_id),
            )
    except sqlite3.Error as exc:
        print("umo_aliases 同步跳过:", exc)
    conn.commit()
    conn.close()

    # ---------- companions.json ----------
    for kind, old_id, new_id, name in plan:
        old_u = users.pop(old_id)
        if kind == "merge":
            stats = merge_profile(users[new_id], old_u)
            print(f"   json {name}: {json.dumps(stats, ensure_ascii=False)}")
        else:
            old_u["nickname"] = name
            old_u["last_display_name"] = old_u.get("last_display_name") or name
            users[new_id] = old_u
    for key in SHELLS:
        users.pop(key, None)
    data["users"] = users
    with io.open(CP, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)

    with io.open(CP, encoding="utf-8") as fh:
        chk = json.load(fh)
    chk_users = chk.get("users") or {}
    print("== 写盘完成：users =", len(chk_users))
    for key, u in sorted(chk_users.items()):
        print(
            f"   {key:42s} {str(u.get('nickname'))[:12]:14s} ep={len(episodes_of(u)):3d} "
            f"mem={len(memory_of(u)):3d} proactive={u.get('proactive_private_enabled')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
