# -*- coding: utf-8 -*-
"""一次性脚本：写入新人设 + 打开分段回复。只走面板 API，不重启 AstrBot。"""
import json, os, shutil, sqlite3, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import jwt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "astrbot", "data", "cmd_config.json")
DB = os.path.join(ROOT, "astrbot", "data", "data_v4.db")
LOGS = os.path.join(ROOT, "logs")
BASE = "http://127.0.0.1:6185"

cfg = json.load(open(CFG, encoding="utf-8-sig"))
tok = jwt.encode(
    {"username": "astrbot", "exp": int(time.time()) + 3600},
    cfg["dashboard"]["jwt_secret"],
    algorithm="HS256",
)
tok = tok.decode() if isinstance(tok, bytes) else tok


def req(method, path, body=None, timeout=90):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return f.status, f.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


ts = time.strftime("%Y%m%d-%H%M%S")

# ---------- 备份 ----------
shutil.copy(CFG, os.path.join(LOGS, f"cmd_config.backup-{ts}.json"))
con = sqlite3.connect(DB)
old = con.execute("select system_prompt from personas where persona_id='1'").fetchone()
old = old[0] if old else ""
open(os.path.join(LOGS, f"persona.backup-{ts}.txt"), "w", encoding="utf-8").write(old)
print(f"[backup] cmd_config + persona({len(old)} chars) -> logs/*{ts}*")

# ---------- 1. 新人设 ----------
new_persona = open(os.path.join(ROOT, "logs", "new_persona.zh.txt"), encoding="utf-8").read()
st, b = req("PUT", "/api/v1/personas/1", {"system_prompt": new_persona})
print("[persona PUT]", st, b[:200])

con = sqlite3.connect(DB)
row = con.execute("select length(system_prompt) from personas where persona_id='1'").fetchone()
print("[persona DB] length =", row[0] if row else None)

# ---------- 2. 打开分段回复 ----------
st, b = req("GET", "/api/v1/system-config")
full = json.loads(b)["data"]["config"]
sr = full["platform_settings"]["segmented_reply"]
print("[segmented before]", json.dumps(sr, ensure_ascii=False))
sr["enable"] = True
sr["only_llm_result"] = True
sr["interval_method"] = "random"
sr["interval"] = "0.8,2.0"
sr["words_count_threshold"] = 150
sr["split_mode"] = "regex"
sr["regex"] = ".*?[。？！~…]+|.+$"
sr["content_cleanup_rule"] = ""
st, b = req("PUT", "/api/v1/system-config", {"config": full})
print("[system-config PUT]", st, b[:300])

st, b = req("GET", "/api/v1/system-config")
sr2 = json.loads(b)["data"]["config"]["platform_settings"]["segmented_reply"]
print("[segmented after ]", json.dumps(sr2, ensure_ascii=False))
print("[file on disk ]", json.load(open(CFG, encoding="utf-8-sig"))["platform_settings"]["segmented_reply"])
