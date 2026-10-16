# -*- coding: utf-8 -*-
"""重试：PUT /api/v1/system-config 直接传 config 本体（无 config 包裹），打开分段回复。"""
import json, os, shutil, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import jwt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "astrbot", "data", "cmd_config.json")
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
shutil.copy(CFG, os.path.join(LOGS, f"cmd_config.backup-{ts}.json"))

st, b = req("GET", "/api/v1/system-config")
full = json.loads(b)["data"]["config"]
sr = full["platform_settings"]["segmented_reply"]
sr.update(
    {
        "enable": True,
        "only_llm_result": True,
        "interval_method": "random",
        "interval": "0.8,2.0",
        "words_count_threshold": 150,
        "split_mode": "regex",
        "regex": ".*?[。？！~…]+|.+$",
        "content_cleanup_rule": "",
    }
)
print("[before PUT] enable =", sr["enable"])

st, b = req("PUT", "/api/v1/system-config", full)
print("[PUT raw config]", st, b[:300])

# 校验：内存态 + 磁盘
st, b = req("GET", "/api/v1/system-config")
print("[GET after ]", json.loads(b)["data"]["config"]["platform_settings"]["segmented_reply"])
print("[disk after]", json.load(open(CFG, encoding="utf-8-sig"))["platform_settings"]["segmented_reply"])
print("[disk mtime]", time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(CFG))))
