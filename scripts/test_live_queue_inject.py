# -*- coding: utf-8 -*-
"""把两条测试消息塞进 bridge 的重发队列，用来验证**正在运行的 bridge 进程**里的
队列线程真的会干活（不是只在单元测试里）。

用法（bridge 必须在跑）：
    python scripts\\test_live_queue_inject.py [已送达的文本]

它会塞两条：
  1. 一条「其实早就发出去了」的消息（since_local_id=0）→ 期望队列回读后**丢弃不重发**；
  2. 一条从没发过的消息（带时间戳）→ 期望队列**真的补发出去**并回读验证通过。
随后自己观察 akasha-bridge\\bridge.log，或等 20–60 秒后看队列文件是否清空。
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUEUE = os.path.join(ROOT, "akasha-bridge", "wxui_pending.jsonl")


def last_sent_text(talker="filehelper"):
    """最近一条「我发出的」文本消息（用来造一条确定已送达的队列项）。

    命令行传中文参数会被 Windows 的 ANSI 代码页毁掉，所以这里直接从 WeFlow 取原文。
    """
    token_file = os.path.join(ROOT, "secrets", "weflow-token.txt")
    with open(token_file, encoding="utf-8") as fh:
        token = fh.read().strip()
    url = "http://127.0.0.1:5031/api/v1/messages?" + urllib.parse.urlencode(
        {"talker": talker, "limit": 30, "access_token": token})
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for m in data.get("messages") or []:
        if m.get("isSend") in (1, True) and (m.get("content") or "").strip():
            return m["content"].strip()
    return ""


target = "文件传输助手"
delivered_text = sys.argv[1] if len(sys.argv) > 1 else last_sent_text()
fresh_text = "队列补发自检 %s" % time.strftime("%H:%M:%S")

if not delivered_text:
    raise SystemExit("拿不到一条已送达的文本，无法构造队列测试")

now = time.time()
items = [
    {"kind": "send", "spec": {"target": target, "text": delivered_text}, "target": target,
     "created": now, "attempts": 0, "since_local_id": 0, "last_error": "live-queue-test"},
    {"kind": "send", "spec": {"target": target, "text": fresh_text}, "target": target,
     "created": now, "attempts": 0, "since_local_id": 0, "last_error": "live-queue-test"},
]

with open(QUEUE, "a", encoding="utf-8") as fh:
    for it in items:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")

print("已写入 %d 条 → %s" % (len(items), QUEUE))
print("  1) 已送达（应被丢弃）: %s" % delivered_text)
print("  2) 未送达（应被补发）: %s" % fresh_text)
print("等待队列线程处理（间隔 20 秒）…")
