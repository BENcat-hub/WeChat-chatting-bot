# -*- coding: utf-8 -*-
"""校验「图片进重发队列」这一条路径：

AstrBot 发图时用的是临时文件，bridge 发完就删。如果图片发送失败直接入队，
队列里的 path 会指向已删除的文件，重发必然失败。所以入队前必须把图片复制进
队列自己的目录（akasha-bridge/wxui_queue_assets），送达/放弃后再删掉副本。

本脚本不驱动微信、不发送任何消息，只检查复制与清理：
  python scripts\\test_wxui_image_queue.py
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BRIDGE = os.path.join(ROOT, "akasha-bridge")
sys.path.insert(0, BRIDGE)

from wxui_sender import WxuiSender  # noqa: E402

QUEUE = os.path.join(tempfile.gettempdir(), "wxui-test-queue.jsonl")
fails = []


def check(name, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + name + (" —— " + str(detail) if detail else ""))
    if not cond:
        fails.append(name)


# 1) 造一个假图片（内容无所谓，只验证搬运）
src = os.path.join(tempfile.gettempdir(), "wxui-fake-%d.png" % int(time.time()))
with open(src, "wb") as fh:
    fh.write(b"\x89PNG\r\n\x1a\n" + b"0" * 16)

for p in (QUEUE, QUEUE + ".tmp"):
    if os.path.exists(p):
        os.unlink(p)

sender = WxuiSender(script=os.path.join(HERE, "wxui.py"), python_exe=sys.executable,
                    retry_queue_path=QUEUE, dry_run=True)  # dry_run=True → 不起队列线程

sender._enqueue("send-image", {"target": "文件传输助手", "path": src}, "文件传输助手",
                {"error": "screen not capturable", "before_local_id": 7, "retryable": True})

items = sender._load_queue()
check("入队 1 条", len(items) == 1, items)
queued_path = (items[0]["spec"]["path"] if items else "")
check("队列里的 path 与源文件不同", bool(queued_path) and os.path.abspath(queued_path) != os.path.abspath(src),
      queued_path)
check("副本已落盘", bool(queued_path) and os.path.exists(queued_path), queued_path)
check("副本在 wxui_queue_assets 目录内",
      bool(queued_path) and os.path.dirname(os.path.abspath(queued_path)) == os.path.abspath(sender.queue_asset_dir),
      sender.queue_asset_dir)
check("原图仍在（没有被搬走）", os.path.exists(src))
check("since_local_id 被带上", int(items[0].get("since_local_id") or -1) == 7, items[0].get("since_local_id"))

# 2) 源文件删掉后（模拟 bridge 清理临时文件），队列里的副本仍可重发
try:
    os.unlink(src)
except OSError:
    pass
check("源文件删除后副本仍可用", os.path.exists(queued_path))

# 3) 送达/放弃时清理副本
sender._drop_asset(items[0])
check("丢弃后副本被删除", not os.path.exists(queued_path))

if os.path.exists(QUEUE):
    os.unlink(QUEUE)
print(("\n全部通过" if not fails else "\n失败: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
