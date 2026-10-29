"""Probe 2: which click point actually gives WeChat's message editor the focus?

Reports the focused child HWND (GetGUIThreadInfo) after each candidate click, then
pastes a marker and screenshots so we can see it. No Enter is sent.
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


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("flags", w.DWORD), ("hwndActive", w.HWND),
                ("hwndFocus", w.HWND), ("hwndCapture", w.HWND), ("hwndMenuOwner", w.HWND),
                ("hwndMoveSize", w.HWND), ("hwndCaret", w.HWND),
                ("rcCaret", w.RECT)]


u = ctypes.windll.user32
cfg = wxui.config()
h = wxui.find_wechat(cfg)
print("hwnd", h, "focus->", wxui.focus(h))
time.sleep(0.4)
r = w.RECT()
u.GetWindowRect(h, ctypes.byref(r))
print("rect", r.left, r.top, r.right, r.bottom)
tid = u.GetWindowThreadProcessId(h, None)


def focused():
    g = GUITHREADINFO()
    g.cbSize = ctypes.sizeof(GUITHREADINFO)
    if not u.GetGUIThreadInfo(tid, ctypes.byref(g)):
        return None
    return g.hwndFocus, g.hwndCaret


x = r.left + int(0.72 * (r.right - r.left))
for dy in (110, 150, 190, 70):
    y = r.bottom - dy
    winio.click(x, y, settle=0.1)
    time.sleep(0.35)
    f = focused()
    print(f"click y={y} (bottom-{dy}) focus={f}")
    winio.set_clipboard("P%03d" % dy)
    time.sleep(0.15)
    winio.chord(0x11, 0x56)
    time.sleep(0.5)
    winio.grab((r.left, r.top, r.right, r.bottom),
               os.path.join(ROOT, "logs", "probe2_%d.png" % dy))
    print("  saved probe2_%d.png" % dy)
