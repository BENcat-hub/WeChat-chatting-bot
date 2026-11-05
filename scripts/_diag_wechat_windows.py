# -*- coding: utf-8 -*-
"""Enumerate Weixin top-level windows: hwnd, pid, rect, visible, title class."""
import ctypes, ctypes.wintypes as wt

user32 = ctypes.windll.user32
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def pid_of(hwnd):
    p = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
    return p.value


def title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    b = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def cls(hwnd):
    b = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, b, 256)
    return b.value


out = []


def cb(hwnd, lparam):
    if not user32.IsWindowVisible(hwnd):
        return True
    c = cls(hwnd)
    if c not in ("Qt51514QWindowIcon", "Qt51515QWindowIcon"):
        return True
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    out.append((hwnd, pid_of(hwnd), c, title(hwnd), (r.left, r.top, r.right, r.bottom)))
    return True


user32.EnumWindows(WNDENUMPROC(cb), 0)
for o in out:
    print(o)
print("count", len(out))
