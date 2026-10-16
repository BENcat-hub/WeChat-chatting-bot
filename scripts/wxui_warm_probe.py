#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""常驻 worker 提速自测：给同一个联系人连发 N 条，逐条计时。

2026-10-16 加入，配合 wxui.py worker / WxuiSender(warm_worker=True)：
  * 第 1 条要付 windows-mcp 启动 + 会话/窗口验证（跟过去一样）；
  * 第 2 条起复用同一个 Windows-MCP 客户端，且「窗口已经停在目标会话上」这件事也是已知的，
    所以跳过侧边栏 OCR 与点击，直接清空输入框→粘贴→回车→回读校验。

用法（默认发给「文件传输助手」，3 条）：
    <bridge 的 venv python> scripts\\wxui_warm_probe.py [联系人] [条数]

只发文本，内容带【性能自测】前缀，方便事后在微信里搜出来删掉。
"""

import io
import json
import os
import sys
import time

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(STACK, "akasha-bridge")
sys.path.insert(0, BRIDGE)

import wxui_sender as mod  # noqa: E402
from wxui_sender import WxuiSender  # noqa: E402


def bridge_config():
    path = os.path.join(BRIDGE, "config.json")
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    cfg = bridge_config()
    target = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    sender = WxuiSender(
        script=cfg.get("wxui_script") or os.path.join(STACK, "scripts", "wxui.py"),
        python_exe=cfg.get("wxui_python") or sys.executable,
        wxui_timeout=float(cfg.get("wxui_timeout") or 90),
        retry_queue_path=os.path.join(BRIDGE, "wxui_probe_pending.jsonl"),
        queue_interval=3600.0,
        queue_max_attempts=1,
        group_gather_seconds=float(cfg.get("wxui_group_gather_seconds") or 1.2),
        warm_worker=True,
        whitelist=[],
        blacklist=[],
        min_interval_contact=0.0,   # 自测要看清 UI 路径本身的开销，不叠加限频
        min_interval_global=0.0,
        dry_run=False,
    )
    print("目标=%s 条数=%d 脚本=%s" % (target, count, sender.script))
    rows = []
    try:
        for i in range(1, count + 1):
            text = "【性能自测】常驻worker提速验证 %d/%d" % (i, count)
            t0 = time.time()
            ok, data = sender._scheduled_run("send", {"target": target, "text": text}, target)
            dt = time.time() - t0
            rows.append((i, dt, ok, data))
            print("  第 %d 条 %5.2fs  送达=%s  路径=%s  尝试=%s  错误=%s"
                  % (i, dt, data.get("verified"), data.get("claimed"),
                     data.get("attempts"), data.get("error") or "-"))
            if not ok:
                print("  ↳ 已知联系人样例: %s" % (data.get("known") or data.get("talker")))
                break
    finally:
        alive = bool(sender._worker is not None and sender._worker.poll() is None)
        print("worker 存活=%s 已处理请求=%d" % (alive, sender._worker_seq))
        sender.stop()

    if len(rows) >= 2:
        first, rest = rows[0][1], [r[1] for r in rows[1:]]
        avg = sum(rest) / len(rest)
        print("第 1 条（冷）=%.2fs，后续（热）平均=%.2fs，单条省下 %.2fs（%.0f%%）"
              % (first, avg, first - avg, (1 - avg / first) * 100 if first else 0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
