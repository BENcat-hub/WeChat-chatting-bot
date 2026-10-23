#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Native (ctypes) desktop input/output for the WeChat UI driver.

Why this exists: every windows-mcp input call costs ~0.5s of JSON-RPC plus its own
control-state bookkeeping, so a full "search a chat and open it" flow needs ~3s. The same
work through SendInput / BitBlt / clipboard in-process costs tens of milliseconds, which is
what makes the 2-second locate budget reachable.

Safety: windows-mcp refuses to drive the desktop while the human is using the mouse or
keyboard. `human_busy()` reproduces that rule with GetLastInputInfo — and it ignores our own
synthetic events, which is the part MCP cannot do (its latch is what used to stick at
"state=user" forever after an interrupted run).
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SRCCOPY = 0x00CC0020

VK_CTRL, VK_V, VK_A, VK_ESCAPE, VK_RETURN, VK_BACK = 0x11, 0x56, 0x41, 0x1B, 0x0D, 0x08
VK_MENU = 0x12  # ALT：只用于「轻推」抢前台，不产生字符


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(wt.ULONG))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTunion)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


_SYNTH = [0.0]        # time.monotonic() of our last synthetic event
_HUMAN_WINDOW = 0.8   # a foreign event younger than this means the human is driving

# 2026-10-21 用户实测：「又双击打开窗口了」——微信把会话双击理解成「在独立窗口打开」。
# 两次点击落在同一个点、间隔又在系统双击时间内，Windows 就会合成一次 WM_LBUTTONDBLCLK，
# 哪怕我们把两次都当成「单击」发出去。这里记住上一次点击的位置和时间，同点连点就补足间隔。
_DBLCLICK_GUARD_S = 0.55   # 略大于 GetDoubleClickTime() 的默认 500ms
_DBLCLICK_SLOP_PX = 4      # 系统双击判定的位置容差（SM_CXDOUBLECLK 默认 4px）
_LAST_CLICK = [None, None, 0.0]


def set_click_guard(guard_s=None, slop_px=None):
    """由 wxui 从 wxui-config.json 下发（`double_click_guard_s` / `double_click_slop_px`）。"""
    global _DBLCLICK_GUARD_S, _DBLCLICK_SLOP_PX
    try:
        if guard_s is not None:
            _DBLCLICK_GUARD_S = max(0.0, float(guard_s))
        if slop_px is not None:
            _DBLCLICK_SLOP_PX = max(0, int(slop_px))
    except (TypeError, ValueError):
        pass


class HumanBusy(RuntimeError):
    """The human just touched the mouse/keyboard; refusing to fight them."""


def _touch():
    _SYNTH[0] = time.monotonic()


def _idle_seconds():
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 1e9
    now = kernel32.GetTickCount()
    delta = (now - info.dwTime) & 0xFFFFFFFF
    return delta / 1000.0


def human_busy(window=_HUMAN_WINDOW):
    """True when a *foreign* input event happened within `window` seconds.

    Our own SendInput events also reset GetLastInputInfo, so an idle age that is merely
    "recent" right after we typed is not evidence of a human — compare it with the time
    since our own last synthetic event instead.
    """
    idle = _idle_seconds()
    since_own = time.monotonic() - _SYNTH[0] if _SYNTH[0] else 1e9
    if idle > window:
        return False
    return idle > since_own + 0.15


def grab(region, path):
    """Screenshot a screen region with BitBlt and save it as PNG. Returns the path."""
    left, top, right, bottom = [int(v) for v in region]
    w, h = max(1, right - left), max(1, bottom - top)
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not gdi32.BitBlt(mem, 0, 0, w, h, screen, left, top, SRCCOPY):
            raise OSError("BitBlt failed")
        buf = ctypes.create_string_buffer(w * h * 4)
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise OSError("GetDIBits failed")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
    from PIL import Image
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.save(path)
    return path


def move(x, y):
    user32.SetCursorPos(int(x), int(y))
    _touch()


def click(x, y, settle=0.02):
    if human_busy():
        raise HumanBusy("human is using the mouse")
    # 同点连点会把单击合成双击（微信 = 在独立窗口打开会话），先把间隔补到双击阈值之外。
    lx, ly, lt = _LAST_CLICK
    if lx is not None and abs(x - lx) <= _DBLCLICK_SLOP_PX and abs(y - ly) <= _DBLCLICK_SLOP_PX:
        gap = time.monotonic() - lt
        if gap < _DBLCLICK_GUARD_S:
            time.sleep(_DBLCLICK_GUARD_S - gap)
    _LAST_CLICK[0], _LAST_CLICK[1], _LAST_CLICK[2] = int(x), int(y), time.monotonic()
    user32.SetCursorPos(int(x), int(y))
    _touch()
    time.sleep(settle)
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        ev = INPUT(type=INPUT_MOUSE)
        ev.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))
        _touch()
        time.sleep(0.01)


def wheel(notches, x=None, y=None, settle=0.02, batch=True):
    """滚鼠标滚轮：正数向上、负数向下（一格 = 120）。

    2026-10-21：windows-mcp 的 Scroll 一次要 4.5~4.8s（它自己还有状态机与等待），而「翻侧栏
    找会话」需要十几次滚动 —— 那条路根本进不了 2 秒。SendInput 的 WHEEL 事件是几十微秒。
    再进一步：一格一次 SendInput 的话，24 格要 312ms（每次调用 ~13ms 的驱动开销），
    把 24 个事件塞进**一次** SendInput 的数组里，同一段滚动就只要几毫秒。
    """
    if human_busy():
        raise HumanBusy("human is using the mouse")
    if x is not None and y is not None:
        user32.SetCursorPos(int(x), int(y))
        _touch()
        time.sleep(settle)
    n = max(1, abs(int(notches)))
    delta = 120 if notches > 0 else -120
    if batch:
        events = (INPUT * n)()
        for i in range(n):
            events[i].type = INPUT_MOUSE
            events[i].mi = MOUSEINPUT(0, 0, delta, MOUSEEVENTF_WHEEL, 0, None)
        user32.SendInput(n, events, ctypes.sizeof(INPUT))
        _touch()
        return
    for _ in range(n):
        ev = INPUT(type=INPUT_MOUSE)
        ev.mi = MOUSEINPUT(0, 0, delta, MOUSEEVENTF_WHEEL, 0, None)
        user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))
        _touch()
        time.sleep(0.012)


def key(vk, up=False):
    ev = INPUT(type=INPUT_KEYBOARD)
    ev.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))


def chord(*vks):
    if human_busy():
        raise HumanBusy("human is using the keyboard")
    for vk in vks:
        key(vk)
        _touch()
    for vk in reversed(vks):
        key(vk, up=True)
        _touch()


def tap(vk):
    chord(vk)


def set_clipboard(text):
    """Put `text` on the clipboard as CF_UNICODETEXT (no console, no PowerShell)."""
    if text is None:               # 调用方把 None 传进来过，报错信息毫无线索
        text = ""
    elif not isinstance(text, str):
        text = str(text)
    kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wt.HGLOBAL
    kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
    kernel32.GlobalUnlock.restype = wt.BOOL
    user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
    user32.SetClipboardData.restype = wt.HANDLE
    user32.OpenClipboard.argtypes = [wt.HWND]
    user32.OpenClipboard.restype = wt.BOOL
    user32.CloseClipboard.restype = wt.BOOL
    opened = False
    for _ in range(6):  # another process may hold the clipboard briefly
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.05)
    if not opened:
        raise OSError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * 2
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), size)
        kernel32.GlobalUnlock(handle)
        user32.SetClipboardData(CF_UNICODETEXT, handle)
    finally:
        user32.CloseClipboard()


def paste_text(x, y, text, focus_wait=0.15, paste_wait=0.35):
    """Click the input at (x, y), put `text` on the clipboard and press Ctrl+V."""
    click(x, y)
    time.sleep(focus_wait)
    set_clipboard(text)
    chord(VK_CTRL, VK_V)
    time.sleep(paste_wait)


def press_escape():
    chord(VK_ESCAPE)


def raise_window(hwnd, timeout=1.6):
    """把窗口从最小化/后台带到前台，返回是否成功。

    用户 2026-10-21 明确要求：他不会把微信置顶，我们不能假设它已经在前台，必须自己唤起。
    顺序：已最小化先 SW_RESTORE；SetForegroundWindow 被系统拒绝时（前台属于别的进程时
    Windows 会忽略它），临时 AttachThreadInput 到自己和前台线程，再试一次；最后用一次
    极短的 ALT 键轻推（系统认为「用户刚按过键」时允许抢前台）。全程不发字符键。
    """
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        if not user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 5)   # SW_SHOW
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if user32.GetForegroundWindow() == hwnd:
            return True
        # AttachThreadInput 借用前台线程的输入状态，绕过前台锁定
        fg = user32.GetForegroundWindow()
        cur = kernel32.GetCurrentThreadId()
        tgt = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        try:
            if tgt and tgt != cur:
                attached = bool(user32.AttachThreadInput(cur, tgt, True))
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur, tgt, False)
        try:
            # SwitchToThisWindow 走的是「用户切换」语义，比 SetForegroundWindow 更容易被允许
            user32.SwitchToThisWindow(hwnd, True)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)  # NOSIZE|NOMOVE|SHOWWINDOW
        except Exception:
            pass
        time.sleep(0.04)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # 轻推 ALT：只按下抬起，不产生字符
        key(VK_MENU)
        key(VK_MENU, up=True)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.06)
    return user32.GetForegroundWindow() == hwnd


def foreground_info():
    """(hwnd, title, class) of the window that currently owns the foreground."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, "", ""
    n = user32.GetWindowTextLengthW(hwnd)
    title = ""
    if n:
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    return hwnd, title, cls.value


if __name__ == "__main__":
    # tiny self-check: grab the whole screen and report sizes/timings
    out = os.path.join(os.environ.get("TEMP") or ".", "winio-selfcheck.png")
    t0 = time.perf_counter()
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    grab([0, 0, w, h], out)
    print("grab %dx%d in %.0f ms -> %s" % (w, h, (time.perf_counter() - t0) * 1000, out))
    print("idle=%.2fs human_busy=%s" % (_idle_seconds(), human_busy()))

def press_down(x, y, settle=0.02):
    """Hold the left button down at (x, y) without releasing (for 按住说话)."""
    if human_busy():
        raise HumanBusy("human is using the mouse")
    _LAST_CLICK[0], _LAST_CLICK[1], _LAST_CLICK[2] = int(x), int(y), time.monotonic()
    user32.SetCursorPos(int(x), int(y))
    _touch()
    time.sleep(settle)
    ev = INPUT(type=INPUT_MOUSE)
    ev.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))
    _touch()


def press_up(x=None, y=None):
    """Release the left button (ends 按住说话 recording)."""
    if x is not None and y is not None:
        user32.SetCursorPos(int(x), int(y))
    ev = INPUT(type=INPUT_MOUSE)
    ev.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    user32.SendInput(1, ctypes.byref(ev), ctypes.sizeof(INPUT))
    _touch()
    time.sleep(0.02)
