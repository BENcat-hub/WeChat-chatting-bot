# -*- coding: utf-8 -*-
"""演示第二步：把按语义挑出来的表情包通过 bridge 真正发到「文件传输助手」，并用微信数据库回读校验。"""
import json, os, sys, time, urllib.error, urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "astrbot", "data", "plugin_data", "meme_manager", "packs",
                   "atri-meme-pack", "memes", "tired", "0048_1368B7386C31A5D732AF1A4A95449125.png")
TARGET = "文件传输助手"
TOKEN = open(os.path.join(ROOT, "secrets", "weflow-token.txt"), encoding="utf-8").read().strip()


def post(url, payload, headers=None, timeout=300):
    r = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return f.status, f.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get(url, headers=None, timeout=60):
    r = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return f.status, f.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


print("图片:", os.path.basename(IMG), "|", os.path.getsize(IMG), "B | 存在:", os.path.exists(IMG))
st, b = post("http://127.0.0.1:8766/send_image", {"target": TARGET, "path": IMG})
print("[bridge /send_image]", st, b[:300])

time.sleep(4)
st, b = get("http://127.0.0.1:5031/api/v1/messages?talker=filehelper&limit=5",
            {"Authorization": "Bearer " + TOKEN})
print("\n[WeFlow 回读 filehelper 最近消息]", st)
try:
    d = json.loads(b)
    msgs = d.get("data") or d.get("messages") or d
    if isinstance(msgs, dict):
        msgs = msgs.get("messages") or msgs.get("list") or []
    for m in (msgs or [])[-5:]:
        if isinstance(m, dict):
            print("  localId=%s isSend=%s type=%s time=%s content=%s" % (
                m.get("localId") or m.get("local_id"), m.get("isSend"),
                m.get("type") or m.get("msgType"), m.get("createTime") or m.get("timestamp"),
                str(m.get("content") or m.get("text") or m.get("parsedContent") or "")[:70].replace("\n", " ")))
        else:
            print("  ", str(m)[:150])
except Exception as e:
    print("  解析失败:", e, b[:400])
