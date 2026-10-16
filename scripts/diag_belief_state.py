# -*- coding: utf-8 -*-
"""只读：核对 别名表 / 主动候选池 / 桃桃的错误体育课信念残留 / KG 节点。

用法：python scripts/diag_belief_state.py
"""
import json
import os
import sqlite3
import sys

if len(sys.argv) > 1:
    sys.stdout = open(sys.argv[1], "w", encoding="utf-8")

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(W, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json")
COMP = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion", "companions.json")
MEMDB = os.path.join(W, "astrbot", "data", "plugin_data", "astrbot_plugin_memory_companion", "memory_companion.db")
TAOTAO = "1000000003"


def walk(node, key, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            walk(v, key, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, key, out)


def main():
    cfg = json.load(open(CFG, encoding="utf-8-sig"))
    hits = []
    walk(cfg, "private_user_aliases", hits)
    for i, h in enumerate(hits):
        print("[private_user_aliases #%d] %s" % (i, json.dumps(h, ensure_ascii=False)[:2000]))
    hits2 = []
    walk(cfg, "target_user_ids", hits2)
    print("[target_user_ids] %s" % json.dumps(hits2, ensure_ascii=False)[:500])

    comp = json.load(open(COMP, encoding="utf-8-sig"))
    pool = comp.get("proactive_candidate_pool") or []
    print("\n=== proactive_candidate_pool: %d 条 ===" % len(pool))
    for i, e in enumerate(pool):
        if not isinstance(e, dict):
            print("  [%d] %s" % (i, type(e).__name__))
            continue
        print("  [%d] uid=%s status=%s kind=%s" % (i, e.get("user_id"), e.get("status"), e.get("kind") or e.get("topic_kind")))
        print("      keys=%s" % ",".join(sorted(e.keys())))
        print("      topic=%r" % (str(e.get("topic"))[:80],))

    u = comp["users"][TAOTAO]
    for field in ("recent_proactive_topics", "action_consequences", "recent_proactive_afterglows"):
        arr = u.get(field) or []
        print("\n=== 桃桃 %s: %d 条 ===" % (field, len(arr)))
        for i, e in enumerate(arr):
            txt = e.get("text") if isinstance(e, dict) else e
            print("   [%d] %r" % (i, str(txt)[:80]))
    print("\n=== 桃桃 state_continuity ===")
    sc = u.get("state_continuity") or {}
    print("   keys=%s" % ",".join(sorted(sc.keys())))
    print("   last_action_text=%r" % (str(sc.get("last_action_text"))[:80],))

    con = sqlite3.connect("file:%s?mode=ro" % MEMDB, uri=True)
    print("\n=== knowledge_nodes 含「体育」 ===")
    rows = list(con.execute("select id,node_type,node_key,label,session_id from knowledge_nodes where label like '%体育%'"))
    for r in rows:
        print("   %s | %s | %s | %s | %s" % r)
    print("=== knowledge_edges 引用上述节点 ===")
    for r in rows:
        for e in con.execute("select id,source_node_id,target_node_id,relation_type from knowledge_edges where source_node_id=? or target_node_id=?", (r[0], r[0])):
            print("   %s" % (e,))
    print("=== memories 旧 session 行 ===")
    for r in con.execute("select id,subject_id,session_id,substr(content,1,50) from memories where session_id in ('1000000004','1000000011','1000000002')"):
        print("   %s | %s | %s | %s" % r)
    print("=== knowledge_nodes 旧 session 行 ===")
    for r in con.execute("select id,label,session_id from knowledge_nodes where session_id in ('1000000004','1000000011','1000000002')"):
        print("   %s | %s | %s" % r)


if __name__ == "__main__":
    main()
