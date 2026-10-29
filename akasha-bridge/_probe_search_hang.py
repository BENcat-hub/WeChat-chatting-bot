"""Where does the search path hang WeChat?

Walks the exact steps `open_chat_by_search` performs, measuring window
responsiveness (WM_NULL round-trip) after each one, so we can name the step that
freezes the client instead of guessing. Sends nothing.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
ctypes.windll.shcore.SetProcessDpiAwareness(2)

import winio  # noqa: E402
import wxui  # noqa: E402

u = ctypes.windll.user32
cfg = wxui.config()
hwnd = wxui.find_wechat(cfg)


def latency(label):
    t0 = time.perf_counter()
    res = ctypes.c_size_t()
    ok = u.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 3000, ctypes.byref(res))
    ms = (time.perf_counter() - t0) * 1000
    print(f"  [{label:22s}] responsive={bool(ok)} {ms:7.1f} ms")
    return bool(ok), ms


print("hwnd", hwnd, "rect", end=" ")
r = w.RECT()
u.GetWindowRect(hwnd, ctypes.byref(r))
print(r.left, r.top, r.right, r.bottom)
latency("baseline")

client = wxui.Client()
rect = (r.left, r.top, r.right, r.bottom)
point = wxui._search_box_point(cfg, rect)
print("search box point", point)

ok, _ = latency("before click")
winio.click(point[0], point[1], settle=0.15)
time.sleep(0.9)
latency("after search click")
winio.grab(rect, os.path.join(ROOT, "logs", "hang_step1_panel.png"))

winio.set_clipboard("文件传输助手")
time.sleep(0.2)
winio.chord(0x11, 0x56)
time.sleep(1.2)
latency("after paste")
winio.grab(rect, os.path.join(ROOT, "logs", "hang_step2_results.png"))

t0 = time.time()
pick, dbg = wxui._search_result_pick(client, cfg, rect, "文件传输助手")
print("pick", pick, "in", round((time.time() - t0) * 1000), "ms", "err=", dbg.get("error"))
print("ocr lines:", dbg.get("lines"))
latency("after OCR screenshot")
winio.grab(rect, os.path.join(ROOT, "logs", "hang_step3_afterocr.png"))

winio.press_escape()
time.sleep(0.6)
latency("after escape")
winio.grab(rect, os.path.join(ROOT, "logs", "hang_step4_afteresc.png"))
print("done")
