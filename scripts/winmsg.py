#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""窗口消息后端：点窗口、往窗口送字符、后台截窗口 —— 光标一动不动。

为什么需要它：现在的 wxui 走 `winio.click` / `paste_text`，本质是
`SetCursorPos + SendInput` —— 物理光标被拽走、前台被抢，人正在用电脑时
就是「抢鼠标」。微信 4.x 是 Qt 单 HWND 应用（`window_class_prefix = "Qt"`），
Qt 的 Windows 平台插件会把 `WM_LBUTTONDOWN` / `WM_CHAR` 翻译成自己的
QMouseEvent / QKeyEvent，所以只要把消息 Post 到那个 HWND 上，就能在不移动
光标、不切前台的前提下驱动它。

UIPI：微信是 High 完整性，本模块必须在 High 上下文里跑（`scripts/_run_elevated.ps1`
或提权起的 bridge），否则 `PostMessageW` 返回 0 / `GetLastError() = 5`。

所有句柄/参数都显式声明 argtypes/restype —— 这台机器上不声明就会被截断成
0x0，之前查输入完整性时踩过。
"""
import ctypes
import ctypes.wintypes as wt
import logging
import os
import time

log = logging.getLogger("winmsg")

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SETTEXT = 0x000C
WM_PASTE = 0x0302
WM_MOUSEWHEEL = 0x020A
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
MK_LBUTTON = 0x0001
PW_RENDERFULLCONTENT = 0x00000002
GA_ROOT = 2

W = ctypes.wintypes

user32.WindowFromPoint.argtypes = [W.POINT]
user32.WindowFromPoint.restype = W.HWND
user32.GetAncestor.argtypes = [W.HWND, ctypes.c_uint]
user32.GetAncestor.restype = W.HWND
user32.ScreenToClient.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
user32.ScreenToClient.restype = W.BOOL
user32.ClientToScreen.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
user32.ClientToScreen.restype = W.BOOL
user32.PostMessageW.argtypes = [W.HWND, ctypes.c_uint, W.WPARAM, W.LPARAM]
user32.PostMessageW.restype = W.BOOL
user32.SendMessageW.argtypes = [W.HWND, ctypes.c_uint, W.WPARAM, W.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.PrintWindow.argtypes = [W.HWND, W.HDC, ctypes.c_uint]
user32.PrintWindow.restype = W.BOOL
# GetWindowRect/GetClientRect 的第二个参数**故意不声明 argtypes**：wxui.py 有自己的 RECT 结构，
# 一旦这里锁死 POINTER(W.RECT)，同一进程里 wxui 再调用就报
# `expected LP_RECT instance instead of pointer to RECT`（2026-11-07 实测踩过）。
user32.GetWindowRect.restype = W.BOOL
user32.GetClientRect.restype = W.BOOL
user32.IsWindowVisible.argtypes = [W.HWND]
user32.IsWindowVisible.restype = W.BOOL
user32.GetForegroundWindow.restype = W.HWND
user32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetGUIThreadInfo.argtypes = [W.DWORD, ctypes.c_void_p]
user32.GetGUIThreadInfo.restype = W.BOOL
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.GetWindowThreadProcessId.restype = W.DWORD
user32.AttachThreadInput.argtypes = [W.DWORD, W.DWORD, W.BOOL]
user32.AttachThreadInput.restype = W.BOOL
user32.SetFocus.argtypes = [W.HWND]
user32.SetFocus.restype = W.HWND
user32.GetActiveWindow.restype = W.HWND
kernel32.GetCurrentThreadId.restype = W.DWORD


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", W.DWORD),
        ("flags", W.DWORD),
        ("hwndActive", W.HWND),
        ("hwndFocus", W.HWND),
        ("hwndCapture", W.HWND),
        ("hwndMenuOwner", W.HWND),
        ("hwndMoveSize", W.HWND),
        ("hwndCaret", W.HWND),
        ("rcCaret", W.RECT),
    ]


def dpi_aware():
    """按物理像素思考（wxui.py 里也是 SetProcessDpiAwareness(2)）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def win_text(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_root(title="微信", class_prefix=None):
    """按标题（精确或包含）+ 可选 class 前缀找主窗，返回 hwnd 或 0。"""
    found = []

    @ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def _cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        t = win_text(hwnd)
        c = win_class(hwnd)
        if class_prefix and not c.startswith(class_prefix):
            return True
        if title and title not in t:
            return True
        if user32.GetWindow(hwnd, 4) and not class_prefix:  # GW_OWNER：跳附属窗
            return True
        found.append(hwnd)
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else 0


def hwnd_at(x, y):
    """屏幕点 -> (root, target)。Qt 是单 HWND，target 通常等于 root。"""
    pt = W.POINT(int(x), int(y))
    target = user32.WindowFromPoint(pt) or 0
    if not target:
        return 0, 0
    root = user32.GetAncestor(target, GA_ROOT) or target
    return int(root), int(target)


def to_client(hwnd, x, y):
    pt = W.POINT(int(x), int(y))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def post_click(hwnd, x, y, settle=0.02, dblclick=False):
    """在屏幕坐标 (x, y) 处向 hwnd 投一个鼠标点击，光标不动。"""
    cx, cy = to_client(hwnd, x, y)
    lp = (int(cy) << 16) | (int(cx) & 0xFFFF)
    user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.01)
    if not user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp):
        log.warning("[winmsg] WM_LBUTTONDOWN 投递失败 err=%s", ctypes.get_last_error())
        return False
    time.sleep(0.01)
    if dblclick:
        user32.PostMessageW(hwnd, WM_LBUTTONDBLCLK, MK_LBUTTON, lp)
        time.sleep(0.01)
    user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    time.sleep(settle)
    return True


def post_chars(hwnd, text, gap=0.004):
    """逐字符 WM_CHAR。Qt 会把它们交给内部焦点控件（输入框）。"""
    ok = 0
    for ch in text:
        if user32.PostMessageW(hwnd, WM_CHAR, ord(ch), 1):
            ok += 1
        time.sleep(gap)
    return ok


def post_msg(hwnd, msg, wparam=0, lparam=0):
    """投递任意窗口消息（如 WM_PASTE=0x0302 —— 让 Qt 自己读剪贴板粘贴图片）。"""
    ok = user32.PostMessageW(hwnd, msg, wparam, lparam)
    if not ok:
        log.warning("[winmsg] PostMessage 0x%04X 失败 err=%s", msg, ctypes.get_last_error())
    return bool(ok)


def post_wheel(hwnd, x, y, notches, settle=0.12):
    """在屏幕坐标 (x, y) 处投滚轮消息（正数向上、负数向下），光标不动。

    WM_MOUSEWHEEL 的 lParam 用**屏幕坐标**（和鼠标点击消息不同，后者用客户区坐标）；
    wParam 高 16 位是 delta，一格 = 120。
    """
    delta = int(notches) * 120
    wp = (delta << 16) & 0xFFFF0000
    lp = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
    ok = user32.PostMessageW(hwnd, WM_MOUSEWHEEL, wp, lp)
    if not ok:
        log.warning("[winmsg] WM_MOUSEWHEEL 投递失败 err=%s", ctypes.get_last_error())
    time.sleep(settle)
    return bool(ok)


def post_key(hwnd, vk, char=0):
    lp = 1 | (vk << 16)
    down = user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lp)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lp | (1 << 30) | (1 << 31))
    if char:
        user32.PostMessageW(hwnd, WM_CHAR, char, 1)
    return bool(down)


def gui_focus(hwnd):
    """读该窗口所属线程的 GUI 焦点/活动窗（不改任何状态）。"""
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
        return None
    return {"active": int(info.hwndActive or 0), "focus": int(info.hwndFocus or 0), "tid": int(tid)}


class focus_attached:
    """把本线程输入队列挂到目标窗口线程上，SetFocus 后**不**抢前台。

    用法：`with focus_attached(hwnd): winmsg.post_chars(hwnd, "你好")`
    退出时恢复原来的焦点并解挂。
    """

    def __init__(self, hwnd):
        self.hwnd = int(hwnd)
        self.tid_target = user32.GetWindowThreadProcessId(self.hwnd, None)
        self.tid_self = kernel32.GetCurrentThreadId()
        self.prev_focus = 0
        self.attached = False

    def __enter__(self):
        if self.tid_target and self.tid_target != self.tid_self:
            self.attached = bool(user32.AttachThreadInput(self.tid_self, self.tid_target, True))
        self.prev_focus = int(user32.SetFocus(self.hwnd) or 0)
        return self

    def __exit__(self, *exc):
        try:
            if self.prev_focus:
                user32.SetFocus(self.prev_focus)
        finally:
            if self.attached:
                user32.AttachThreadInput(self.tid_self, self.tid_target, False)
        return False


def client_size(hwnd):
    """窗口客户区宽高 —— capture(region=...) 用的就是这个坐标系。"""
    rc = W.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    return int(rc.right - rc.left), int(rc.bottom - rc.top)


def capture(hwnd, path, region=None):
    """PrintWindow(PW_RENDERFULLCONTENT) 截窗口 —— 被别的窗口盖住也能截。

    region 用窗口客户区坐标 (left, top, right, bottom)；None = 整窗。
    返回 dict：{path, w, h, mean, std}，std≈0 说明是黑帧/空帧。
    """
    from PIL import Image
    import numpy as np

    rc = W.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
    if w <= 0 or h <= 0:
        raise OSError("window has no size")

    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT):
            raise OSError("PrintWindow failed")
        class BIH(ctypes.Structure):
            _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG),
                        ("biPlanes", W.WORD), ("biBitCount", W.WORD), ("biCompression", W.DWORD),
                        ("biSizeImage", W.DWORD), ("biXPelsPerMeter", W.LONG),
                        ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]

        buf = ctypes.create_string_buffer(w * h * 4)
        bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            raise OSError("GetDIBits failed")
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)

    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    # 窗口坐标 -> 客户区坐标
    crect = W.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(crect))
    org = W.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(org))
    dx, dy = org.x - rc.left, org.y - rc.top
    if region:
        l, t, r, b = [int(v) for v in region]
        box = (max(0, dx + l), max(0, dy + t), min(w, dx + r), min(h, dy + b))
        if box[2] > box[0] and box[3] > box[1]:
            img = img.crop(box)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.save(path)
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    return {"path": path, "w": img.width, "h": img.height,
            "mean": round(float(arr.mean()), 2), "std": round(float(arr.std()), 2)}


def mean_abs_diff(path_a, path_b):
    from PIL import Image
    import numpy as np

    a = np.asarray(Image.open(path_a).convert("L"), dtype=np.float32)
    b = np.asarray(Image.open(path_b).convert("L"), dtype=np.float32)
    if a.shape != b.shape:
        return None
    return round(float(np.abs(a - b).mean()), 2)
