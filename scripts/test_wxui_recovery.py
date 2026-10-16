# -*- coding: utf-8 -*-
"""真机自检：wxui 的失败恢复能力（正常发送 / 输入框残留 / 中断后重发不重复 / 重发队列）。

只发到「文件传输助手」，每条消息带唯一后缀，最后用 WeFlow 回读微信数据库判定。
用法：<bridge venv python> scripts/test_wxui_recovery.py
"""
import copy
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "akasha-bridge"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import wxui  # noqa: E402

TARGET = "文件传输助手"
STAMP = time.strftime("%H%M%S")
RESULT = []


def messages(cfg, talker, limit=30):
    return wxui.api(cfg, "/api/v1/messages", talker=talker, limit=limit).get("messages", [])


def count_text(cfg, talker, text):
    key = wxui.norm(text)
    return sum(1 for m in messages(cfg, talker)
               if m.get("isSend") == 1 and key in wxui.norm(m.get("content")))


def record(name, ok, detail):
    RESULT.append({"test": name, "ok": bool(ok), "detail": detail})
    print("[%s] %s —— %s" % ("PASS" if ok else "FAIL", name, json.dumps(detail, ensure_ascii=False)))


def main():
    cfg = wxui.config()
    talker = wxui.talker_for(cfg, TARGET, wxui.api(cfg, "/api/v1/sessions", limit=200).get("sessions", []))
    print("talker=%s | 配置: send_attempts=%s session_wait_s=%s clear_input_box=%s"
          % (talker, cfg.get("send_attempts"), cfg.get("session_wait_s"), cfg.get("clear_input_box")))

    # ---------- A. 正常发送（含清空输入框 + 回读校验） ----------
    text_a = "[自检A] 正常发送 %s" % STAMP
    plan = wxui.send(cfg, TARGET, text_a, commit=True)
    record("A 正常发送", plan.get("ok") and plan.get("verified"),
           {"attempts": plan.get("attempts"), "localId": plan.get("localId"),
            "input_clear": plan.get("input_clear"), "error": plan.get("error"),
            "session_waited_s": plan.get("session_waited_s")})

    # ---------- B. 输入框里有残留文本时，只发出新消息 ----------
    leftover = "[自检B] 残留不该被发出 %s" % STAMP
    text_b = "[自检B] 正式消息 %s" % STAMP
    client = wxui.Client()
    try:
        hwnd = wxui.find_wechat(cfg)
        wxui.focus(hwnd)
        rect = wxui.restored_rect(hwnd)
        wxui.clear_input(client, cfg, rect)                 # 先清干净
        client.call("Clipboard", {"mode": "set", "text": leftover})
        client.call("Shortcut", {"shortcut": "ctrl+v"})      # 只粘贴，不按回车 → 留在输入框
        time.sleep(0.5)
    finally:
        client.close()
    plan_b = wxui.send(cfg, TARGET, text_b, commit=True)
    time.sleep(1.0)
    n_left = count_text(cfg, talker, leftover)
    n_b = count_text(cfg, talker, text_b)
    record("B 残留被清掉、只发新消息", plan_b.get("ok") and n_left == 0 and n_b == 1,
           {"verified": plan_b.get("verified"), "attempts": plan_b.get("attempts"),
            "残留命中条数": n_left, "新消息条数": n_b, "error": plan_b.get("error")})

    # ---------- C. 第一轮回读没读到 → 重发前先回读校验 → 只到一条 ----------
    # 真机上数据库写得太快，缩短 verify_timeout 也未必能造出"读不到"，所以在测试里
    # 明确模拟一次回读失败（RDP 抖动、数据库写入慢时就是这个样子）。
    text_c = "[自检C] 中断后重发不重复 %s" % STAMP
    real_verify = wxui.verify
    calls = {"n": 0}

    def flaky_verify(cfg_, talker_, text_, deadline, since_local_id=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return None                      # 第一轮：假装回读失败
        return real_verify(cfg_, talker_, text_, deadline, since_local_id)

    wxui.verify = flaky_verify
    try:
        plan_c = wxui.send(cfg, TARGET, text_c, commit=True)
    finally:
        wxui.verify = real_verify
    time.sleep(1.0)
    n_c = count_text(cfg, talker, text_c)
    record("C 回读失败后重发且不重复",
           plan_c.get("ok") and n_c == 1 and int(plan_c.get("attempts") or 0) >= 2
           and any(e.get("stage") == "precheck_delivered" for e in plan_c.get("attempt_log") or []),
           {"attempts": plan_c.get("attempts"), "attempt_log": plan_c.get("attempt_log"),
            "数据库条数": n_c, "verify调用次数": calls["n"], "error": plan_c.get("error")})

    # ---------- D. 会话探测（窗口/最小化/RDP 截屏） ----------
    client = wxui.Client()
    try:
        hwnd = wxui.find_wechat(cfg)
        rect = wxui.restored_rect(hwnd)
        ok_d, reason_d = wxui.screen_capturable(client, cfg, rect)
    finally:
        client.close()
    record("D 会话可用性探测", ok_d, {"reason": reason_d, "rect": list(rect) if rect else None})

    # ---------- E. 重发队列：已送达的丢弃、没送达的重发 ----------
    from wxui_sender import WxuiSender
    qpath = os.path.join(tempfile.gettempdir(), "wxui_pending_test.jsonl")
    if os.path.exists(qpath):
        os.unlink(qpath)
    sender = WxuiSender(
        script=os.path.join(HERE, "wxui.py"),
        python_exe=sys.executable,
        wxui_timeout=120,
        retry_queue_path=qpath,
        queue_interval=3600,          # 关掉后台线程的自动触发，手动 drain
        queue_max_attempts=3,
        whitelist=[], blacklist=[], dry_run=False,
    )
    before = messages(cfg, talker, limit=1)
    since = (before[-1] or {}).get("localId") if before else 0
    # E1：把"其实已经送达"的 A 消息塞进队列 → 应被丢弃，不重发
    with open(qpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "send", "spec": {"target": TARGET, "text": text_a},
                             "target": TARGET, "created": time.time(), "attempts": 0,
                             "since_local_id": 0, "last_error": "not read back"}, ensure_ascii=False) + "\n")
    # E2：把一条从没发过的消息塞进队列 → 应真的发出去
    text_e = "[自检E] 队列补发 %s" % STAMP
    with open(qpath, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"kind": "send", "spec": {"target": TARGET, "text": text_e},
                             "target": TARGET, "created": time.time(), "attempts": 0,
                             "since_local_id": since, "last_error": "session unavailable"},
                            ensure_ascii=False) + "\n")
    stat = sender.drain_queue()
    time.sleep(1.0)
    n_a = count_text(cfg, talker, text_a)
    n_e = count_text(cfg, talker, text_e)
    pending = sender.pending_count()
    record("E 重发队列（丢弃已送达 + 补发未送达）",
           n_a == 1 and n_e == 1 and pending == 0 and stat.get("delivered") == 1 and stat.get("resent") == 1,
           {"统计": stat, "队列剩余": pending, "A条数": n_a, "E条数": n_e})

    print("\n===== 汇总 =====")
    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(r["ok"] for r in RESULT) else 1


if __name__ == "__main__":
    sys.exit(main())
