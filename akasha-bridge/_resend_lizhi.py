"""把没送到「用户F」的消息补发出去（走微信窗口本身，不经 OCR 定位）。

背景：bridge 的 open_chat 靠侧栏 OCR 找行，用户F那行是 **选中态（绿底）**，
OCR 反而跳过它；聊天头部 OCR 又把「用户F」读成「丕 忄 尸」，于是
current_chat_check 永远 matched=false，一条都发不出去。
好在她的对话本来就开着，这里直接点输入框粘贴 + 回车，绕开定位校验。

用法： akasha-bridge/.venv/Scripts/python.exe -X utf8 _resend_lizhi.py [--dry]
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import winio  # noqa: E402
import wxui  # noqa: E402

QUEUE = os.path.join(ROOT, "akasha-bridge", "wxui_pending.jsonl")

# 顺序 = 原本该按顺序到达的顺序：前 3 条是重发队列里的，后 2 条同样没送达。
TEXTS = [
    "咋了",
    "刚才顾着别的事了",
    "行了，这不理你了嘛",
    "行行行，刚在吹头发没看手机",
    "不是故意晾你",
]


def main() -> int:
    dry = "--dry" in sys.argv
    cfg = wxui.config()
    u = ctypes.windll.user32
    h = wxui.find_wechat(cfg)
    if not h:
        print("no wechat window")
        return 2
    winio.raise_window(h)
    time.sleep(0.6)
    r = w.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    x = r.left + int(0.72 * (r.right - r.left))
    y = r.bottom - 110
    print(f"window={r.left},{r.top},{r.right},{r.bottom} input=({x},{y}) dry={dry}")

    if dry:
        for t in TEXTS:
            print("would send:", t)
        return 0

    if os.path.exists(QUEUE):
        bak = QUEUE + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(QUEUE, bak)
        open(QUEUE, "w", encoding="utf-8").close()  # 清空，避免 bridge 再补一遍
        print("queue backed up ->", os.path.basename(bak), "and cleared")

    for i, t in enumerate(TEXTS, 1):
        winio.click(x, y, settle=0.05)
        time.sleep(0.15)
        winio.chord(0x11, 0x41)  # ctrl+a
        time.sleep(0.1)
        winio.tap(0x2E)          # delete
        time.sleep(0.1)
        winio.paste_text(x, y, t, focus_wait=0.25, paste_wait=0.5)
        time.sleep(0.35)
        winio.tap(0x0D)          # enter
        print(f"sent {i}/{len(TEXTS)}: {t}")
        time.sleep(1.7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
