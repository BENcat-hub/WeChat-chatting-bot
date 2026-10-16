# -*- coding: utf-8 -*-
"""清掉「用户B」档案里被串进去的体育课错误信念（用户A的日程串到了她名下）。

背景（2026-10-15 排查结论）：
  会话原文归属没错——微信里 07:51 说「今天上午10:00提醒我去上体育课[爱心]」的是用户A
  (wxid_CONTACT_B, umo default:FriendMessage:1000000001)，用户B
  (wxid_CONTACT_C, umo default:FriendMessage:1000000003) 从来没说过体育课，
  她只纠正过「我上的不是体育课啊」「用户A上的才是体育课」。
  但主动消息引擎在桃桃名下留下了一条假信念，并据此在 11:55 问她「体育课上完了吧 中午吃了没」。

本脚本只动「桃桃」的 scope，且只删/清含关键词的条目：
  1) companions.json
     users["1000000003"] 里下列列表字段中含「体育」的条目 → 删除
       recent_proactive_topics / action_consequences / recent_proactive_afterglows /
       recent_proactive_hesitations / proactive_impulses
     state_continuity.last_action_text 含「体育」→ 置空
     （保留 dialogue_episodes[2] 与 avoid_next[0]：「别再猜或提桃桃上的是体育课」是纠错教训；
       保留 expression_profile 里她本人说的那两句）
  2) memory_companion.db
     knowledge_nodes：session_id=default:FriendMessage:1000000003 且 label 含「体育」→
       删除节点 + knowledge_label_trgm + 相关 knowledge_edges
       （用户A session default:FriendMessage:1000000001 下的体育课节点不动）
     memories：桃桃 scope（session_id/subject_id/object_id 命中 1000000003 或旧 id 1000000004）
       且 content 同时含「体育课」与「主动」→ 删除（这类是「Bot 主动向桃桃发送：体育课…」的假信念）；
       桃桃自己那晚的复盘/情节记忆只在报告里列出，不删

默认 dry-run，只打印；`--apply` 才写入（会先备份到 logs/backup-<stamp>/）。
写 companions.json 用 UTF-8 无 BOM（BOM 会让插件解析失败，见既有踩坑记录）。
AstrBot 在跑时拒绝写入（避免它的内存状态覆盖回去），除非加 `--force`。

用法：
  python scripts/clean_pe_belief.py            # dry-run
  python scripts/clean_pe_belief.py --apply    # 需先停 AstrBot
"""
import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
import time

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMP = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json")
MEMDB = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db")

TARGET_UID = "1000000003"          # 用户B（规范 id）
TARGET_SESSION = "default:FriendMessage:1000000003"
TARGET_LEGACY = ("1000000004", "1000000005")
KEYWORD = "体育"
LIST_FIELDS = (
    "recent_proactive_topics",
    "action_consequences",
    "recent_proactive_afterglows",
    "recent_proactive_hesitations",
    "proactive_impulses",
)


def log(msg=""):
    print(msg)


def astrbot_listening():
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


def entry_text(entry):
    if isinstance(entry, dict):
        parts = []
        for k in ("text", "topic", "signature", "motive", "note", "reply_text"):
            if entry.get(k):
                parts.append(str(entry[k]))
        return " ".join(parts)
    return str(entry)


# 带纠错信息的条目保留：它们编码的是「桃桃没有体育课」这个事实，删掉反而丢教训。
KEEP_MARKERS = ("不是体育课", "用户A")


def is_corrective(entry):
    if isinstance(entry, dict):
        for k, v in entry.items():
            if isinstance(v, str) and any(m in v for m in KEEP_MARKERS):
                return True
    elif isinstance(entry, str):
        return any(m in entry for m in KEEP_MARKERS)
    return False


def clean_companions(apply):
    with open(COMP, "rb") as fh:
        raw = fh.read()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    doc = json.loads(raw.decode("utf-8-sig"))
    user = doc.get("users", {}).get(TARGET_UID)
    if user is None:
        log("[companions] 找不到 users[%s]，跳过" % TARGET_UID)
        return []

    removed = []
    kept_corrective = []
    for field in LIST_FIELDS:
        arr = user.get(field)
        if not isinstance(arr, list):
            continue
        keep = []
        for i, e in enumerate(arr):
            if KEYWORD in entry_text(e) and not is_corrective(e):
                removed.append("%s[%d] %r" % (field, i, entry_text(e)[:70]))
            else:
                if KEYWORD in entry_text(e):
                    kept_corrective.append("%s[%d] %r" % (field, i, entry_text(e)[:70]))
                keep.append(e)
        if len(keep) != len(arr):
            user[field] = keep

    sc = user.get("state_continuity")
    if isinstance(sc, dict):
        for k in ("last_action_text",):
            v = sc.get(k)
            if isinstance(v, str) and KEYWORD in v:
                removed.append("state_continuity.%s = %r -> \"\"" % (k, v[:70]))
                sc[k] = ""

    log("[companions] 待删/待清 %d 处（BOM=%s）：" % (len(removed), had_bom))
    for r in removed:
        log("   - %s" % r)
    log("[companions] 保留的纠错条目 %d 处：" % len(kept_corrective))
    for r in kept_corrective:
        log("   = %s" % r)
    if apply and removed:
        backup = os.path.join(W, "logs", "backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(backup, exist_ok=True)
        shutil.copy2(COMP, os.path.join(backup, os.path.basename(COMP)))
        tmp = COMP + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, COMP)
        with open(COMP, "rb") as fh:
            head = fh.read(3)
        log("[companions] 已写入，前 3 字节 = %s（应为 7b 0d 0a 或 7b 0a）" % head.hex(" "))
    return removed


def clean_db(apply):
    con = sqlite3.connect(MEMDB)
    con.execute("PRAGMA foreign_keys=ON")
    todo_nodes, todo_edges, todo_mem, keep_mem = [], [], [], []

    rows = list(con.execute(
        "select id,node_type,label from knowledge_nodes where session_id=? and label like ?",
        (TARGET_SESSION, "%" + KEYWORD + "%")))
    node_ids = {r[0] for r in rows}
    for r in rows:
        todo_nodes.append("%s | %s | %s" % (r[0], r[1], r[2][:60]))
    for nid in sorted(node_ids):
        for e in con.execute(
                "select id,source_node_id,target_node_id,relation_type from knowledge_edges "
                "where source_node_id=? or target_node_id=?", (nid, nid)):
            todo_edges.append("%s | %s -> %s (%s)" % e)

    scope = (TARGET_UID, TARGET_SESSION, "FriendMessage:" + TARGET_UID,
             TARGET_UID, TARGET_LEGACY[0], TARGET_LEGACY[1])
    for r in con.execute(
            "select id,subject_id,session_id,substr(content,1,70) from memories "
            "where content like ? and (subject_id in (?,?,?) or session_id in (?,?))",
            ("%" + KEYWORD + "课%", TARGET_UID, TARGET_LEGACY[0], TARGET_LEGACY[1],
             TARGET_SESSION, "FriendMessage:" + TARGET_UID)):
        mid, subj, sess, content = r
        if "主动" in content:
            todo_mem.append("%s | subj=%s sess=%s | %s" % r)
        else:
            keep_mem.append("%s | subj=%s sess=%s | %s" % r)

    log("\n[db] knowledge_nodes 待删 %d 个：" % len(todo_nodes))
    for r in todo_nodes:
        log("   - %s" % r)
    log("[db] knowledge_edges 待删 %d 条：" % len(todo_edges))
    for r in todo_edges:
        log("   - %s" % r)
    log("[db] memories 待删（含「主动」的假信念）%d 条：" % len(todo_mem))
    for r in todo_mem:
        log("   - %s" % r)
    log("[db] memories 保留（她的情节记忆，仅报告）%d 条：" % len(keep_mem))
    for r in keep_mem:
        log("   - %s" % r)

    if apply and (todo_nodes or todo_edges or todo_mem):
        backup = os.path.join(W, "logs", "backup-%s" % time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(backup, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            src = MEMDB + suffix
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(backup, os.path.basename(src)))
        con.execute("begin")
        for nid in sorted(node_ids):
            con.execute("delete from knowledge_edges where source_node_id=? or target_node_id=?", (nid, nid))
            con.execute("delete from knowledge_label_trgm where node_id=?", (nid,))
            con.execute("delete from knowledge_nodes where id=?", (nid,))
        for line in todo_mem:
            con.execute("delete from memories where id=?", (line.split(" | ")[0],))
        con.commit()
        log("[db] 已提交，备份目录 %s" % backup)
    con.close()
    return todo_nodes, todo_edges, todo_mem, keep_mem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--force", action="store_true", help="AstrBot 在跑时也强制写")
    ap.add_argument("--out", default=None, help="把报告写到这个文件（UTF-8）")
    args = ap.parse_args()

    if args.out:
        sys.stdout = open(args.out, "w", encoding="utf-8")

    port = astrbot_listening()
    if port:
        log("!! AstrBot 正在监听 127.0.0.1:%d —— 它的内存状态可能把改动覆盖回去。" % port)
        if args.apply and not args.force:
            log("已拒绝写入：请先停 AstrBot，或加 --force。")
            return 2

    log("模式：%s\n" % ("APPLY" if args.apply else "dry-run"))
    clean_companions(args.apply)
    clean_db(args.apply)
    log("\n完成（%s）。" % ("已写入" if args.apply else "未写入，加 --apply 生效；注意先停 AstrBot"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
