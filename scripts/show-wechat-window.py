"""把微信 4.x 主窗口（标题 微信 / Weixin 的 Qt 窗口）从托盘里还原出来。

为什么需要它：scripts/wxui.py 只往 **可见** 的 Qt 窗口里输入，
微信主窗口一旦收到托盘里（IsWindowVisible=False），
bridge 的 /senders 就会报
    ok=false, hwnd=null, session_reason="WeChat window not found"
于是一条消息都发不出去。start-all.ps1 在启动微信后会调用本脚本兜底。

退出码：0 = 最终有可见窗口；1 = 没找到窗口或还原失败（需要人工点开）。
用法：  <astrbot venv 的 python.exe> -X utf8 scripts/show-wechat-window.py
"""

import ctypes
import sys
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
TITLES = {"微信", "Weixin"}
_CB = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def enumerate_windows():
    """[(hwnd, title, visible)] for Qt windows titled 微信/Weixin."""
    hits = []

    def visit(hwnd, _lparam):
        title = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, cls, 512)
        if title.value in TITLES and cls.value.startswith("Qt"):
            hits.append((hwnd, title.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(_CB(visit), 0)
    return hits


def main():
    hits = enumerate_windows()
    if not hits:
        print("[show-wechat] 没找到标题为 微信/Weixin 的 Qt 窗口（微信没启动？）")
        return 1

    for hwnd, title, visible in hits:
        if visible:
            continue
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        print("[show-wechat] 已还原窗口: %s hwnd=%d" % (title, hwnd))

    time.sleep(0.8)
    visible_now = [(h, t) for h, t, v in enumerate_windows() if v]
    if visible_now:
        print(
            "[show-wechat] 当前可见: "
            + ", ".join("%s(hwnd=%d)" % (t, h) for h, t in visible_now)
        )
        return 0
    print("[show-wechat] ✗ 窗口仍是隐藏状态（可能被托盘逻辑立刻收回），请手动点开微信主窗口")
    return 1


if __name__ == "__main__":
    sys.exit(main())
