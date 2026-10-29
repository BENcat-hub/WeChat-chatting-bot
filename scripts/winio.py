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
import json
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


_CLICK_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "wxui-clickstate.json")


def _shared_last_click():
    try:
        with open(_CLICK_STATE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return float(data.get("x")), float(data.get("y")), float(data.get("t") or 0.0)
    except Exception:
        return None, None, 0.0


def _store_shared_click(x, y):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(_CLICK_STATE)), exist_ok=True)
        with open(_CLICK_STATE, "w", encoding="utf-8") as fh:
            json.dump({"x": int(x), "y": int(y), "t": time.time()}, fh)
    except Exception:
        pass


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


WS_CAPTION = 0x00C00000      # 有标题栏的窗口不可能是独占全屏
_GWL_STYLE = -16
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "SysShadow"}


def fullscreen_app(exclude=()):
    """(hwnd, title) of the borderless-fullscreen window that owns the desktop, else (0, "").

    2026-10-24：用户开《三角洲行动》（无边框全屏，rect 正好等于屏幕、无 WS_CAPTION）时，
    ForegroundWindow 永远是游戏，`focus()` 一次都抢不到前台、截图也只是游戏画面。以前
    这种状态只表现为「could not bring WeChat to the foreground」，一等 60 秒照样失败，
    重发队列还把每条消息都算成一次失败 → 打游戏期间聊天全是丢消息。把它显式认出来，
    调用方就能「等桌面空出来再发」而不是消耗重试次数。
    """
    hwnd, title, cls = foreground_info()
    if not hwnd or cls in _SHELL_CLASSES or hwnd in exclude:
        return 0, ""
    try:
        if user32.GetWindowLongW(hwnd, _GWL_STYLE) & WS_CAPTION:
            return 0, ""
        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return 0, ""
        vx = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if not (rect.left <= vx and rect.top <= vy
                and rect.right >= vx + vw and rect.bottom >= vy + vh):
            return 0, ""
    except Exception:
        return 0, ""
    return hwnd, title or cls


if __name__ == "__main__":
    # tiny self-check: grab the whole screen and report sizes/timings
    out = os.path.join(os.environ.get("TEMP") or ".", "winio-selfcheck.png")
    t0 = time.perf_counter()
    w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    grab([0, 0, w, h], out)
    print("grab %dx%d in %.0f ms -> %s" % (w, h, (time.perf_counter() - t0) * 1000, out))
    print("idle=%.2fs human_busy=%s" % (_idle_seconds(), human_busy()))

def list_wechat_chat_windows(main_title="微信"):
    """列出微信除主窗口以外的窗口（悬浮会话窗/工具窗），供诊断与清理报告。"""
    rows = []
    try:
        u = ctypes.windll.user32
    except Exception:
        return rows
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def _cb(hwnd, lparam):
        try:
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Qt51514"):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            name = (buf.value or "").strip()
            if not name or name == main_title:
                return True
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            rows.append({"hwnd": int(hwnd), "title": name, "class": cls.value,
                         "visible": bool(u.IsWindowVisible(hwnd)),
                         "rect": [r.left, r.top, r.right, r.bottom]})
        except Exception:
            pass
        return True

    u.EnumWindows(CB(_cb), 0)
    return rows


def close_detached_chat(title=None, main_title="微信", force=True):
    """关掉微信「独立会话窗口」。

    微信 4.x 里双击 / 拖拽会话行会把它弹成一个独立窗口（ClassName 同为
    Qt51514QWindowIcon，标题是联系人名）。这种窗口会让定位与打字落到错误的
    窗口上，而且用户反馈「关也关不掉，只能退出微信」。

    2026-10-27：force=True 时如果 WM_CLOSE 被无视，就再发 SC_CLOSE，最后
    ShowWindow(SW_HIDE) + 挪到屏幕外（WeChat 某些悬浮会话窗是工具窗口，
    只认隐藏，不认关闭）。
    """
    closed = []
    try:
        u = ctypes.windll.user32
        runtimes = ctypes.windll.user32
    except Exception:
        return closed
    EnumWindows = u.EnumWindows
    CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    targets = []

    def _cb(hwnd, lparam):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            name_len = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(name_len + 1)
            u.GetWindowTextW(hwnd, buf, name_len + 1)
            cls = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, cls, 256)
            if not cls.value.startswith("Qt51514"):
                return True
            if "TrayIcon" in cls.value:
                return True
            name = (buf.value or "").strip()
            if not name or name == main_title:
                return True
            if title is not None and name != str(title).strip():
                return True
            targets.append((hwnd, name))
        except Exception:
            pass
        return True

    EnumWindows(CB(_cb), 0)
    for hwnd, name in targets:
        try:
            u.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE
            time.sleep(0.20)
            if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
                closed.append(name)
                continue
            if force:
                u.PostMessageW(hwnd, 0x0112, 0xF060, 0)  # WM_SYSCOMMAND / SC_CLOSE
                time.sleep(0.20)
            if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd):
                closed.append(name)
                continue
            if force:
                r = ctypes.wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                wpx, hpx = max(200, r.right - r.left), max(200, r.bottom - r.top)
                u.ShowWindow(hwnd, 0)                    # SW_HIDE
                u.SetWindowPos(hwnd, 0, -20000, -20000, wpx, hpx, 0x0014)  # NOZORDER|NOACTIVATE
                closed.append(name + "（已强制隐藏）")
            else:
                closed.append(name + "（关闭失败）")
        except Exception:
            continue
    return closed


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
