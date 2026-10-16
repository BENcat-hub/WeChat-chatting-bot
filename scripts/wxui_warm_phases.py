#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""常驻 worker 的「热路径」分阶段计时：一条消息的时间到底花在哪。

配合 wxui_warm_probe.py 使用（那个看端到端，这个看内部各阶段）。
只发 1 条文本，阶段：会话探测 → 打开/确认会话 → 清输入框 → 粘贴+回车 → 回读校验。

用法：<bridge 的 venv python> scripts\\wxui_warm_phases.py [联系人]
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wxui  # noqa: E402


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "文件传输助手"
    cfg = wxui.config()
    client = wxui.Client()
    spans = []

    def timer(name):
        t0 = time.time()
        return lambda extra="": spans.append((name, time.time() - t0, extra))

    try:
        stop = timer("sessions API (WeFlow)")
        rows = wxui.api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
        stop()
        talker = wxui.talker_for(cfg, target, rows)
        if not talker:
            print("找不到联系人 %s；附近有：%s"
                  % (target, [s.get("displayName") for s in rows[:15]]))
            return 1

        plan = {"target": target, "talker": talker, "kind": "text"}
        text = "【性能自测】热路径分阶段计时"

        stop = timer("wait_for_session")
        ok = wxui.wait_for_session(client, cfg, plan)
        stop("ok=%s" % ok)
        if not ok:
            return 1

        stop = timer("last_message (baseline)")
        since = (wxui.last_message(cfg, talker) or {}).get("localId") or 0
        stop("since=%s" % since)

        # 先热一次：让 _WARM 记住「窗口停在这个会话上」，接下来测的才是用户要的快路径
        stop = timer("open_chat 第1次（冷）")
        opened = wxui.open_chat(client, cfg, target, plan, do_ocr=True, assume_current=False)
        stop(plan.get("claimed") or "")

        stop = timer("open_chat 第2次（热）")
        opened2 = wxui.open_chat(client, cfg, target, plan, do_ocr=True, assume_current=True)
        stop(plan.get("claimed") or "")
        if not (opened and opened2):
            print("open_chat 失败：%s" % plan.get("error"))
            return 1

        stop = timer("clear_input")
        plan["input_clear"] = wxui.clear_input(client, cfg, plan.get("window_rect"))
        stop(str(plan["input_clear"])[:60])

        stop = timer("clipboard + ctrl+v")
        client.call("Clipboard", {"mode": "set", "text": text})
        client.call("Shortcut", {"shortcut": "ctrl+v"})
        stop()

        stop = timer("sleep 0.5 + enter")
        time.sleep(0.5)
        client.call("Shortcut", {"shortcut": "enter"})
        stop()

        stop = timer("verify 回读")
        found = wxui.verify(cfg, talker, text, time.time() + 12, since)
        stop("verified=%s localId=%s" % (bool(found), (found or {}).get("localId")))
    finally:
        try:
            client.close()
        except Exception:
            pass

    total = sum(s for _, s, _ in spans)
    print("目标=%s（窗口=%s）" % (target, plan.get("window_rect")))
    for name, sec, extra in spans:
        print("  %-26s %6.2fs  %s" % (name, sec, extra))
    print("  合计（不含 WeFlow 会话列表以外的开销） %.2fs" % total)

    # 单次 Windows-MCP 调用的基准延迟：决定还能从哪里再省
    x, y = wxui.input_box_point(cfg, plan.get("window_rect") or [0, 0, 1, 1])
    client = wxui.Client()
    try:
        calls = [
            ("Snapshot", lambda: client.call("Snapshot", {})),
            ("Click(输入框)", lambda: client.call("Click", {"loc": [x, y], "clicks": 1})),
            ("Shortcut ctrl+a", lambda: client.call("Shortcut", {"shortcut": "ctrl+a"})),
            ("Shortcut delete", lambda: client.call("Shortcut", {"shortcut": "delete"})),
            ("Clipboard set", lambda: client.call("Clipboard", {"mode": "set", "text": "bench"})),
        ]
        print("  单次调用基准：")
        for label, fn in calls:
            t0 = time.time()
            try:
                fn()
                print("    %-18s %5.2fs" % (label, time.time() - t0))
            except Exception as e:
                print("    %-18s 失败: %s" % (label, e))
    finally:
        try:
            client.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
