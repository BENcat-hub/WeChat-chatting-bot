"""Probe: does the paste primitive actually put text into WeChat's input box?

Steps: focus WeChat → click the input spot → clipboard → Ctrl+V → screenshot.
Nothing is sent (no Enter), so it is safe to run while the user is watching.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import os
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

cfg = wxui.config()
u = ctypes.windll.user32
h = wxui.find_wechat(cfg)
print("hwnd", h)
print("focus ->", wxui.focus(h))
time.sleep(0.4)
r = w.RECT()
u.GetWindowRect(h, ctypes.byref(r))
print("rect", r.left, r.top, r.right, r.bottom, "fg=", u.GetForegroundWindow() == h)
x = r.left + int(0.72 * (r.right - r.left))
y = r.bottom - 110
print("input point", x, y)
winio.click(x, y, settle=0.1)
time.sleep(0.3)
print("clipboard set ->", winio.set_clipboard("测试PROBE123"))
time.sleep(0.2)
winio.chord(0x11, 0x56)  # ctrl+v
time.sleep(0.8)
p = os.path.join(ROOT, "logs", "probe_paste.png")
winio.grab((r.left, r.top, r.right, r.bottom), p)
print("saved", p)
