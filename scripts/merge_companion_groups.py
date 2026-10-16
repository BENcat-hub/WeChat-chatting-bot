#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把历史遗留的「同群多份档案」并到稳定群 id 下。

背景
----
旧版 bridge 用 ``abs(hash(room)) % 2**31`` 派生 OneBot group_id，而 Python 的
``hash()`` 受 PYTHONHASHSEED 影响：bridge 每重启一次，同一个群就换一个新 id。
于是同一个群在 companions.json 里留下了多份互不相通的档案——历史库里有
``1000000016`` 和 ``1000000017`` 两份（成员表、最近消息、唤醒日志各存一半）。

现在群 id 由 ``stable_int("group", room)``（sha256 派生）稳定生成，本群是
``1000000015``。本脚本：

1. 把遗留群记录按时间顺序合并成一份，写进 ``groups[1000000015]``；
2. 把 companions.json 其它区段里指向旧群 id 的引用（值 **和键**）改写成稳定 id；
3. 把 memory_companion.db 各表文本列里的旧群 id 一并改写（display_name /
   aliases / profile / timeline.metadata / relationship_edges.object_name 等）。

用法
----
    python scripts/merge_companion_groups.py                  # 只预览（dry-run）
    python scripts/merge_companion_groups.py --apply          # 真正写入（先停 AstrBot）
    python scripts/merge_companion_groups.py --apply --force  # 跳过“AstrBot 已停止”检查

写入前会自动备份 companions.json + memory_companion.db 到
``logs/backup-merge-groups-<时间戳>/``，完整报告写到 ``logs/merge-groups-<时间戳>.txt``。

注意：合并后的记录里 ``alias_group_ids`` / ``umo_aliases`` 会保留旧 id 作为历史别名，
这是刻意的——插件靠它们认得出旧 UMO，不要再手工删掉。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import socket
import sqlite3
import sys
import time

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP = os.path.join(W, "astrbot", "data", "plugin_data",
                    "astrbot_plugin_private_companion", "companions.json")
MEMDB = os.path.join(W, "astrbot", "data", "plugin_data",
                     "astrbot_plugin_memory_companion", "memory_companion.db")
OB_MAP = os.path.join(W, "akasha-bridge", "ob_id_map.json")

CANONICAL = "1000000015"

_lines: list[str] = []


def log(msg: str = "") -> None:
    print(msg)
    _lines.append(msg)


def astrbot_listening():
    """AstrBot 的 dashboard / OneBot 端口只要有一个在听，就说明它还活着。"""
    for port in (6185, 11229):
        s = socket.socket()
        s.settimeout(0.4)
        try:
            s.connect(("127.0.0.1", port))
            return port
        except OSError:
            pass
        finally:
            s.close()
    return None


def _key(item) -> str:
    """列表去重用的稳定指纹。"""
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _is_empty(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def merge_record(target: dict, source: dict, path: str) -> None:
    """把 source（更新的那份）合进 target（更旧的那份）。

    字典递归合并；列表按 JSON 指纹去重后追加（target 在前 → 时间上仍是旧→新）；
    标量取 source 的非空值（source 是更新的记录，所以它代表最新状态）。
    """
    for k, v in source.items():
        here = "%s/%s" % (path, k)
        if k not in target:
            target[k] = copy.deepcopy(v)
            log("    + %s = %s" % (here, _brief(v)))
            continue
        cur = target[k]
        if isinstance(v, dict) and isinstance(cur, dict):
            merge_record(cur, v, here)
        elif isinstance(v, list) and isinstance(cur, list):
            seen = {_key(x) for x in cur}
            added = 0
            for item in v:
                f = _key(item)
                if f not in seen:
                    cur.append(copy.deepcopy(item))
                    seen.add(f)
                    added += 1
            if added:
                log("    ~ %s 追加 %d 条（%d → %d）" % (here, added, len(cur) - added, len(cur)))
        elif _is_empty(v):
            log("    . %s 保留 target（source 为空 %s）" % (here, _brief(v)))
        elif cur != v:
            log("    ~ %s %s → %s" % (here, _brief(cur), _brief(v)))
            target[k] = copy.deepcopy(v)


def _brief(v) -> str:
    if isinstance(v, (dict, list)):
        return "%s(len=%d)" % (type(v).__name__, len(v))
    s = str(v)
    return s if len(s) <= 70 else s[:67] + "..."


def rewrite_refs(node, legacy_ids, path, out_changes, depth=0):
    """把任意 JSON 子树里指向旧群 id 的字符串/键改写成稳定 id（原地改）。"""
    if isinstance(node, dict):
        for k in list(node.keys()):
            new_k = k
            for old in legacy_ids:
                if old in k:
                    new_k = new_k.replace(old, CANONICAL)
            v = node[k]
            if new_k != k:
                out_changes.append("%s/%s  (键) → %s" % (path, k, new_k))
                # 目标键已存在时并进去，避免覆盖
                if new_k in node:
                    if isinstance(node[new_k], dict) and isinstance(v, dict):
                        merge_record(node[new_k], v, path + "/" + new_k)
                    del node[k]
                else:
                    node[new_k] = node.pop(k)
                k = new_k
            rewrite_refs(node[k], legacy_ids, "%s/%s" % (path, k), out_changes, depth + 1)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            if isinstance(item, str):
                new_s = item
                for old in legacy_ids:
                    if old in new_s:
                        new_s = new_s.replace(old, CANONICAL)
                if new_s != item:
                    out_changes.append("%s[%d] %s → %s" % (path, i, _brief(item), _brief(new_s)))
                    node[i] = new_s
            else:
                rewrite_refs(item, legacy_ids, "%s[%d]" % (path, i), out_changes, depth + 1)
    elif isinstance(node, str):
        for old in legacy_ids:
            if old in node:
                out_changes.append("%s = %s" % (path, _brief(node)))
                return


def main() -> int:
    ap = argparse.ArgumentParser(description="合并历史遗留的群档案到稳定群 id")
    ap.add_argument("--apply", action="store_true", help="写回 companions.json 与 memory_companion.db")
    ap.add_argument("--force", action="store_true", help="AstrBot 在跑时也强制写（不推荐）")
    ap.add_argument("--canonical", default=CANONICAL, help="稳定群 id，默认 %s" % CANONICAL)
    args = ap.parse_args()
    canonical = str(args.canonical)

    with open(COMP, "rb") as fh:
        raw = fh.read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    doc = json.loads(raw.decode("utf-8-sig"))
    groups = doc.get("groups")
    if not isinstance(groups, dict):
        log("companions.json 里没有 groups 段，退出")
        return 1

    # ---- 找出「同一个群的遗留记录」：与稳定 id 同名的群记录 ----
    canonical_rec = groups.get(canonical) if isinstance(groups.get(canonical), dict) else {}
    try:
        with open(OB_MAP, encoding="utf-8") as fh:
            ob_map = json.load(fh)
    except Exception:
        ob_map = {}
    canonical_name = (canonical_rec.get("group_name") or canonical_rec.get("name")
                      or ob_map.get(canonical) or "")
    log("稳定群 id：%s（群名 %r）" % (canonical, canonical_name))

    legacy: list[str] = []
    for k, rec in groups.items():
        if str(k) == canonical or not isinstance(rec, dict):
            continue
        name = rec.get("group_name") or rec.get("name") or ""
        if canonical_name and name == canonical_name:
            legacy.append(str(k))
        elif str(k) in ob_map and ob_map.get(str(k)) == canonical_name:
            legacy.append(str(k))
    if not legacy:
        log("没有找到需要合并的遗留群记录（groups 现有键：%s）" % list(groups.keys()))
        return 0

    log("待合并的遗留群 id：%s" % legacy)

    # ---- 合并：以较旧的那份为骨架，把较新的那份合进来 ----
    records = sorted(
        [(float((groups[k].get("last_seen") or 0)), k) for k in legacy],
        key=lambda t: t[0],
    )
    log("\n[1/3] 合并群记录")
    for ts, k in records:
        log("  记录 %s：last_seen=%s message_count=%s" %
            (k, time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "-",
             groups[k].get("message_count")))
    base_ts, base_key = records[0]
    merged = copy.deepcopy(groups[base_key])
    log("  骨架 = %s（最早的活动记录）" % base_key)
    for ts, k in records[1:]:
        log("  合入 %s：" % k)
        merge_record(merged, groups[k], "groups/%s" % canonical)

    # 身份字段 + 历史别名
    platform = "default"
    old_umo = str(merged.get("umo") or "default:GroupMessage:%s" % base_key)
    if ":" in old_umo:
        platform = old_umo.split(":", 1)[0]
    merged["group_id"] = canonical
    merged["umo"] = "%s:GroupMessage:%s" % (platform, canonical)
    aliases = merged.get("alias_group_ids")
    if not isinstance(aliases, list):
        aliases = []
    for k in legacy:
        if k not in aliases:
            aliases.append(k)
    merged["alias_group_ids"] = aliases
    umo_aliases = merged.get("umo_aliases")
    if not isinstance(umo_aliases, list):
        umo_aliases = []
    for k in legacy:
        u = "%s:GroupMessage:%s" % (platform, k)
        if u not in umo_aliases:
            umo_aliases.append(u)
    merged["umo_aliases"] = umo_aliases
    names = merged.get("group_name_aliases")
    if not isinstance(names, list):
        names = []
    if canonical_name and canonical_name not in names:
        names.insert(0, canonical_name)
    merged["group_name_aliases"] = names
    log("  → groups[%s]：group_id=%s umo=%s alias_group_ids=%s"
        % (canonical, merged["group_id"], merged["umo"], merged["alias_group_ids"]))

    # ---- 其它区段里的旧 id 引用（跳过 groups，它已经处理完） ----
    log("\n[2/3] 改写 companions.json 其它区段里的旧群 id 引用")
    changes: list[str] = []
    for section, value in list(doc.items()):
        if section == "groups":
            continue
        rewrite_refs(value, legacy, "/" + section, changes)
    if changes:
        for c in changes:
            log("  ~ " + c)
    else:
        log("  （无）")

    # 用合并结果替换 groups
    doc["groups"] = {canonical: merged}

    # ---- memory_companion.db 文本列 ----
    log("\n[3/3] 改写 memory_companion.db 里的旧群 id 文本")
    db_changes: list[str] = []
    if os.path.exists(MEMDB):
        con = sqlite3.connect(MEMDB)
        try:
            tables = [r[0] for r in con.execute(
                "select name from sqlite_master where type='table'")]
            for t in tables:
                cols = [r[1] for r in con.execute("pragma table_info(%s)" % t)]
                for c in cols:
                    where = " or ".join(
                        "cast(%s as text) like '%%%s%%'" % (c, old) for old in legacy)
                    sql = ("select count(*) from %s where %s" % (t, where))
                    try:
                        n = con.execute(sql).fetchone()[0]
                    except sqlite3.Error:
                        continue
                    if not n:
                        continue
                    db_changes.append("%s.%s：%d 行" % (t, c, n))
                    if args.apply:
                        set_clause = ", ".join(
                            "%s = replace(%s, '%s', '%s')" % (c, c, old, canonical)
                            for old in legacy)
                        con.execute("update %s set %s where %s" % (t, set_clause, where))
            if args.apply:
                con.commit()
        finally:
            con.close()
        for c in db_changes:
            log("  ~ " + c)
        if not db_changes:
            log("  （无）")
    else:
        log("  找不到 memory_companion.db，跳过")

    if not args.apply:
        log("\n（dry-run，未写入；加 --apply 生效。注意：先停 AstrBot）")
        return 0

    busy = astrbot_listening()
    if busy and not args.force:
        log("\n已拒绝写入：AstrBot 还在监听端口 %d。先停 AstrBot（或加 --force）。" % busy)
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(W, "logs", "backup-merge-groups-" + stamp)
    os.makedirs(bak, exist_ok=True)
    shutil.copy2(COMP, os.path.join(bak, "companions.json"))
    if os.path.exists(MEMDB):
        shutil.copy2(MEMDB, os.path.join(bak, "memory_companion.db"))

    with open(COMP, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    log("\n已写入 %s（备份：%s，BOM 已移除=%s）" % (COMP, bak, had_bom))

    report = os.path.join(W, "logs", "merge-groups-%s.txt" % stamp)
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_lines) + "\n")
    log("报告：%s" % report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
