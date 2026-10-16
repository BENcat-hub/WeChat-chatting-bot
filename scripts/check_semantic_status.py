# -*- coding: utf-8 -*-
"""定时巡检：表情包语义化任务进度 + 链路各服务端口。"""
import json, os, socket, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import jwt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://127.0.0.1:6185"

cfg = json.load(open(os.path.join(ROOT, "astrbot", "data", "cmd_config.json"), encoding="utf-8-sig"))
tok = jwt.encode({"username": "astrbot", "exp": int(time.time()) + 1800},
                 cfg["dashboard"]["jwt_secret"], algorithm="HS256")
tok = tok.decode() if isinstance(tok, bytes) else tok


def api(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return f.status, json.loads(f.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)


print("时间:", time.strftime("%Y-%m-%d %H:%M:%S"))
for port, name in ((8766, "bridge 面板"), (11229, "AstrBot OneBot WS"), (6185, "AstrBot 面板"), (5031, "WeFlow API")):
    s = socket.socket()
    s.settimeout(1.5)
    ok = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    print(f"  端口 {port:<6} {name:<18} {'✅ listening' if ok else '❌ 无监听'}")

st, d = api("GET", "/api/plug/meme_manager/semantic/status")
print("\n[GET semantic/status]", st)
data = d.get("data", d) if isinstance(d, dict) else d
if isinstance(data, dict):
    for k in ("task_status", "task_phase", "pending", "processed", "failed", "index_ready", "total",
              "pack_id", "progress", "status_message", "last_error", "started_at", "finished_at"):
        if k in data:
            v = data[k]
            print(f"  {k:<15} = {str(v)[:200]}")
    extra = {k: v for k, v in data.items() if k not in
             ("task_status", "task_phase", "pending", "processed", "failed", "index_ready", "total",
              "pack_id", "progress", "status_message", "last_error", "started_at", "finished_at")}
    if extra:
        print("  其它:", json.dumps(extra, ensure_ascii=False)[:600])
else:
    print(" ", str(data)[:600])
