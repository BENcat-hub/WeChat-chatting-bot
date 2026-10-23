#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verified WeChat sender driven by Windows-MCP + Windows OCR.

Why the previous senders silently failed:
  * WeeMessenger / UIA both do Ctrl+F -> paste -> Enter. In WeChat 4.x the first
    dropdown row is "搜索网络结果" (a web result), so Enter opens nothing and the
    text is dropped (or leaked as a chat message when focus was elsewhere).
  * WeFlow's session list is NOT the sidebar list: it omits service accounts, so
    a sidebar row index cannot be derived from it.

This module drives the window through Windows-MCP (physical pixel coordinates),
finds the target row by OCR-ing the sidebar with the name from WeFlow, clicks it,
types the message, and finally VERIFIES delivery by reading the message back from
WeChat's own database through the WeFlow API. Exit code 0 only when verified.

Run it with the bridge venv python (3.12, has winsdk):
  <bridge>\\.venv\\Scripts\\python.exe wxui.py info
  ... wxui.py send --target 文件传输助手 --text "hello"        (dry-run)
  ... wxui.py send --target 文件传输助手 --text "hello" --commit
"""
import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from wmcp import Client  # noqa: E402

try:  # 原生驱动（SendInput/BitBlt/剪贴板）。~30ms 一次，而 windows-mcp 一次输入要 ~0.5s
    import winio  # noqa: E402
except Exception:  # pragma: no cover - 没有 Pillow 等依赖时退回纯 MCP
    winio = None

# 人为在用键鼠时原生层抛出它；没有原生层时用一个永不抛出的类型占位，except 分支保持同形。
HumanBusy = winio.HumanBusy if winio is not None else type("HumanBusy", (RuntimeError,), {})

try:  # noqa: E402  - must match Windows-MCP's physical-pixel space
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import logging                                                      # noqa: E402

if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wxui")

CONFIG_PATH = os.path.join(HERE, "wxui-config.json")


def config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        cfg = json.load(fh)
    # 防双击参数随配置下发（winio 自己不知道配置文件在哪）：同点连点的最小间隔与位置容差。
    if winio is not None:
        winio.set_click_guard(cfg.get("double_click_guard_s"), cfg.get("double_click_slop_px"))
    return cfg


def token(cfg):
    with open(os.path.join(ROOT, cfg["token_file"]), encoding="utf-8") as fh:
        return fh.read().strip()


def api(cfg, path, **params):
    # 2026-10-22：WeFlow 的 /messages 偶发 500（会话列表/回读同时坏掉），
    # 以前直接抛异常 → 重发队列把「其实已送达」判成失败 → 同一条消息刷屏 12 次。
    # 这里给所有 GET 加 3 次重试，把瞬时 5xx/超时吸收掉。
    _last = None
    for _i in range(3):
        try:
            return _api_once(cfg, path, **params)
        except Exception as _e:            # noqa: BLE001
            _last = _e
            time.sleep(0.35 + 0.3 * _i)
    raise _last


def _api_once(cfg, path, **params):
    params["access_token"] = token(cfg)
    url = cfg["weflow_base_url"] + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def find_wechat(cfg):
    """HWND of the WeChat main window (Qt window titled 微信), or None."""
    return find_window(cfg, cfg["window_title"])


def find_window(cfg, title, fuzzy=False):
    """First visible window whose title matches and class starts with the prefix.

    `fuzzy=True` is for a chat popped out into its own window (titled with the chat name):
    the title can carry emoji or a remark the target string does not spell out. The main
    WeChat window is always looked up with the strict default.
    """
    user32 = ctypes.windll.user32
    hits = []
    hidden = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _):
        t = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, t, 512)
        user32.GetClassNameW(hwnd, cls, 512)
        if cls.value.startswith(cfg["window_class_prefix"]):
            matched = (name_match(title, t.value) > 0) if fuzzy else (t.value == title)
            if matched:
                (hits if user32.IsWindowVisible(hwnd) else hidden).append(hwnd)
        return True

    user32.EnumWindows(proc(visit), 0)
    if hits:
        return hits[0]
    # 2026-10-21: 微信被最小化到托盘后 Qt 主窗口变成「不可见但仍然存在」——
    # wxui 因此报 "WeChat window not found"，发送在 90s 后超时、重发队列越积越多，
    # 而用户只觉得自己关了窗口。这里把隐藏的主窗口恢复出来（只有窗口已存在才不会
    # 拉起新实例），恢复后再确认它真的可见。
    for hwnd in hidden:
        try:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        except Exception:
            continue
        for _ in range(10):
            if user32.IsWindowVisible(hwnd):
                try:
                    sys.stderr.write("[wxui] 微信窗口原本在托盘里，已自动恢复显示\n")
                    sys.stderr.flush()
                except Exception:
                    pass
                return hwnd
            time.sleep(0.15)
    return None


def device_class(hwnd):
    """(title, class) of a window handle — used to refuse typing into a foreign window."""
    if not hwnd:
        return "", ""
    user32 = ctypes.windll.user32
    t = ctypes.create_unicode_buffer(512)
    cls = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, t, 512)
    user32.GetClassNameW(hwnd, cls, 512)
    return t.value, cls.value


def foreground():
    return ctypes.windll.user32.GetForegroundWindow()


def focus(hwnd):
    """Bring hwnd to the foreground; returns True when it really is the foreground window.

    Windows refuses SetForegroundWindow from a background process unless the calling
    thread is attached to the current foreground thread, which is what the
    AttachThreadInput dance below does. Without this the click lands on whatever
    window is on top and the typed text goes to the wrong application.
    """
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    # 2026-10-16: 分段回复每条都要聚焦一次，而 ShowWindow + 长等待只有「窗口不在前台
    # 或已最小化」时才需要。已经在最前面就直接返回，省掉每条消息 0.45s。
    # 2026-10-21: 而且那 0.45s 是**盲等**——SetForegroundWindow 通常几十毫秒就生效。
    # 改成每 20ms 探一次前景窗口，一旦是目标就走，实测 focus 从 755ms 降到 ~30ms。
    if user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
        return True
    if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE

    def _wait_foreground(limit_s):
        deadline = time.time() + limit_s
        while time.time() < deadline:
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.02)
        return user32.GetForegroundWindow() == hwnd

    if _wait_foreground(0.5):
        return True
    for _ in range(3):
        fg = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(mine, theirs, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(mine, theirs, False)
        if _wait_foreground(0.45):
            return True
    return user32.GetForegroundWindow() == hwnd


def window_rect(hwnd):
    r = RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def rect_usable(rect):
    """False for the -32000 sentinel Windows reports for a minimized/hidden window.

    Screenshotting that region returns no image at all, which used to surface as a
    misleading "could not locate target row by OCR".
    """
    left, top, right, bottom = rect
    return left > -30000 and top > -30000 and (right - left) > 200 and (bottom - top) > 200


def restored_rect(hwnd, tries=3):
    """A rect we can actually screenshot, restoring WeChat when it is minimized/hidden."""
    rect = window_rect(hwnd)
    for _ in range(tries):
        if rect_usable(rect):
            return rect
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.6)
        rect = window_rect(hwnd)
    return rect


def fail(plan, error, retryable=True):
    """Record a send failure. retryable=True means "try again", not "the message is lost"."""
    plan.update(ok=False, error=error, retryable=bool(retryable))
    # 让 bridge 不必解析文案就能分辨「windows-mcp 不肯驱动桌面」——那种情况要换掉常驻
    # worker（新客户端才会拿到干净的 control latch），而不是当成普通的发不出去重试。
    plan["control_preempted"] = preempted(error)
    return False


def input_box_point(cfg, rect):
    """Click point of WeChat's message box.

    Clicked before clearing, because ctrl+a only means "select all in the input box" when
    the caret is really in it; with focus on the message list ctrl+a means something else.
    """
    left, _, right, bottom = rect
    x = left + int(cfg.get("input_box_x_offset_px") or (right - left) * 0.72)
    y = bottom - int(cfg.get("input_box_y_from_bottom_px", 110))
    return int(x), int(y)


def clear_input(client, cfg, rect, focused_clean=False):
    """Empty the message box so an interrupted attempt can never be sent twice.

    If a send is interrupted between paste and Enter (or Enter never reaches WeChat), the
    text stays in the input box; the next message would then be delivered as
    "oldtextnewtext". Click the box, ctrl+a, delete — every attempt starts from empty.

    `focused_clean=True` means this process itself just read a send back as delivered in
    the *same* chat, so WeChat cleared the box and still owns the keyboard: one `delete`
    is enough. 2026-10-16（用户要求分段回复提速）：完整清理要 click+ctrl+a+delete 三次
    输入调用 ≈ 2.1s，而每条分段回复都付一遍。只有「上一次确实被回读确认送达」才允许走
    这条路——被中断的发送永远拿不到 verified，所以旧文本不可能被拼在新文本前面。
    """
    if not cfg.get("clear_input_box", True) or not rect:
        return {"skipped": True}
    x, y = input_box_point(cfg, rect)
    native = _native_on(cfg, "native_input")
    if focused_clean and cfg.get("input_fast_clear", True):
        if native:
            winio.chord(winio.VK_BACK)
        else:
            client.call("Shortcut", {"shortcut": "delete"})
        time.sleep(0.05)
        return {"x": x, "y": y, "fast": True, "driver": "native" if native else "mcp"}
    if native:
        winio.click(x, y)
        time.sleep(0.06)
        winio.chord(winio.VK_CTRL, winio.VK_A)
        time.sleep(0.04)
        winio.chord(winio.VK_BACK)
        time.sleep(0.05)
        return {"x": x, "y": y, "driver": "native"}
    client.call("Click", {"loc": [x, y], "clicks": 1})
    time.sleep(0.15)
    client.call("Shortcut", {"shortcut": "ctrl+a"})
    time.sleep(0.08)
    client.call("Shortcut", {"shortcut": "delete"})
    time.sleep(0.12)
    return {"x": x, "y": y, "driver": "mcp"}


def clamp_region(region, rect, margin=6):
    """Keep an absolute capture region strictly inside the window.

    The sidebar/header offsets are absolute pixels measured on one window size; when the
    window is smaller (or WeChat restores a compact layout) an unclamped region reaches
    past the window edge, so the screenshot also contains the desktop / other windows.
    OCR then "finds" text that is not in WeChat and the click lands outside the app —
    exactly the "clicks the wrong row" symptom.
    """
    left, top, right, bottom = (int(v) for v in rect)
    left, right = min(left, right - 1), max(right, left + 1)
    top, bottom = min(top, bottom - 1), max(bottom, top + 1)
    # A window smaller than twice the margin (logged-out login window, collapsed panel)
    # would otherwise produce an inverted region; shrink the margin instead.
    if right - left <= 2 * margin:
        margin = max(0, (right - left - 1) // 2)
    if bottom - top <= 2 * margin:
        margin = min(margin, max(0, (bottom - top - 1) // 2))
    x1 = max(left + margin, min(int(region[0]), right - margin - 1))
    y1 = max(top + margin, min(int(region[1]), bottom - margin - 1))
    x2 = min(right - margin, max(int(region[2]), x1 + 1))
    y2 = min(bottom - margin, max(int(region[3]), y1 + 1))
    return [x1, y1, x2, y2]


def screen_capturable(client, cfg, rect):
    """(ok, reason): False when this session cannot be screenshotted at all.

    A disconnected RDP session or a locked workstation still enumerates the WeChat window
    with a perfectly usable rect, but every Screenshot comes back empty — which used to
    surface as a bogus "could not locate target row by OCR". Detecting it here turns an
    outage into an explicit retryable failure instead of a lost message.
    """
    if not rect_usable(rect):
        return False, "WeChat window is minimized/hidden (rect=%s)" % (list(rect),)
    left, top = rect[0], rect[1]
    tiny = [left + 4, top + 4, left + 64, top + 64]
    png, err = shot_ex(client, tiny, os.path.join(ROOT, "logs", "wmcp", "probe"), tries=1)
    if not png:
        # 2026-10-18: windows-mcp answers a refused tool call with a text result, not an
        # image — a CONTROL_PREEMPTED (human owns the desktop / its latch never cleared)
        # used to be reported as "screen not capturable", which sent the whole diagnosis
        # down the "RDP dropped / workstation locked" path. Name the real cause instead.
        if preempted(err):
            return False, "desktop control preempted by windows-mcp (CONTROL_PREEMPTED: %s)" % _brief(err)
        return False, "screen not capturable (RDP disconnected or workstation locked)"
    return True, ""


def control_state(client):
    """(state, wait_seconds) — who owns the desktop: 'ready' / 'ai' / 'user' / ...

    windows-mcp hands the desktop back to the human the instant they touch the keyboard or
    mouse ('user', 'takeover_pending') and refuses AI input until they have been idle for
    ~10s. Pasting in that window is exactly how an interrupted send turns into a garbled
    message, so callers wait for 'ready'/'ai' instead of racing the user.
    Returns (None, 0.0) when the probe is unavailable (older server, transport error).
    """
    try:
        res = client.call("ControlStatus", {})
    except Exception:
        return None, 0.0
    for item in (res or {}).get("content") or []:
        if item.get("type") != "text":
            continue
        try:
            data = json.loads(item.get("text") or "{}")
        except ValueError:
            continue
        return str(data.get("state") or ""), float(data.get("wait_seconds") or 0.0)
    return None, 0.0


def wait_for_session(client, cfg, plan, hwnd=None):
    """Wait until WeChat is restored, focusable, screenshottable and ours to drive.

    Covers the three outages that used to lose messages: the window being minimized, an
    RDP session that dropped, and a human currently using the machine. All three are
    usually temporary, so park here instead of failing; after session_wait_s the failure is
    marked retryable and the bridge queues the message.
    """
    started = time.time()
    deadline = started + float(cfg.get("session_wait_s", 60))
    reason = "WeChat window not found"
    native = _native_on(cfg, "native_input")
    while True:
        hwnd = find_wechat(cfg) or hwnd
        if hwnd:
            if native:
                # 原生驱动下不看 windows-mcp 的 control latch：它的 user 状态被中断过的
                # 自动化钉在 ~10s 不动（每次读都是 9.9/10.0），一旦信它就永远发不出去。
                # 真正该让路的是「人现在正握着鼠标键盘」，这一条用 GetLastInputInfo 自己判，
                # 而且会忽略我们自己投出去的合成事件（见 winio.human_busy）。
                ctl, wait = ("user", 0.0) if winio.human_busy() else ("ready", 0.0)
            else:
                ctl, wait = control_state(client)
            if ctl not in (None, "ready", "ai"):
                if ctl == "user" and wait >= 9.9:
                    # windows-mcp 的 control latch 被 _recover_after_rehook 钉死在 10 秒
                    # （每次读都是 ~10.0s、状态永远 user），等满 session_wait_s 也不会自己好；
                    # 立刻交回，由 bridge 丢掉这个常驻 worker，换新客户端重试。
                    return fail(plan, "desktop control preempted by windows-mcp "
                                      "(CONTROL_PREEMPTED: state=user wait=%.1fs frozen latch)"
                                      % wait, retryable=True)
                reason = "user is using the desktop (control=%s, ~%.0fs)" % (ctl, wait)
            else:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                rect = restored_rect(hwnd, tries=1)
                ok, reason = screen_capturable(client, cfg, rect)
                if ok:
                    if focus(hwnd):
                        plan["window_rect"] = list(rect)
                        plan["control_state"] = ctl
                        plan["session_waited_s"] = round(time.time() - started, 1)
                        return True
                    reason = "could not bring WeChat to the foreground"
        if time.time() >= deadline:
            break
        time.sleep(float(cfg.get("session_poll_s", 3.0)))
    return fail(plan, "wechat session unavailable: %s" % reason, retryable=True)


def fit(text):
    """Whitespace-insensitive comparison key (Windows OCR puts spaces in CJK).

    2026-10-22：这里以前直接 `(text or "").split()`，一旦调用方把**异常对象**当
    文本传进来（`wxui.open_chat` 找「文件传输助手」时确实发生过），就会抛
    `AttributeError: 'AttributeError' object has no attribute 'split'`，
    把「定位失败」伪装成代码 bug。现在统一先转成 str。
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    return "".join(text.split()).lower()


# Windows OCR cannot read emoji and drops most symbols, and WeChat truncates a long name
# in the sidebar — so a group named ㊙️情🔞VIP💎508🉑👅🈲✈️ comes back as "情VIP508" (or split
# over two lines). Comparing raw strings can therefore never match, whatever the remark is.
_NAME_KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
NAME_MATCH_CLEAR = 0.9         # one side contains the other: trust it like today's partial
NAME_MATCH_MIN_RATIO = 0.7     # otherwise: this share of the shorter name, as one run
NAME_MATCH_MIN_CHARS = 3       # ...and at least this many characters in a row
NAME_MATCH_UNIQUE_MARGIN = 0.15  # a fuzzy winner must beat the runner-up by this much
NAME_MATCH_LENIENT = 0.55       # 同长度+首字相同（OCR 形近替身，如 测试群→测讠羊）；
                                # 低于 CLEAR，只供"唯一最佳"分支使用，闸门一概不认


def name_key(text):
    """Emoji/symbol/whitespace-insensitive key for comparing a chat name with OCR text."""
    return _NAME_KEEP.sub("", (text or "")).lower()


# Windows OCR 最常见的数字/字母认错（杏园6-508 → 「杏园6巧08」就是这样来的）。只用于
# 「整串折一折再比」这一步：两侧都折，所以像 Lisa 里的 l→1 不会单独造成误判；真正防错
# 的是调用方要求「折完相等且全屏唯一」。
_OCR_FOLD = str.maketrans({
    "巧": "5", "弓": "5", "亏": "5", "巳": "6", "己": "6",
    "〇": "0", "口": "0", "o": "0", "d": "0",
    "b": "8", "日": "8", "z": "2", "了": "7", "刁": "7", "l": "1", "i": "1",
})


def ocr_fold(text):
    """`name_key` plus the digit/letter shapes Windows OCR most often swaps."""
    return name_key(text).translate(_OCR_FOLD)


def _longest_common_run(a, b):
    """Length of the longest common substring of two normalized names."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def name_match(key, text):
    """0.0-1.0: how sure are we that this OCR/window text is the chat named `key`?

    1.0 = same after normalization, NAME_MATCH_CLEAR when one contains the other, else the
    longest shared run as a share of the shorter name. Everything below the thresholds is
    0.0, so `> 0` is the "matched" test.
    """
    a, b = name_key(key), name_key(text)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return NAME_MATCH_CLEAR
    run = _longest_common_run(a, b)
    if run >= NAME_MATCH_MIN_CHARS:
        ratio = run / float(min(len(a), len(b)))
        if ratio >= NAME_MATCH_MIN_RATIO:
            return ratio
    # 2026-10-21：「测试群」在侧边栏和聊天标题里被 Windows OCR 稳定读成「测讠羊」
    # （实测 1x/2x/3x/4x 放大结果一致，放大救不回来——所以只能改判定），只要有一个
    # 字读错，exact/containment 就全废，于是明明在列表第二行也报
    # "could not locate target row by OCR"。同长度 + 首字相同是这种"部首/形近替身"的
    # 典型特征；但 0.55 远低于 NAME_MATCH_CLEAR，因此它**只能**通过需要"同一屏里唯一
    # 最佳"的 fuzzy 分支（find_row / 标题复核）生效，凡是要求精确的闸门都不认它。
    # 2026-10-23：「桃桃真可爱」的旧备注「陈铭坚」靠这条宽匹配认领了「陈奥琦」，
    # 身份闸因此放行，发给桃桃的消息全敲进了陈奥琦。收紧成首尾都相同：
    # OCR 形近改写通常两端都对得上，而不同人的名字极少首尾同时撞车。
    if len(a) == len(b) >= 3 and a[0] == b[0] and a[-1] == b[-1]:
        return NAME_MATCH_LENIENT
    return 0.0


def name_matches_any(key, texts, allow_fuzzy=False):
    """True when any OCR text (or `key` inside their join) names the target.

    The join matters because WeChat shows a remarked chat as the remark with the real
    group name underneath: OCR returns them as two lines, so neither line holds the name.
    `allow_fuzzy` is off for the header/gate checks that decide whether typing is safe:
    there only exactness or containment may pass, never a "looks similar" score.
    """
    lines = [t for t in (texts or []) if t]

    def accept(score):
        # Fuzzy scores are only consulted where a caller asked for them.
        return score > 0 if allow_fuzzy else score >= NAME_MATCH_CLEAR

    for text in lines:
        if accept(name_match(key, text)):
            return True
    joined = " ".join(lines)
    if len(name_key(key)) >= NAME_MATCH_MIN_CHARS and accept(name_match(key, joined)):
        return True
    return False


# 2026-10-18（用户反馈：把「桃桃真可爱」的备注改成「陈铭坚」后，侧边栏 OCR 再也找不到那条
# 会话，消息直接发不出去）：WeFlow 的 displayName 是旧名字，侧边栏显示的却是新备注，于是
# 「用 API 名字找 UI 行」永远匹配不上。别名表让一条会话可以有几个合法拼写——手填在
# wxui-config.json 的 contact_aliases 里，或由一次「WeFlow 回读确认成功」的发送自动学到
# （logs/wxui-aliases.json），所以改备注只需要别名表里多一个名字，不需要改代码。
def _alias_pairs(cfg):
    """{normalized name: [other raw spellings]} from config + the learned alias file."""
    pairs = {}

    def add(a, b):
        if not a or not b or name_key(a) == name_key(b):
            return
        for src, dst in ((a, b), (b, a)):
            bucket = pairs.setdefault(name_key(src), [])
            if name_key(dst) not in {name_key(x) for x in bucket}:
                bucket.append(dst)

    for primary, others in (cfg.get("contact_aliases") or {}).items():
        for other in ([others] if isinstance(others, str) else (others or [])):
            add(primary, other)
    try:
        with open(_alias_learn_path(cfg), encoding="utf-8") as fh:
            learned = json.load(fh)
    except Exception:
        learned = {}
    if isinstance(learned, dict):
        for primary, others in learned.items():
            for other in ([others] if isinstance(others, str) else (others or [])):
                add(primary, other)
    return pairs


def _alias_learn_path(cfg):
    path = cfg.get("alias_learn_file") or os.path.join(ROOT, "logs", "wxui-aliases.json")
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def alias_names(cfg, target):
    """Other known spellings of `target`, primary first (a rename adds one)."""
    return list(_alias_pairs(cfg).get(name_key(target), []))


def target_keys(cfg, target):
    """Every spelling that may name this chat in the sidebar or the chat header."""
    return [target] + alias_names(cfg, target)


def name_match_keys(keys, text):
    """Best name_match() over several spellings of the same chat."""
    return max((name_match(k, text) for k in keys if k), default=0.0)


def _learn_alias(cfg, target, plan):
    """Remember the sidebar spelling that actually opened `target`, after a verified send.

    Called only once WeFlow read-back proved the right talker received the message, so a
    learned alias can never point at a different chat.
    """
    picked = (plan.get("ocr") or {}).get("picked") or {}
    text = (picked.get("text") or "").strip()
    if not text or name_key(text) == name_key(target):
        return None
    path = _alias_learn_path(cfg)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    others = data.get(target)
    others = [others] if isinstance(others, str) else list(others or [])
    if name_key(text) in {name_key(x) for x in others}:
        return None
    others.append(text)
    data[target] = others
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        return None
    return text


def _result_text(res):
    """Text content of a windows-mcp tool result (where its refusals are reported)."""
    parts = []
    for item in (res or {}).get("content") or []:
        if item.get("type") == "text":
            parts.append(item.get("text") or "")
    return " ".join(p for p in parts if p).strip()


CONTROL_PREEMPTED = "CONTROL_PREEMPTED"


def preempted(err_text):
    """True when windows-mcp refused a tool call because the desktop is not ours.

    It refuses *every* tool that way (Screenshot included) while its control latch says
    the human is driving, so the empty screenshot is not a screen problem at all.
    """
    if not isinstance(err_text, str):
        err_text = "" if err_text is None else str(err_text)
    return CONTROL_PREEMPTED in err_text.upper()


def _brief(text, limit=180):
    """One-line excerpt of a JSON-ish windows-mcp status blob, for logs and reasons."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    flat = " ".join(text.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


_SHOT_ERR = [""]


def shot(client, region, keep_dir, tries=2):
    """Screenshot `region`; return the saved PNG path, or None.

    The refusal/failure text is kept in `_SHOT_ERR` (see `shot_ex`) because callers must
    tell "windows-mcp will not drive the desktop" apart from "OCR found nothing in it".
    """
    _SHOT_ERR[0] = ""
    cfg_now = config()
    if _native_on(cfg_now, "native_shots") and _foreground_is_wechat(cfg_now):
        # 原生 BitBlt 抓屏：局部区域 ~30ms，而 windows-mcp 的 Screenshot 走 JSON-RPC + base64
        # 要 ~240ms。抓不到就退回 MCP（例如区域越界、被最小化窗口挡住）。
        # 前提是微信确实在最前面：抓屏拿到的是**屏幕像素**，被别的窗口盖住就会把别人的 UI 读成
        # 微信的侧边栏/头部，然后点到别的窗口里去。
        try:
            os.makedirs(keep_dir, exist_ok=True)
            path = os.path.join(keep_dir, "wxui-%d.png" % time.time())
            return winio.grab(region, path)
        except Exception as exc:
            _SHOT_ERR[0] = "native grab failed: %s" % exc
    for attempt in range(tries):
        res = client.call("Screenshot", {"region": list(region)})
        for item in (res or {}).get("content") or []:
            if item.get("type") == "image":
                import base64
                os.makedirs(keep_dir, exist_ok=True)
                path = os.path.join(keep_dir, "wxui-%d.png" % time.time())
                with open(path, "wb") as fh:
                    fh.write(base64.b64decode(item.get("data") or ""))
                return path
        text = _result_text(res)
        if text:
            _SHOT_ERR[0] = text
        if attempt + 1 < tries:  # the first frame after a window restore can come back empty
            time.sleep(0.5)
    return None


def shot_ex(client, region, keep_dir, tries=2):
    """(png_path, refusal_text): screenshot `region`, and why it came back empty.

    A refused tool call returns a text result instead of an image (see `preempted`).
    Keeping that text is what lets callers say "windows-mcp will not drive the desktop"
    instead of the old, wrong "could not locate target row by OCR".
    """
    png = shot(client, region, keep_dir, tries)
    return png, ("" if png else _SHOT_ERR[0])


def _native_on(cfg, name, default=True):
    """配置开关 + 原生模块是否可用。"""
    return bool(cfg.get(name, default)) and winio is not None


def _foreground_is_wechat(cfg):
    """最前面的窗口是不是微信**主窗口**（原生抓屏只在这时可信）。

    侧栏/头部的偏移量都是按主窗口布局量出来的；如果前台是「被弹出成独立窗口的某个会话」，
    BitBlt 抓到的将是那个会话的像素，OCR 读出来的文字会被当成侧栏列表 → 点到别处去。
    所以这里要求前台 hwnd 就是主窗口（`find_wechat`），而不是「任何 Qt 微信窗口」。
    """
    if winio is None:
        return False
    try:
        hwnd, title, cls = winio.foreground_info()
    except Exception:
        return False
    # 注意 `native_shot_class_prefix: null` 也要落到默认值：str(None) == "None" 会让每次
    # 原生截图都被判「前台不是微信」，于是静默退回 windows-mcp（+240ms/张）。
    prefix = str(cfg.get("native_shot_class_prefix") or "Qt51514QWindowIcon")
    if prefix and not cls.startswith(prefix):
        return False
    if not title:
        return False
    if title == cfg["window_title"]:
        return True
    if not cfg.get("native_shot_require_main", True):
        return True
    main = find_wechat(cfg)  # 弹窗标题虽然是会话名，但只有主窗口才允许按侧栏偏移量抓屏
    return bool(main) and int(hwnd) == int(main)


def ui_click(client, cfg, point):
    """点一下屏幕坐标：原生 SendInput 优先（~20ms），windows-mcp 兜底（~580ms）。

    人为在用键鼠时原生层抛 HumanBusy，调用方按“桌面不归我们”处理，不跟人抢。
    """
    x, y = int(point[0]), int(point[1])
    if _native_on(cfg, "native_input"):
        winio.click(x, y)
        return "native"
    client.call("Click", {"loc": [x, y], "clicks": 1})
    return "mcp"


def ui_paste(client, cfg, point, text):
    """在坐标处粘贴一段文字（原生：点 + 写剪贴板 + Ctrl+V，一次约 40ms）。"""
    x, y = int(point[0]), int(point[1])
    focus_wait = float(cfg.get("search_focus_wait_s", 0.35))
    paste_wait = float(cfg.get("search_paste_wait_s", 0.7))
    if _native_on(cfg, "native_input"):
        winio.paste_text(x, y, text, focus_wait=focus_wait, paste_wait=paste_wait)
        return "native"
    client.call("Click", {"loc": [x, y], "clicks": 1})
    time.sleep(focus_wait)
    client.call("Clipboard", {"mode": "set", "text": text})
    client.call("Shortcut", {"shortcut": "ctrl+v"})
    time.sleep(paste_wait)
    return "mcp"


def ui_escape(client, cfg):
    """按 Escape 收起搜索面板。"""
    if _native_on(cfg, "native_input"):
        winio.press_escape()
        return "native"
    client.call("Shortcut", {"shortcut": "escape"})
    return "mcp"


def image_size(path):
    try:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except Exception:
        return None
    async def _size():
        f = await StorageFile.get_file_from_path_async(path)
        s = await f.open_async(FileAccessMode.READ)
        d = await BitmapDecoder.create_async(s)
        return int(d.pixel_width), int(d.pixel_height)
    return asyncio.run(_size())


def ocr_lines(path):
    import ocr
    res = asyncio.run(ocr.run(path))
    return res


def header_names_target(key, texts):
    """True when the open chat's header OCR names `key` (a name or a list of spellings).

    Exactness or containment on any line (or on the join of the lines, because a remarked
    chat shows the remark with the real group name underneath) is always enough. A merely
    similar line is accepted only when it is clearly the best one in the header — the row
    was already located from this same name, so "similar and unique" is what OCR noise
    (dropped emoji, a truncated title) leaves behind.
    """
    keys = [k for k in (key if isinstance(key, (list, tuple)) else [key]) if k]
    lines = [t for t in (texts or []) if t]
    if not keys:
        return False
    # 第一个是真实名称，后面是备注/历史别名。别名只接受**高置信**命中：
    # 事故里别名「陈铭坚」用 0.55 的宽匹配认领了「陈奥琦」。
    if name_matches_any(keys[0], lines):
        return True
    for k in keys[1:]:
        for t in lines:
            if k == t or k in t or t in k:
                return True
    # OCR 把数字读歪（6-508 → 6巧08）时，整串折一折再比；允许前缀命中，因为群标题后面
    # 常跟着成员数「(7)」。这一关只在前一行已由侧边栏选中该目标时才可能通过。
    fold_keys = {ocr_fold(k) for k in keys if ocr_fold(k)}
    for text in lines + ([" ".join(lines)] if len(lines) > 1 else []):
        folded = ocr_fold(text)
        if folded and any(folded == fk or folded.startswith(fk) for fk in fold_keys):
            return True
    scored = sorted((name_match_keys(keys, t)
                     for t in lines + ([" ".join(lines)] if len(lines) > 1 else [])),
                    reverse=True)
    if not scored or scored[0] <= 0:
        return False
    runner_up = scored[1] if len(scored) > 1 else 0.0
    return scored[0] - runner_up >= NAME_MATCH_UNIQUE_MARGIN


def _header_group_clean(text):
    """去掉群标题尾巴上的成员数：「测试群（3）」→「测试群」。"""
    return re.sub(r"\s*[（(]\s*\d+\s*[)）]\s*$", "", text or "").strip()


def _char_dice(a, b):
    """字符级 Dice 系数（按重数算交集）：容忍 OCR 多读/少读一个字。"""
    if not a or not b:
        return 0.0
    from collections import Counter
    inter = sum((Counter(a) & Counter(b)).values())
    return 2.0 * inter / float(len(a) + len(b))


def header_matches_surface(texts, surface, threshold=0.5):
    """聊天标题的 OCR 与「刚才点的那一行」是不是同一个名字。

    Windows OCR 对「测试群」这个短名很不稳定：侧边栏读成「测讠羊」，同一时刻聊天标题
    又读成「测讠胡羊（3）」——两边都不等于目标名，但两边互相同源（字符重合 3/4）。所以
    复核分两步：先按目标名/别名严格比，不中再退回「标题 ≈ 我点的这一行」，两者都过不了
    才拒绝打字。这一条只在侧边栏已经用唯一最佳匹配选中该行之后才会用到，不会放宽发错人。
    """
    s = name_key(_header_group_clean(surface))
    if not s:
        return False
    for t in (texts or []):
        tt = name_key(_header_group_clean(t))
        if not tt:
            continue
        if tt == s or s in tt or tt in s or _char_dice(tt, s) >= threshold:
            return True
    return False


# 侧栏当前滚动位置（只记我们自己滚的唯一状态机）：`at_top` 为 True 表示「上一次滚动把列表
# 带到了顶部」，下一次定位就不必再花 0.9s 回顶。人手滚过会由 human_busy 兜底重置。
_SCROLL_POS = {"at_top": True, "ts": 0.0, "serial": 0}


# 2026-10-21（用户反复实测「怎么还是点两次」）：一次 send() 可能重试多次，每次都走 open_chat
# 又点一下侧栏同一行；名字 OCR 读不准的会话（「测试群」→「测讠羊」）靠名字永远复核不过，
# 于是重试 = 再点一次鼠标，用户看到的就是「点两次」。这里记「刚给谁点过哪一行、点完头部
# 长什么样」，同一目标在 row_click_dedup_s 秒内再次进入 open_chat 时不再点击。
_CLICK_MEMO = {}


def _note_click(target, point, header_texts=None, why=""):
    """Record a row click (and log it) so repeated attempts cannot click twice."""
    rec = {"point": (int(point[0]), int(point[1])), "ts": time.time(),
           "header_texts": list(header_texts or [])}
    _CLICK_MEMO[target] = rec
    try:
        log_dir = os.path.join(ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "wxui-clicks.log"), "a", encoding="utf-8") as fh:
            fh.write("%s target=%s point=%s why=%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), target, rec["point"], why or "-"))
    except Exception:
        pass
    return rec

def _pixel_rgb(png_path: str, x: int, y: int):
    try:
        from PIL import Image
        with Image.open(png_path) as im:
            im = im.convert("RGB")
            if 0 <= x < im.width and 0 <= y < im.height:
                return im.getpixel((x, y))
    except Exception:  # noqa: BLE001
        return None
    return None


def _row_selected(png_path: str, point, win_rect, offsets=(-110, -90, 70, 90)):
    """侧栏这一行是不是「选中态」。

    微信 4.x 的选中行是整行饱和绿底，比头部 OCR 稳得多：群里名字常被读成
    「测 讠 胡 羊（3）」之类，名字比对必然失败，但绿底像素不会读错。
    返回 True/False/None（None = 判断不了）。
    """
    if not png_path or not point or not win_rect:
        return None
    x = int(point[0]) - int(win_rect[0])
    y = int(point[1]) - int(win_rect[1])
    best = None
    for dx in offsets:
        rgb = _pixel_rgb(png_path, x + dx, y)
        if rgb is None:
            continue
        r, g, b = rgb
        score = g - max(r, b)
        if best is None or score > best:
            best = score
    if best is None:
        return None
    return best >= 20



def selected_row_text(cfg, rect):
    """读出「侧栏里当前高亮选中的那一行」的名字（OCR 文本）。

    为什么需要它：头部 OCR 对私聊短名并不可靠（「文件传输助手」常被拆开），于是
    `header_looks_like` 的宽匹配会在**打开了别的会话**时误报「已经是目标」，
    接着 mic/发送就点到错的会话上（2026-10-22 实测：目标 文件传输助手，实际停在
    雾中人耍起，`open_chat` 却返回 `chat already on target`）。
    侧栏绿底高亮是像素事实，用它复核头部判断，比再加名字规则稳。
    """
    left, top = rect[0], rect[1]
    region = clamp_region(
        [left + cfg["sidebar_capture_x_offset"], top + cfg["sidebar_capture_y_offset"],
         left + cfg["sidebar_capture_x2_offset"], rect[3] - cfg["sidebar_capture_bottom_margin"]],
        rect,
    )
    png = shot(None, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        return None
    res = ocr_lines(_upscale_for_ocr(png, float(cfg.get("sidebar_ocr_scale", 2.0) or 1.0)))
    if not res.get("ok"):
        return None
    size = image_size(png)
    img_h = size[1] if size else (region[3] - region[1])
    scale_y = (region[3] - region[1]) / float(img_h or 1)
    for line in res.get("lines") or []:
        box_h = max((w["y"] + w["h"] for w in line.get("words") or []), default=line["y"] + 18) - line["y"]
        y = int(region[1] + (line["y"] + max(box_h, 18) / 2.0) * scale_y)
        point = (int(region[0]) + 60, y)
        if _row_selected(png, point, rect):
            return {"text": line.get("text") or "", "y": y}
    return None


def _row_text_of(row):
    """`selected_row_text` 返回 {"text","y"}；这里容忍直接传字符串的情况。"""
    if isinstance(row, dict):
        return row.get("text") or ""
    return row or ""


def _row_selected_live(cfg, rect, point):
    """现拍一小条侧栏行判断选中态（不落大图，~30ms）。"""
    if not point:
        return None
    try:
        box = (int(point[0]) - 140, int(point[1]) - 14, int(point[0]) + 140, int(point[1]) + 14)
        png = shot(None, box, os.path.join(ROOT, "logs", "wmcp"))
        if not png:
            return None
        return _row_selected(png, point, rect)
    except HumanBusy:
        raise
    except Exception:  # noqa: BLE001
        return None


def _note_scroll(notches, cfg):
    if notches < 0:
        _SCROLL_POS["at_top"] = False
    elif notches > 0 and notches >= int(cfg.get("sidebar_reset_notches", 24) or 24) * 0.9:
        _SCROLL_POS["at_top"] = True
    _SCROLL_POS["ts"] = time.time()
    _SCROLL_POS["serial"] += 1


def _side_scroll(client, cfg, hover, notches, wait_s=None):
    """滚侧栏：原生滚轮优先（一格 ~12ms），windows-mcp 的 Scroll 兜底（一次 4.6s）。

    2026-10-21：旧路径「滚 12 次到顶 + 每页一次 Scroll」全靠 MCP，一次 4.5s，于是一次定位
    要几十秒。SendInput 的 WHEEL 事件把同样的动作压到几十毫秒，翻页才可能进 2 秒预算。
    `notches` 正数向上、负数向下。
    """
    # 滚轮事件只送到前台窗口：微信不在最上层时滚它等于滚别人的窗口，
    # 于是「翻页了但列表没动」。先确认窗口在最上层，抢不到就直接放弃滚动。
    _ready = ensure_wechat_ready(cfg)
    if not _ready.get("ok"):
        return "not-foreground"
    notches = int(notches)
    if notches == 0:
        return "noop"
    if _native_on(cfg, "native_input") and cfg.get("sidebar_native_wheel", True):
        winio.wheel(notches, hover[0], hover[1])
        time.sleep(float(cfg.get("sidebar_wheel_wait_s", 0.15) if wait_s is None else wait_s))
        _note_scroll(notches, cfg)
        return "native"
    direction = "up" if notches > 0 else "down"
    client.call("Move", {"loc": hover})
    client.call("Scroll", {"loc": hover, "type": "vertical", "direction": direction,
                           "wheel_times": abs(notches)})
    time.sleep(float(cfg.get("sidebar_scroll_wait_s", 0.5) if wait_s is None else wait_s))
    return "mcp"


_PINNED_COLLAPSE_HINTS = ("折叠置顶", "展开置顶", "折叠的置顶", "查看更多", "查看全部",
                         "个置顶聊天", "置顶聊天", "个置顶")


def _expand_pinned_section(client, cfg, rect):
    """点开微信侧栏的「折叠置顶聊天」，让被折进去的置顶会话重新可见。

    2026-10-22：置顶会话一多，微信会把整段折成一行「折叠置顶聊天 N 个」，
    里面的会话（实测「文件传输助手」）在侧栏 OCR 里根本不存在，于是
    open_chat 永远报 `could not locate target row by OCR`。这里先找这一行，
    点一下展开，再让 find_row 重新扫一遍。
    """
    hwnd = find_wechat(cfg)
    if not hwnd:
        return False
    r = restored_rect(hwnd)
    if not rect_usable(r):
        return False
    left, top, right, bottom = r
    region = clamp_region(
        [left + cfg["sidebar_capture_x_offset"], top + cfg["sidebar_capture_y_offset"],
         left + cfg["sidebar_capture_x2_offset"], bottom - cfg["sidebar_capture_bottom_margin"]],
        r,
    )
    png, err = shot_ex(client, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        log.info("[wxui] 展开置顶失败：截图拿不到（%s）", _brief(err))
        return False
    scale_up = float(cfg.get("sidebar_ocr_scale", 2.0) or 1.0)
    ocr_png = _upscale_for_ocr(png, scale_up) if scale_up > 1.0 else png
    size = image_size(ocr_png)
    res = ocr_lines(ocr_png)
    if not res.get("ok"):
        return False
    img_w, img_h = size if size else (region[2] - region[0], region[3] - region[1])
    scale_y = (region[3] - region[1]) / float(img_h)
    scale_x = (region[2] - region[0]) / float(img_w)
    for line in res.get("lines") or []:
        text = fit(line.get("text") or "")
        if not text or not any(hint in text for hint in _PINNED_COLLAPSE_HINTS):
            continue
        box_h = max((w["y"] + w["h"] for w in (line.get("words") or [])),
                    default=line.get("y", 0) + 18) - line.get("y", 0)
        y_phys = region[1] + (line.get("y", 0) + max(box_h, 18) / 2.0) * scale_y
        x_phys = region[0] + (line.get("x", 0) + 40) * scale_x
        try:
            ui_click(client, cfg, (int(x_phys), int(y_phys)))
        except HumanBusy:
            raise
        except Exception as exc:                   # noqa: BLE001
            log.info("[wxui] 展开置顶点不动：%s", _brief(exc))
            return False
        time.sleep(0.45)
        _SCROLL_POS["at_top"] = True
        _SCROLL_POS["ts"] = time.time()
        log.info("[wxui] 🔓 已展开「%s」，重新扫描侧栏", line.get("text"))
        return True
    return False


def session_exists_in_weflow(cfg, target):
    """目标会话在 WeFlow 的会话列表里存在吗？用来区分「聊天列表里没有」和「根本没这个人」。"""
    try:
        res = api(cfg, "/api/v1/sessions", limit=300)
        sessions = res.get("sessions") or res.get("data") or []
        if not isinstance(sessions, list):
            return None
        keys = {fit(k) for k in target_keys(cfg, target)}
        for sess in sessions:
            names = [sess.get("displayName"), sess.get("nickname"), sess.get("username"), sess.get("name")]
            if any(fit(n) in keys for n in names if n):
                return {"displayName": sess.get("displayName") or sess.get("nickname"),
                        "username": sess.get("username")}
        return {}
    except Exception as exc:                                   # noqa: BLE001
        return {"error": str(exc)}


def _pixels_belong_to(hwnd, rect, probes=((0.35, 0.5), (0.5, 0.3), (0.5, 0.5), (0.5, 0.7), (0.7, 0.5))):
    """窗口矩形内抽样几个点，看屏幕上真正显示的是不是这个进程的窗口。

    仅靠 GetForegroundWindow 判断「微信在最上层」太严：有置顶小工具、输入法候选框或
    RDP/远程会话时前台句柄可能不是微信，但微信的像素仍然好好地摆在那儿，OCR 完全可用。
    真正会让 OCR 读错的是「微信被别的窗口盖住」，那用 WindowFromPoint 就能问出来。
    """
    user32 = ctypes.windll.user32
    try:
        target_pid = int(user32.GetWindowThreadProcessId(hwnd, None))
    except Exception:                                          # noqa: BLE001
        return None
    left, top, right, bottom = [int(v) for v in rect]
    ok = 0
    hits = []
    for fx, fy in probes:
        x = left + int((right - left) * fx)
        y = top + int((bottom - top) * fy)
        try:
            hwnd_at = int(user32.WindowFromPoint(ctypes.wintypes.POINT(x, y)) or 0)
            pid_at = int(user32.GetWindowThreadProcessId(hwnd_at, None)) if hwnd_at else 0
        except Exception:                                      # noqa: BLE001
            return None
        hits.append((x, y, hwnd_at, pid_at))
        if pid_at and pid_at == target_pid:
            ok += 1
    return {"ok": ok >= max(1, int(len(probes) * 0.6)), "hits": hits, "pid": target_pid,
            "covered": [h for h in hits if h[3] != target_pid]}


def ensure_wechat_ready(cfg, raise_it=True):
    """OCR / 点击之前的前置检查：微信窗口是否开着、是否在最上层。

    OCR 读的是屏幕像素（GDI BitBlt），被别的窗口盖住就会读到别人家 UI，于是出现
    「侧栏找不到会话」「头部认错人」「点到别的窗口」。所以每轮扫描/复核之前先确认：
      ① 窗口存在 ② 可见且未最小化 ③ 不是被遮挡（前台是它，或抽样点都落在它自己身上）
    不满足就先尝试唤起（SetForegroundWindow / AttachThreadInput / 最小化再还原 / 临时置顶），
    仍然被遮挡才返回 ok=False，让调用方跳过 OCR 而不是读脏数据。
    """
    info = {"ok": False, "raised": False}
    user32 = ctypes.windll.user32
    try:
        hwnd = find_wechat(cfg)
    except Exception as exc:                                   # noqa: BLE001
        info["reason"] = "find_wechat failed: %s" % exc
        return info
    if not hwnd:
        info["reason"] = "wechat main window not found"
        return info
    info["hwnd"] = int(hwnd)
    try:
        info["visible"] = bool(user32.IsWindowVisible(hwnd))
        info["iconic"] = bool(user32.IsIconic(hwnd))
    except Exception:                                          # noqa: BLE001
        info["visible"], info["iconic"] = True, False
    try:
        info["foreground"] = int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:                                          # noqa: BLE001
        info["foreground"] = False

    if raise_it and winio is not None and (not info["visible"] or info["iconic"] or not info["foreground"]):
        for attempt in ("raise", "restore", "topmost"):
            try:
                if attempt == "raise":
                    info["raised"] = bool(winio.raise_window(hwnd))
                elif attempt == "restore":
                    user32.ShowWindow(hwnd, 6)          # SW_MINIMIZE
                    time.sleep(0.12)
                    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
                else:
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)   # HWND_TOPMOST
                    user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # HWND_NOTOPMOST
            except Exception as exc:                           # noqa: BLE001
                info["%s_error" % attempt] = str(exc)
            try:
                info["foreground"] = int(user32.GetForegroundWindow() or 0) == int(hwnd)
            except Exception:                                  # noqa: BLE001
                pass
            if info["foreground"]:
                break
            time.sleep(float(cfg.get("ensure_foreground_wait_s", 0.12)))

    try:
        info["rect"] = list(restored_rect(hwnd))
    except Exception:                                          # noqa: BLE001
        info["rect"] = []
    on_top = None
    if info["rect"]:
        on_top = _pixels_belong_to(hwnd, info["rect"])
    info["on_top"] = on_top
    info["ok"] = bool(info["rect"]) and (info["foreground"] or bool(on_top and on_top.get("ok")))
    if not info["ok"]:
        info["reason"] = ("wechat window is covered by another window"
                          if info["visible"] else "wechat window is hidden or minimized")
    return info


def find_row(client, cfg, rect, target):
    """Click point of `target`'s sidebar row, located by OCR. Returns ((x, y), debug).

    The click lands on the matched name box itself, so neither the row pitch nor the
    first-row offset has to be exact (they drift as the list scrolls, the window is
    resized or WeChat changes its layout); the box x is used too, so a resized window
    no longer pushes the click into the chat pane.

    A long session list pushes chats below the fold, where no amount of OCR can see them
    (`sidebar_shows` in the failure text then lists only the neighbours). When the first
    screen has no match, the list is scrolled down and re-scanned a few times, then scrolled
    back to the top so the next call starts from a known place.
    """
    left, top, right, bottom = rect
    region = clamp_region(
        [left + cfg["sidebar_capture_x_offset"], top + cfg["sidebar_capture_y_offset"],
         left + cfg["sidebar_capture_x2_offset"], bottom - cfg["sidebar_capture_bottom_margin"]],
        rect,
    )
    pages = max(1, int(cfg.get("sidebar_scroll_pages", 4)))
    wheel_times = max(1, int(cfg.get("sidebar_scroll_wheel_times", 5)))
    wait_s = float(cfg.get("sidebar_scroll_wait_s", 0.5))
    hover = [int(region[0] + 40), int((region[1] + region[3]) // 2)]
    debug: dict = {}
    t_start = time.monotonic()
    timing: dict = {}
    # 先看当前视口：绝大多数发送是发给「最近聊过」的人，他们就停在列表顶部，直接认出来
    # 只要一次 OCR（~0.6s）；先回顶部反而每次都多花 0.3~0.6s 的滚动。
    at_top = bool(_SCROLL_POS["at_top"]) and (time.time() - _SCROLL_POS["ts"]) < 120
    skip_page0 = False
    if cfg.get("sidebar_scan_before_reset", True):
        point, seen_dbg = _scan_sidebar(client, cfg, region, top, target)
        timing["viewport_scan"] = int((time.monotonic() - t_start) * 1000)
        if point:
            seen_dbg["scanned_where"] = "current viewport"
            seen_dbg["ms"] = timing
            return point, seen_dbg
        # 侧栏本来就在顶部时，下面的「回顶」是空操作，page0 扫的就是刚扫过的同一帧：
        # 只要上一帧 OCR 正常读到了几行，就别再花 ~0.2s 重复 OCR 一次。
        rows_seen = [c for c in (seen_dbg.get("candidates") or []) if c.get("text")]
        skip_page0 = at_top and len(rows_seen) >= 5
    # 侧边栏可能停在任意位置（上一次失败、重发队列的若干轮、或人手动滚过）：
    # 直接在当前视口 OCR 就永远看不到列表顶部的会话（典型症状：新群/刚建的群
    # 明明在第一屏却报 "could not locate target row by OCR"）。所以每次定位都
    # 先滚回顶部，让扫描永远从同一个已知位置开始。原生滚轮下一趟到顶只要几十毫秒。
    if cfg.get("sidebar_reset_to_top", True) and not at_top:
        try:
            top_ok, chunks = _scroll_to_top(client, cfg, region, hover,
                                            os.path.join(ROOT, "logs", "wmcp"))
            debug["reset_driver"] = "wheel-chunks"
            debug["reset_chunks"] = chunks
            debug["reset_at_top"] = top_ok
            if not top_ok:
                debug["reset_warn"] = "list may not have reached the top"
        except HumanBusy:
            raise
        except Exception as exc:  # 滚不动就算了，扫描本身还会给出诊断
            debug["reset_driver"] = "failed: %s" % (_brief(exc),)
        timing["reset"] = int((time.monotonic() - t_start) * 1000)
    else:
        timing["reset"] = 0
    prev_top = None
    prev_frame_md5 = None
    page_ms = []
    for page in range(pages):
        t_page = time.monotonic()
        if page == 0 and skip_page0:
            point, debug = None, (seen_dbg or {})
            timing["page0_scan"] = 0
            debug["page0_skipped"] = "same viewport as the scan above"
        else:
            point, debug = _scan_sidebar(client, cfg, region, top, target)
            timing["page%d_scan" % page] = int((time.monotonic() - t_page) * 1000)
        if not point and not (page == 0 and skip_page0) and \
                float(cfg.get("sidebar_ocr_scale", 2.0) or 1.0) < 3.0:
            # 这一屏还认不出来：再放大一档重扫一次（小字短名读错时通常这一步就成）。
            # 准确性优先：翻页后的每一屏也做同样的放大重试，命中率不够就会漏会话。
            bigger = dict(cfg)
            bigger["sidebar_ocr_scale"] = 3.0
            point, debug = _scan_sidebar(client, bigger, region, top, target)
            debug["retry_scale"] = 3.0
        if point:
            if page:
                debug["scrolled_pages"] = page
            debug["ms"] = timing
            return point, debug
        if page + 1 >= pages:
            break
        # 已经滚到底：最后一行不再变化时继续滚只是白花时间，还会把列表
        # 留在奇怪的位置，让下一次定位又从中间开始。
        rows = sorted((c for c in (debug.get("candidates") or []) if c.get("text")),
                      key=lambda c: c["y"])
        top_text = fit(rows[0]["text"]) if rows else None
        if top_text and prev_top and top_text == prev_top:
            debug["list_bottom"] = True
            break
        prev_top = top_text
        # 目标行在可视区之外：把鼠标移到侧边栏上，再滚一屏重新 OCR。
        _side_scroll(client, cfg, hover, -wheel_times)
        page_png, page_md5, page_changed = _settled_frame(
            client, cfg, region, os.path.join(ROOT, "logs", "wmcp"), prev_frame_md5,
            timeout=float(cfg.get("sidebar_settle_timeout_s", 0.25) or 0.25))
        prev_frame_md5 = page_md5
        if page_png and not page_changed:
            # 画面和上一页完全一样：列表已经到底，再翻也没有新行，直接收工。
            debug["list_bottom"] = True
            break
        if page_png:
            point, debug = _scan_sidebar(client, cfg, region, top, target, page_png)
            timing["page%d_scan" % (page + 1)] = int((time.monotonic() - t_page) * 1000)
            if point:
                debug["scrolled_pages"] = page + 1
                debug["ms"] = timing
                return point, debug
    if pages > 1:
        # 没找到也把列表滚回顶部，下一次探测不必从中间开始。
        try:
            _scroll_to_top(client, cfg, region, hover, os.path.join(ROOT, "logs", "wmcp"))
        except Exception:
            pass
    # 第二遍：上面那次「滚到底 + 逐页 OCR」偶尔会踩到微信的滚动动画（一帧抓早/抓晚，
    # 结果整页都对不上），失败前再从列表顶部完整确认一次，避免把「其实在列表里」的会话
    # 判成找不到（群发失败会把消息塞进重发队列，代价远高于一次 0.4s 的复扫）。
    if pages > 1 and cfg.get("locate_second_pass", True):
        timing["second_pass"] = True
        for page2 in range(pages):
            point2, dbg2 = _scan_sidebar(client, cfg, region, top, target)
            if point2:
                dbg2["ms"] = timing
                dbg2["scanned_where"] = "second pass"
                if page2:
                    dbg2["scrolled_pages"] = page2
                return point2, dbg2
            if page2 + 1 >= pages:
                break
            _side_scroll(client, cfg, hover, -wheel_times)
        debug["second_pass_failed"] = True
        try:
            _scroll_to_top(client, cfg, region, hover, os.path.join(ROOT, "logs", "wmcp"))
        except Exception:
            pass
    debug["scrolled_pages"] = pages
    debug["ms"] = timing
    return None, debug


def _upscale_for_ocr(png, scale):
    """把侧边栏截图放大后再 OCR。

    微信会话名是 12px 左右的小字，Windows OCR 对「测试群」这种短名会读成
    「是测讠羊」——只要有一个字读错，整行匹配（exact/partial）就全废，于是明明
    在第一屏也报 "could not locate target row by OCR"。放大 2 倍基本能救回来，
    而坐标映射本来就用截图尺寸换算，放大不影响落点。
    """
    try:
        from PIL import Image as _Image
        with _Image.open(png) as im:
            big = im.convert("RGB").resize(
                (int(im.width * scale), int(im.height * scale)), _Image.LANCZOS)
            out = os.path.splitext(png)[0] + "@%gx.png" % scale
            big.save(out)
        return out
    except Exception:
        return png


_FRAME_GRID = (120, 240)  # 侧栏缩略图的宽高，用来比较「画面有没有动」
_FRAME_DIFF_TOL = 0.004     # 允许 0.4% 的格子有差异：静止画面里仍有 1 级灰度的抖动


def _frame_vec(png):
    """把一帧缩成灰度缩略向量，用于判断侧栏画面有没有变化。

    2026-10-22：原来是比文件 md5，实测同一静止画面连续 10 帧：最大级差 1、平均差 0.001、
    差异超过 16 级的像素占 0.0000% —— 像素几乎完全相同，md5 却每帧都变，于是
    `_sidebar_stable` 每次都等满 timeout 返回 False，回顶/翻页/点击前各白花 0.45s。
    改成「缩略图 + 容差比较」：只有真正滚动（整页文字换位）才算变。
    """
    try:
        from PIL import Image
        with Image.open(png) as im:
            return im.convert("L").resize(_FRAME_GRID).tobytes()
    except Exception:
        return None


def _frames_same(a, b):
    """两帧是否算同一画面（容忍静止画面里的 1 级灰度抖动）。"""
    if not a or not b or len(a) != len(b):
        return False
    diff = 0
    limit = int(len(a) * _FRAME_DIFF_TOL)
    for i in range(len(a)):
        if abs(a[i] - b[i]) > 8:
            diff += 1
            if diff > limit:
                return False
    return True


def _scroll_to_top(client, cfg, region, hover, keep_dir, chunk=None, max_chunks=4):
    """把会话列表滚到顶部，并返回 (是否到顶, 用了几趟)。

    「滚固定格数」在列表长短/惯性不同的机器上不可靠：格数不够就停在中间，随后 page 扫描
    从中间往下翻，比它更靠上的会话永远看不到 —— 表现为偶发「找不到目标」或退化成第二遍
    全量扫描（实测 3.1s / 5.2s）。这里改成「滚一趟看画面还变不变」：不变就是到顶了。
    """
    chunk = int(chunk if chunk is not None else cfg.get("sidebar_reset_chunk_notches", 12))
    prev = None
    for i in range(max_chunks):
        _side_scroll(client, cfg, hover, chunk)
        png = shot(client, region, keep_dir, tries=1)
        if not png:
            return False, i
        vec = _frame_vec(png)
        if prev is not None and _frames_same(vec, prev):
            return True, i + 1
        prev = vec
    return False, max_chunks


def _sidebar_stable(client, cfg, region, keep_dir, timeout=0.45, step=0.05):
    """侧栏是否已经停下来（连拍两帧像素一致）。

    2026-10-21：点开一个会话时微信会把那一行往列表顶部做滚动动画。动画中间截的那一帧里，
    别人的行位置还没定，OCR 读出来的 y 是过渡位置 —— 点下去就落到相邻一行（实测点「雾中人
    耍起」打开了「文件传输助手」），头部复核失败，消息被塞进重发队列。滚完再截一次即可，
    不必靠“补点一次”那种会乱动鼠标的补救。
    """
    prev = None
    deadline = time.monotonic() + timeout
    while True:
        png = shot(client, region, keep_dir, tries=1)
        if not png:
            return False
        cur = _frame_vec(png)
        if cur is None:
            return False
        if prev is not None and _frames_same(cur, prev):
            return True
        prev = cur
        if time.monotonic() >= deadline:
            return False
        time.sleep(step)


def _settled_frame(client, cfg, region, keep_dir, prev_md5, timeout=0.25, step=0.04):
    """滚一屏之后，返回「画面已经和上一页不同」的那一帧及其 md5。

    原来是滚完先调 `_sidebar_stable` 连拍两帧验稳定，再截第三帧去 OCR —— 每页三次截图。
    这里只截一帧：和上一页像素一致就说明列表还没动（动画起步/滚轮没吃到），隔 40ms 再截；
    一旦不同就已经滚到位，这一帧直接拿去 OCR。
    """
    deadline = time.monotonic() + timeout
    while True:
        png = shot(client, region, keep_dir, tries=1)
        if not png:
            return None, None, False
        md5 = _frame_vec(png)
        if prev_md5 is None or not _frames_same(md5, prev_md5):
            return png, md5, True
        if time.monotonic() >= deadline:
            # 画面等满 timeout 还没变：多半已经滚到底了（再滚也没有新内容）。
            return png, md5, False
        time.sleep(step)


def _scan_sidebar(client, cfg, region, top, target, png_path=None):
    """One OCR pass over the visible sidebar; ((x, y), debug) or (None, debug).

    `png_path` lets the caller hand in a frame it already grabbed while waiting for the
    list to settle — one screenshot per page instead of two.
    """
    _ready = ensure_wechat_ready(cfg)
    if not _ready.get("ok"):
        return None, {"error": "wechat window is not in the foreground", "window": _ready}
    if png_path:
        png, err = png_path, None
    else:
        png, err = shot_ex(client, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        return None, {"error": "no image from Screenshot (%s)" % (_brief(err) or "region off screen?"),
                      "region": [int(v) for v in region],
                      "control_preempted": bool(preempted(err))}
    ocr_png = png
    scale_up = float(cfg.get("sidebar_ocr_scale", 2.0) or 1.0)
    if scale_up > 1.0:
        ocr_png = _upscale_for_ocr(png, scale_up)
    size = image_size(ocr_png)
    res = ocr_lines(ocr_png)
    if not res.get("ok"):
        return None, {"error": "ocr failed", "region": [int(v) for v in region], "detail": res}
    img_w, img_h = size if size else (region[2] - region[0], region[3] - region[1])
    scale_y = (region[3] - region[1]) / float(img_h)
    scale_x = (region[2] - region[0]) / float(img_w)
    key = fit(target)
    # A renamed chat keeps its old API name, so an alias is accepted — but only as a
    # whole-line match: a partial hit on an alias would also match a *preview* line such
    # as "陈铭坚：嗯" inside a group row, and clicking that opens the wrong chat.
    keys = target_keys(cfg, target)
    exact_keys = {fit(k) for k in keys}
    candidates = []
    for line in res["lines"]:
        text = fit(line["text"])
        if not text:
            continue
        box_h = max((w["y"] + w["h"] for w in line["words"]), default=line["y"] + 18) - line["y"]
        y_phys = region[1] + (line["y"] + max(box_h, 18) / 2.0) * scale_y
        x_phys = region[0] + line["x"] * scale_x
        candidates.append({"text": line["text"], "x": int(x_phys), "y": int(y_phys),
                           "index": round((y_phys - (top + cfg["first_row_offset_px"]))
                                          / float(cfg["row_pitch_px"])),
                           "exact": text in exact_keys,
                           "partial": key in text or text in key,
                           "fold": ocr_fold(line["text"]) == ocr_fold(target),
                           "score": round(name_match(target, line["text"]), 3)})
    exact = [c for c in candidates if c["exact"]]
    # 2026-10-22 事故：目标「天文学家」在列表里已被删掉，但群「杏园6-508」的预览行是
    # 「天文学家：我猜又断电了」，partial 命中它就去点那一行 → 打开错的会话，
    # 头部复核失败后白白多一轮。预览行（带冒号）永远不是会话名，partial 里也剔掉。
    partial = [c for c in candidates if c["partial"]
               and not any(ch in c["text"] for ch in "：:")]

    pick = (exact or partial)
    fuzzy = False
    folded = False
    lenient = False
    if not pick:
        # OCR 把 508 读成「巧08」这类：整串折一折再比。必须全屏唯一，否则宁可继续找。
        fold_hits = [c for c in candidates if c["fold"]]
        if len(fold_hits) == 1:
            pick, folded = fold_hits, True
    if not pick:
        # Emoji-laden or truncated names never satisfy exact/partial, so fall back to a
        # similarity match — but only when one row is clearly the best candidate, since a
        # wrong pick would click the wrong chat. 带冒号的是「某某：最后一句」预览行，永远
        # 不是会话名，先剔掉（同长度首字相同的宽匹配最容易误吃这种行）。
        scored = sorted((c for c in candidates
                         if c["score"] > 0 and not any(ch in c["text"] for ch in "：:")
                         and 2 <= len(fit(c["text"])) <= 24),
                        key=lambda c: (-c["score"], c["y"]))
        if scored:
            runner_up = scored[1]["score"] if len(scored) > 1 else 0.0
            if scored[0]["score"] - runner_up >= NAME_MATCH_UNIQUE_MARGIN:
                pick, fuzzy = [scored[0]], True
                if scored[0]["score"] < NAME_MATCH_CLEAR:
                    lenient = True
    debug = {"png": png, "engine": res.get("engine"), "candidates": candidates[:40],
             "key": key, "keys": [fit(k) for k in keys], "fuzzy": fuzzy, "folded": folded,
             "lenient": lenient}
    if not pick:
        return None, debug
    pick.sort(key=lambda c: (not c["exact"], c["y"]))
    best = pick[0]
    debug["picked"] = {"text": best["text"], "score": best.get("score"), "fuzzy": fuzzy,
                       "folded": folded, "lenient": lenient, "alias": fit(best["text"]) != key}
    # Click on the matched name box, not a fixed offset from the window edge: after a
    # resize (or a different display scale) a fixed x can land in the chat pane, and a
    # fixed y can land on the row above. Small biases keep the point off the box border.
    x_pick = int(best["x"]) + int(cfg.get("row_click_x_bias_px", 12))
    y_pick = int(best["y"]) + int(cfg.get("row_click_y_bias_px", 6))
    x_click = max(region[0] + 4, min(region[2] - 4, x_pick))
    y_click = max(region[1] + 4, min(region[3] - 4, y_pick))
    debug["click"] = {"x": x_click, "y": y_click,
                      "from_box": [int(best["x"]), int(best["y"])]}
    return (int(x_click), int(y_click)), debug


def row_point(cfg, rect, index):
    """Physical coordinates of session-list row `index` (0 = top of the list)."""
    left, top, right, bottom = rect
    x = left + int(cfg["sidebar_x_offset_px"])
    y = top + int(cfg["first_row_offset_px"]) + index * int(cfg["row_pitch_px"])
    if not (top < y < bottom - 10):
        raise SystemExit(json.dumps({"ok": False, "error": "row %d outside window" % index}))
    return x, y


def talker_for(cfg, target, sessions):
    keys = target_keys(cfg, target)
    keyset = {fit(k) for k in keys}
    best_score, best_user = 0.0, None
    for s in sessions:
        if fit(s.get("displayName")) in keyset:
            return s.get("username")
        score = name_match_keys(keys, s.get("displayName"))
        if score > best_score:
            best_score, best_user = score, s.get("username")
    if best_user and best_score >= NAME_MATCH_CLEAR:
        # Same chat, spelled differently (the session list may show the remark while the
        # target is the real group name). Read-back only: a wrong talker cannot mis-send,
        # it just fails verification.
        return best_user
    # Service accounts and official chats are missing from the session list but
    # still resolvable as contacts (needed to read the message back).
    try:
        contacts = api(cfg, "/api/v1/contacts", keyword=target, limit=20).get("contacts", [])
    except Exception:
        return None
    fuzzy_user, fuzzy_score = None, 0.0
    for c in contacts:
        names = (c.get("displayName"), c.get("remark"))
        if any(fit(n) in keyset for n in names):
            return c.get("username")
        score = max(name_match_keys(keys, n) for n in names)
        if score > fuzzy_score:
            fuzzy_score, fuzzy_user = score, c.get("username")
    if fuzzy_user and fuzzy_score >= NAME_MATCH_CLEAR:
        return fuzzy_user
    return None


def last_message(cfg, talker):
    msgs = api(cfg, "/api/v1/messages", talker=talker, limit=1).get("messages", [])
    return msgs[-1] if msgs else None


def norm(text):
    """Punctuation/whitespace-insensitive key for comparing message text.

    WeChat stores what it actually rendered, so a sent message can read back with an
    extra full-width comma or wrapped line breaks. Comparing exactly would report a
    failure for a delivered message and the caller would resend it (duplicate).
    """
    return re.sub(r"[\s,，.。、!！?？~～…\"'“”‘’()（）【】\[\]:：;；<>《》\-—_*|]+",
                  "", (text or "")).lower()


def verify(cfg, talker, text, deadline, since_local_id=0, since_time=None):
    """The outgoing text message read back from WeChat's DB, or None.

    since_time (epoch 秒) 是兜底：WeFlow 的 localId 偶尔不严格递增，或者同一条
    长文本被拆成两条落库，只看 localId 会把「其实已经送到」判成没送到，然后无限重发
    （2026-10-22 实测同一条消息重发 8 次）。所以 localId 不够新时再比 createTime。
    """
    key = norm(text)
    if not key:
        return None
    while time.time() < deadline:
        msgs = api(cfg, "/api/v1/messages", talker=talker, limit=10).get("messages", [])
        for m in reversed(msgs):
            if m.get("isSend") != 1:
                continue
            if since_time and (m.get("createTime") or 0) < since_time:
                continue
            if since_local_id and (m.get("localId") or 0) <= since_local_id:
                continue
            body = norm(m.get("content"))
            if key in body:
                return m
            # 2026-10-22：带 emoji 的长文本（例如插件发的「…已发送。\n\n📊 本次配置…」）
            # 落库时 emoji 的变体选择符常被微信改写，整串 norm 之后就对不上了，于是
            # 「明明已经送达」被反复判成失败、消息被重发好几次。退化到「前 40 字一致」。
            if len(key) >= 16 and key[:40] in body:
                return m
        # 2026-10-16: 回读轮询间隔可配（默认 1.0s）。分段发送时每条消息都要等一轮回读，
        # 0.6s 的轮询让「消息已到库」的平均等待少 0.2~0.4s，而回读是只读的、代价极低。
        time.sleep(max(0.1, float(cfg.get("verify_poll_s", 1.0))))
    return None


# WeChat 出站消息的 localType：3 = 图片，47 = 表情/自定义贴纸，低字节 49 = appmsg（文件等）。
# 两个都是 2026-10-15 实测踩出来的：
#   * 47 —— 小 GIF 走「文件拖放 → Ctrl+V」时，微信把它当**自定义表情**发出（content="[表情]"）；
#   * 49 —— 大 GIF（1920x1080 / 2160x1620）不符合表情规则，微信改当**文件**发出，
#            content 是 `<msg><appmsg …><type>6</type><title>原文件名</title><appattach><totallen>原字节数`。
#     旧实现只认 localType==3，上面两种都被判「未送达」→ 重试 3 次 → 同一张图发 3 遍。
IMAGE_LOCAL_TYPES = (3, 47)
IMAGE_MEDIA_TYPES = ("image", "emoji", "sticker")
APPMSG_LOCAL_TYPE = 49


def file_md5(path):
    """md5 of a local file (WeChat records this for每张发出去的表情/文件)."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_outgoing(m, expect_name=None, expect_md5=None):
    """『这条出站消息是不是我刚发的那张图？』→ 'image' / 'sticker' / 'emoticon' / 'file' / None。

    微信 PC 对同一个 GIF 有三种落法（2026-10-15 实测）：
      * localType 47                      —— 小图当自定义表情，content="[表情]"
      * appmsg <type>8</type> + emoticonmd5 —— 尺寸合格的大图当**表情**上传（原始字节，totallen=原大小）
      * appmsg <type>6</type> + title/md5   —— 超限的图当**文件**发（原始字节，totallen=原大小）
    `expect_name` / `expect_md5` 用来确认文件/表情形态确实是**我们这一张**：
    appmsg 不带文件名时靠 md5 认，否则任何转发/链接都会被当成「图片已送达」，验证就失去意义。
    """
    if m.get("isSend") != 1:
        return None
    local_type = m.get("localType")
    media_type = str(m.get("mediaType") or "")
    content = str(m.get("content") or "")
    if local_type == 3 or (media_type in IMAGE_MEDIA_TYPES and local_type != 47):
        return "image" if local_type == 3 else "sticker"
    if local_type == 47:
        return "sticker"
    if not (isinstance(local_type, int) and (local_type & 0xFF) == APPMSG_LOCAL_TYPE):
        return None
    if "<appmsg" not in content and "<appattach" not in content:
        return None
    md5_hit = bool(expect_md5) and (expect_md5 in content)
    name_hit = False
    if expect_name:
        stem = os.path.splitext(os.path.basename(expect_name))[0]
        name_hit = bool(stem) and (stem in content)
    if not (md5_hit or name_hit):
        return None
    appmsg_type = re.search(r"<type>(\d+)</type>", content)
    if appmsg_type and appmsg_type.group(1) == "8":
        return "emoticon"
    return "file"


def verify_image(cfg, talker, deadline, since_local_id, expect_name=None, expect_md5=None,
                 since_time=None):
    """A new outgoing image/emoji/file message read back from WeChat's DB, or None.

    since_time 与 verify() 同理：文件卡片落库用的常是哈希名 / 显示名，只看 localId
    或文件名会把已送达判成失败。带 since_time 时额外接受「同一时间窗内、扩展名一致」
    的出站文件消息。
    """
    ext = os.path.splitext(str(expect_name or ""))[1].lower()
    while time.time() < deadline:
        msgs = api(cfg, "/api/v1/messages", talker=talker, limit=10).get("messages", [])
        for m in reversed(msgs):
            fresh = True
            if since_local_id and (m.get("localId") or 0) <= since_local_id:
                fresh = bool(since_time and (m.get("createTime") or 0) >= since_time)
            if not fresh:
                continue
            if classify_outgoing(m, expect_name, expect_md5):
                return m
            if since_time and ext and (m.get("createTime") or 0) >= since_time:
                if m.get("isSend") == 1 and ext in (m.get("content") or "").lower():
                    return m
        time.sleep(1.0)
    return None


def is_animated_image(path):
    """True when the file is (or may be) an animated image: GIF/WebP.

    无法判定时按动图处理 —— 文件拖放对静态图同样有效，而 SetImage 对动图一定丢帧。
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".gif", ".webp"):
        return False
    try:
        from PIL import Image as _PILImage  # 可选依赖：有就精确判断帧数
        with _PILImage.open(path) as im:
            return int(getattr(im, "n_frames", 1) or 1) > 1
    except Exception:
        return True


def set_clipboard_image(path, animated=None):
    """Put an image file on the Windows clipboard (WeChat turns Ctrl+V into an image).

    静态图走 `Clipboard::SetImage`（位图）。动图必须走 `Clipboard::SetFileDropList`
    （文件拖放列表）：SetImage 只放上第一帧，微信粘到的就是一张静止图 —— 这正是
    「GIF 发出去不动」的直接原因。

    文件路径经环境变量 `WXUI_CLIP_PATH` 传入 PowerShell，不做字符串插值，避免路径
    含单引号时的转义问题（安全评审 R1）。
    """
    abs_path = os.path.abspath(path)
    if animated is None:
        animated = is_animated_image(abs_path)
    if animated:
        ps = ("Add-Type -AssemblyName System.Windows.Forms; "
              "$files = New-Object System.Collections.Specialized.StringCollection; "
              "[void]$files.Add($env:WXUI_CLIP_PATH); "
              "[System.Windows.Forms.Clipboard]::SetFileDropList($files)")
    else:
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
              "$img=[System.Drawing.Image]::FromFile($env:WXUI_CLIP_PATH); "
              "[System.Windows.Forms.Clipboard]::SetImage($img); $img.Dispose()")
    env = dict(os.environ)
    env["WXUI_CLIP_PATH"] = abs_path
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, env=env)
    return r.returncode == 0, (r.stderr or "").strip()[:200]


def chat_header_matches(client, cfg, rect, target):
    """True when the open chat's header shows `target`.

    WeChat 4.x may show a conversation either in its own top-level window or embedded in
    the main window, so the header text (not a window title) is what proves the right
    chat is open — and that is what stops a wrong-recipient send.
    """
    _ready = ensure_wechat_ready(cfg)
    if not _ready.get("ok"):
        return False, {"error": "wechat window is not in the foreground", "window": _ready,
                       "texts": [], "ocr_tries": 0}
    left, top, right, bottom = rect
    region = clamp_region(
        [left + cfg["chat_header_x_offset_px"], top + cfg["chat_header_y_offset_px"],
         left + cfg["chat_header_x2_offset_px"], top + cfg["chat_header_y2_offset_px"]],
        rect,
    )
    png = shot(client, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        return False, {"error": "no header image"}
    # 2026-10-21 实测：Windows OCR 对同一行字并不稳定，而且**放大不一定更好**——
    # 「陈奥琦」1x 返回空、2x 才读得出来；「严正阳」1x 读得对、2x 反而变空。所以这里按
    # 档位依次试，取第一个非空的：空结果绝不能被当成“不是目标”（那会把明明已经打开的会话
    # 判失败、还多挨一次点击）。三档都空才补抓新帧。
    scales = [float(s) for s in (cfg.get("header_ocr_scales") or [1.0, 2.0, 3.0])]
    retries = max(1, int(cfg.get("header_empty_retries", 3)))
    wait_s = float(cfg.get("header_empty_wait_s", 0.22))
    tries = 0
    texts = []
    res = {}
    while tries < retries:
        for scale in scales:
            ocr_png = _upscale_for_ocr(png, scale) if scale > 1.0 else png
            res = ocr_lines(ocr_png)
            texts = [ln["text"] for ln in res.get("lines", [])] if res.get("ok") else []
            if texts:
                break
        tries += 1
        if texts:
            break
        time.sleep(wait_s)
        new_png = shot(client, region, os.path.join(ROOT, "logs", "wmcp"))
        if not new_png:
            break
        png = new_png
    key = fit(target)
    matched = header_names_target(target_keys(cfg, target), texts)
    return matched, {"png": png, "texts": texts[:6], "key": key, "matched": matched,
                     "ocr_tries": tries}


# 2026-10-16（用户要求：分段回复不要每次重新验证窗口位置和名字）：
# 常驻 worker（`wxui.py worker`）里记着「聊天窗口现在停在谁身上」，同一联系人的分段回复
# 之间（AstrBot 每段一次 HTTP 调用，间隔几秒）不再扫侧边栏、也不再点行，只做便宜的
# ctypes 校验（窗口还在、仍在最前面、前景类是微信）后直接粘贴发送。
# 一次性模式（`send` 子命令）里这份状态随进程一起消失，行为与过去完全一致。
# 安全边界：①只在目标相同、且窗口矩形没变时才复用；②距上次「确认头部名字」超过
# warm_header_recheck_s 秒就重新 OCR 一次聊天头部（小区域，很便宜）；③每条消息仍然
# 走 WeFlow 回读验证，发错人不会被当成成功。
_WARM = {"target": "", "at": 0.0, "header_at": 0.0, "rect": None, "clean": "", "clean_at": 0.0}


def _warm_get(cfg, target, rect):
    """(reuse, header_recent): 当前打开的会话是否仍可信地是 `target`。"""
    if not target or target != _WARM["target"]:
        return False, False
    if _WARM["rect"] and rect and tuple(rect) != tuple(_WARM["rect"]):
        return False, False  # 窗口大小/位置变了：布局可能也变了，走完整路径更稳
    now = time.time()
    if now - _WARM["at"] > float(cfg.get("warm_reuse_s", 45)):
        return False, False
    return True, (now - _WARM["header_at"]) <= float(cfg.get("warm_header_recheck_s", 10))


def _warm_mark(target, rect=None, header_verified=False, clean=False):
    """Remember that `target`'s chat is the one open in the WeChat window."""
    _WARM["target"] = target or ""
    _WARM["at"] = time.time()
    if rect:
        _WARM["rect"] = tuple(rect)
    if header_verified:
        _WARM["header_at"] = time.time()
    if clean:
        # 「刚刚给这个联系人成功发过一条」：输入框一定是空的（回车已把它清掉），窗口也一定
        # 还在同一位置。下一条消息正是靠这个标记才敢省掉会话探测。
        _WARM["clean"] = target or ""
        _WARM["clean_at"] = time.time()


def input_box_trusted_clean(cfg, target):
    """输入框能不能只按一个 delete 就当清空？（本进程刚给同一联系人验证送达过）

    `_WARM["clean"]` 只在「回读确认送达」之后才被写上，所以这个标记为真时，输入框一定是
    回车清空后的状态、且键盘焦点还在微信的消息框里。此时再 click + ctrl+a + delete（≈2.1s）
    纯属重复劳动；直接 delete 一次（≈0.55s）即可。被中断的发送拿不到这个标记，因此「旧文本
    拼到新文本前面」的风险不存在。
    """
    if not cfg.get("input_fast_clear", True) or not target:
        return False
    if not _WARM["rect"] or _WARM["clean"] != target:
        return False
    if time.time() - _WARM["clean_at"] > float(cfg.get("warm_reuse_s", 45)):
        return False
    return bool(_warm_get(cfg, target, _WARM["rect"])[0])


def _warm_forget_clean():
    """上一次发送没有拿到回读确认 —— 输入框里可能留着半截文本，不能再信任它。"""
    _WARM["clean"] = ""


def warm_session_trusted(cfg, target, plan):
    """热路径上能不能省掉 wait_for_session（会话探测）？

    条件很紧：同一个联系人，上一动作是**本进程内一次已验证送达**，窗口矩形没变，且在
    warm_reuse_s 秒内。满足时，wait_for_session 要确认的那些事（窗口在原位、微信在前台、
    桌面没被人抢走）刚刚才由一次成功的粘贴+回读证明过，再花一次 windows-mcp 调用（约 0.6s）
    去问一遍不划算。

    省掉它不会让失败变得不安全：真正的输入动作仍有 windows-mcp 自己的「人正在操作」保护，
    回读校验也照旧，校验不过照样进重发队列。
    """
    if not cfg.get("warm_skip_session_probe", True) or not target:
        return False
    if not _WARM["rect"] or _WARM["clean"] != target:
        return False
    reuse, _ = _warm_get(cfg, target, _WARM["rect"])
    if not reuse:
        return False
    if time.time() - _WARM["clean_at"] > float(cfg.get("warm_reuse_s", 45)):
        return False
    plan["window_rect"] = list(_WARM["rect"])
    plan["control_state"] = "assumed (warm session)"
    plan["session_waited_s"] = 0.0
    plan["session_reused"] = True
    return True


def _chat_ready_to_type(cfg, plan, target):
    """Make sure the target's chat (or the main window) really owns the keyboard.

    Shared by every path that has already decided the right conversation is open, so the
    "refuse to type blind" checks can never drift apart.

    2026-10-22 事故（「卷子分完了没」被连发到陈奥琦那边 6 遍）：搜索框兜底路径
    (`opened by Ctrl+F search`) 点完结果行就直接宣称「已打开」，没有回头确认聊天头部，
    于是文本被粘进了当时开着的**另一个**会话；WeFlow 回读当然找不到 → 判失败 →
    重试 3 次 + 重发队列，全都粘进同一个错会话。这里补最后一道闸：只有头部 OCR 或
    侧栏绿底选中行**确认是目标**，才允许继续打字。
    """
    if plan.get("target_verified") is not True:
        rect_now = tuple(plan.get("window_rect") or ())
        if not rect_now:
            try:
                rect_now = tuple(restored_rect(find_wechat(cfg)))
            except Exception:
                rect_now = ()
        verified_by = ""
        header_detail = {}
        if rect_now:
            try:
                ok_header, header_detail = chat_header_matches(None, cfg, rect_now, target)
            except Exception as exc:          # noqa: BLE001
                ok_header, header_detail = False, {"error": str(exc)}
            if ok_header:
                verified_by = "header"
            else:
                row_now = None
                try:
                    row_now = selected_row_text(cfg, rect_now)
                except Exception:             # noqa: BLE001
                    row_now = None
                if row_now and name_match(target, _row_text_of(row_now)) >= NAME_MATCH_LENIENT:
                    verified_by = "selected_row"
                plan["identity_check"] = {
                    "header": (header_detail.get("texts") or [])[:4],
                    "selected_row": row_now,
                    "verified_by": verified_by,
                }
        if not verified_by and find_window(cfg, target, fuzzy=True) is None:
            return fail(plan, "chat identity not confirmed before typing "
                              "(header=%s selected_row=%s)" % (
                                  (header_detail.get("texts") or [])[:3],
                                  (plan.get("identity_check") or {}).get("selected_row")))
        plan["target_verified"] = verified_by or "detached_window"
    chat = find_window(cfg, target, fuzzy=True) or find_wechat(cfg)
    if chat and foreground() != chat and not focus(chat):
        return fail(plan, "chat window did not take focus; refusing to type blind")
    fg_title, fg_class = device_class(foreground())
    plan["keyboard_target"] = {"title": fg_title, "class": fg_class}
    if not fg_class.startswith(cfg["window_class_prefix"]):
        return fail(plan, "foreground window is not WeChat; refusing to type blind")
    _warm_mark(target, plan.get("window_rect"),
               header_verified=bool(plan.get("warm_header_verified")))
    return True


_SEARCH_BLOCKED_SECTIONS = ("公众号", "文章", "视频号", "小程序", "朋友圈", "搜一搜", "网络结果")
# 搜索面板里会出现的分区标题（用来判断「当前这一行的归属」）。顺序不敏感，按 OCR 文本包含判定。
_SEARCH_SECTION_WORDS = ("聊天记录", "搜索网络结果", "公众号", "文章", "视频号", "小程序",
                         "朋友圈", "搜一搜", "联系人", "群聊", "功能")


def _strip_icon_noise(text):
    """去掉搜索行前面被 OCR 当成文字的图标噪声（「0 文件传输助手」「．测试群」「八…」）。

    只剥掉开头 1~3 个「不像名字开头」的字符：数字/标点/单个字母，且剥完还剩至少 2 个字。
    目标本身以数字开头的会话（如「12306」）不受影响 —— 剥完对不上目标名时调用方也不会采纳。
    """
    t = (text or "").strip()
    for _ in range(3):
        if len(t) < 3:
            break
        head = t[0]
        if head.isdigit() or not (head.isalpha() and "\u4e00" <= head <= "\u9fff"):
            stripped = t[1:].lstrip()
            if len(stripped) >= 2:
                t = stripped
                continue
        break
    return t


def _collapse_repeats(text):
    """把 OCR 把同一行文字重复读出的结果压回一份（群聊结果行特别容易这样）。

    实测：「雾中人耍起」的搜索结果行被读成
        `雾中人耍起雾中人耍起雾中人耍起雾中，`
    —— 行里既有群名又有成员名/标签，Windows OCR 把几段拼成一串重复文字，长度远超名字，
    精确比对和 2..24 长度过滤都过不去，长名字群因此永远找不到。这里检测「整串是某个前缀
    的重复（末尾允许截断）」并只留一份前缀。
    """
    t = (text or "").replace(" ", "")
    n = len(t)
    for p in range(2, n // 2 + 1):
        if (t[:p] * ((n + p - 1) // p))[:n] == t:
            return t[:p]
    return t


def _search_result_pick(client, cfg, rect, target):
    """OCR 微信搜索面板，返回「确实叫这个名字」的结果行坐标和诊断。

    搜索面板里除了会话还列出公众号、文章、视频号……用户实测过：直接按 Enter 会选中默认
    高亮的第一条，那条可能是个公众号（消息就此发进公众号会话）。所以这里先 OCR 结果列表，
    跳过「公众号/文章/…」分区下的行，只在名字能对上目标时才返回坐标；调用方点完仍要由
    聊天头部 OCR 复核。
    """
    left, top, right, bottom = rect
    region = [left + int(cfg.get("sidebar_capture_x_offset", 90)),
              top + int(cfg.get("search_results_y_offset_px", 100)),
              left + int(cfg.get("sidebar_capture_x2_offset", 560)),
              bottom - int(cfg.get("sidebar_capture_bottom_margin", 30))]
    png, err = shot_ex(client, region, os.path.join(ROOT, "logs", "wmcp"))
    dbg = {"png": png, "region": [int(v) for v in region]}
    if not png:
        dbg["error"] = "no image from Screenshot (%s)" % (_brief(err) or "region off screen?")
        return None, dbg
    ocr_png = png
    scale_up = float(cfg.get("sidebar_ocr_scale", 2.0) or 1.0)
    if scale_up > 1.0:
        ocr_png = _upscale_for_ocr(png, scale_up)
    size = image_size(ocr_png)
    res = ocr_lines(ocr_png)
    if not res.get("ok"):
        dbg["error"] = "ocr failed"
        return None, dbg
    img_w, img_h = size if size else (region[2] - region[0], region[3] - region[1])
    scale_y = (region[3] - region[1]) / float(img_h)
    scale_x = (region[2] - region[0]) / float(img_w)
    keys = [k for k in target_keys(cfg, target) if k]
    lines = []
    for line in res["lines"]:
        text = fit(line["text"])
        if not text:
            continue
        box_h = max((w["y"] + w["h"] for w in line["words"]), default=line["y"] + 18) - line["y"]
        lines.append({"text": text, "raw": line["text"],
                      "x": int(region[0] + line["x"] * scale_x),
                      "y": int(region[1] + (line["y"] + max(box_h, 18) / 2.0) * scale_y)})
    lines.sort(key=lambda c: c["y"])
    dbg["lines"] = [c["raw"] for c in lines][:30]
    target_fold = ocr_fold(target)
    # 2026-10-21 实测的面板结构（从上到下）：
    #   ①第一条就是命中的会话（头像被读成「0」「．」「八」这类噪声前缀）
    #   ②「聊天记录」分区（同名会话的消息预览）
    #   ③「搜索网络结果」分区（一堆同名的网络条目，点进去是网页/公众号，绝不能点）
    #   ④搜索词下面的普通侧栏列表
    # 以前的做法是「见到公众号/网络结果这类词之后一行都不许点」，结果把 ① 之后的真会话也
    # 一起毙掉了。现在改成按 y 顺序跟踪「当前处于哪个分区」：只有顶部（还没遇到分区标题）和
    # 「聊天记录/联系人/群聊」分区里的行才允许点，网络结果/公众号/文章里的行一律跳过。
    section = ""
    scored = []
    for c in lines:
        text = c["text"]
        head_hit = next((w for w in _SEARCH_SECTION_WORDS if w in text and len(text) <= len(w) + 2), None)
        if head_hit:
            section = head_hit
            continue
        blocked_section = any(w in section for w in _SEARCH_BLOCKED_SECTIONS)
        if blocked_section:
            continue
        name = _collapse_repeats(_strip_icon_noise(text))
        score = name_match_keys(keys, name)
        if score >= NAME_MATCH_CLEAR or (target_fold and ocr_fold(name) == target_fold):
            scored.append(dict(c, text=name, score=max(score, 1.0), exact=True, section=section))
            continue
        if score > 0 and not blocked_section and not any(ch in name for ch in "：:") \
                and 2 <= len(name) <= 24:
            scored.append(dict(c, text=name, score=score, exact=False, section=section))
    if not scored:
        dbg["error"] = "no search result row names the target"
        return None, dbg
    exact = [c for c in scored if c["exact"]]
    pick = exact[0] if exact else None
    if pick is None:
        best = max(scored, key=lambda c: c["score"])
        runner_up = max([c["score"] for c in scored if c is not best], default=0.0)
        if best["score"] - runner_up >= NAME_MATCH_UNIQUE_MARGIN:
            pick = best
    if pick is None:
        dbg["error"] = "search results are ambiguous"
        dbg["scored"] = [{"text": c["raw"], "score": c["score"], "section": c.get("section")}
                         for c in scored][:10]
        return None, dbg
    dbg["picked"] = {"text": pick["raw"], "score": pick["score"], "exact": pick["exact"],
                     "section": pick.get("section")}
    return (int(pick["x"] + int(cfg.get("row_click_x_bias_px", 12))),
            int(pick["y"])), dbg


def _search_box_point(cfg, rect):
    """点在微信「搜索」输入框里的坐标（窗口相对偏移，默认对准那块输入框的中间）。

    2026-10-21 实测：`ctrl+f` 在微信 4.x 里打不开可用搜索面板（粘贴落不进去），而**直接点
    搜索框 → 粘贴 → OCR 结果列表**是能用的（结果第一行就是目标会话）。所以搜索兜底改成点。
    """
    return [int(rect[0] + cfg.get("search_box_x_px", 300)),
            int(rect[1] + cfg.get("search_box_y_px", 105))]


def header_looks_like(cfg, target, texts):
    """聊天头部这一行是不是目标会话（含 OCR 把字读歪的宽匹配）。

    严格判定优先（名字/别名/折串）。再退一档：**同长度 + 首字相同**的宽匹配——微信头部把
    「测试群」读成「测讠胡羊（3）」就是这种「部首/形近替身」，严格判定永远不中。宽度只给
    同长度同首字的串，误判面很小；而且这条只用来决定「不要再点一次侧栏」，真正发出去之后
    仍有 WeFlow 回读校验兜底。
    """
    if header_names_target(target_keys(cfg, target), texts):
        return True, "strict"
    want = fit(target)
    if not want:
        return False, ""
    for text in texts or []:
        got = fit(_header_group_clean(text))
        if not got or len(got) != len(want) or got[0] != want[0]:
            continue
        if name_match(target, got) >= NAME_MATCH_LENIENT:
            return True, "lenient"
    return False, ""


def open_chat_by_search(client, cfg, rect, target):
    """点搜索框 → 粘贴会话名 → 点「确实叫这个名字」的结果行。

    `ctrl+f` 在微信 4.x 里不可用（打不开结果面板），改成点搜索框（`_search_box_point`）。

    侧边栏 OCR 对短名、生僻字、被 emoji 挤压的名字并不可靠（实测「测试群」在侧边栏和聊天
    标题里都被读成「测讠羊」），而微信自带的搜索是精确匹配，因此它比逐行 OCR 可靠得多；
    顺带还省掉了「滚 12 次到顶 + 翻 6 页」那 40 秒。但**绝不按 Enter**：Enter 会选中默认
    高亮的第一条，那条可能是公众号；这里先 OCR 结果列表、跳过公众号/文章等分区、点名对上
    才点，点完还要由聊天头部 OCR 复核，绝不盲发。
    """
    dbg: dict = {"target": target}
    started = time.time()
    try:
        hwnd = find_wechat(cfg)
        if not hwnd or not focus(hwnd):
            dbg["error"] = "WeChat main window not focusable"
            return False, dbg
        if cfg.get("search_use_ctrl_f", False):
            client.call("Shortcut", {"shortcut": "ctrl+f"})
            time.sleep(float(cfg.get("search_focus_wait_s", 0.35)))
            client.call("Clipboard", {"mode": "set", "text": target})
            client.call("Shortcut", {"shortcut": "ctrl+v"})
            time.sleep(float(cfg.get("search_paste_wait_s", 0.7)))
        else:
            dbg["driver"] = ui_paste(client, cfg, _search_box_point(cfg, rect), target)
    except HumanBusy as e:  # 人为在用鼠标键盘：不抢
        dbg["error"] = "%s: %s" % (CONTROL_PREEMPTED, e)
        return False, dbg
    except Exception as e:
        dbg["error"] = "search jump failed: %s" % e
        return False, dbg
    point, pdbg = _search_result_pick(client, cfg, rect, target)
    dbg["results"] = pdbg
    dbg["search_ms"] = int((time.time() - started) * 1000)
    if not point:
        try:
            ui_escape(client, cfg)
        except Exception:
            pass
        dbg["error"] = "search fallback: %s" % pdbg.get("error", "no safe result row")
        return False, dbg
    try:
        ui_click(client, cfg, point)
        time.sleep(float(cfg.get("search_open_wait_s", 1.4)))
    except HumanBusy as e:  # 人为在用鼠标键盘：不抢
        dbg["error"] = "%s: %s" % (CONTROL_PREEMPTED, e)
        return False, dbg
    except Exception as e:
        dbg["error"] = "click on search result failed: %s" % e
        return False, dbg
    matched, header = chat_header_matches(client, cfg, rect, target)
    if not matched:
        matched = header_matches_surface(header.get("texts", []),
                                         (pdbg.get("picked") or {}).get("text"))
    # 点完结果行，微信要先收起搜索面板再切换会话：0.3s 时抓到的那一帧很可能还是**上一个**
    # 会话的头部（实测点「严正阳」时读到「杨志贺」，于是明明切过去了却判失败）。所以这一
    # 步按「等它变成目标」轮询，只在最后仍不匹配时才认输。
    polls = max(1, int(cfg.get("header_verify_polls", 4)))
    poll_wait = float(cfg.get("header_verify_wait_s", 0.3))
    tried = 1
    while not matched and tried < polls:
        time.sleep(poll_wait)
        tried += 1
        matched, header = chat_header_matches(client, cfg, rect, target)
        if not matched:
            matched = header_matches_surface(header.get("texts", []),
                                             (pdbg.get("picked") or {}).get("text"))
    header["polls"] = tried
    dbg["header"] = header
    if not matched:
        dbg["error"] = "search result header does not show the target"
        try:
            ui_escape(client, cfg)
        except Exception:
            pass
        return False, dbg
    # 2026-10-21 实测：点中搜索结果之后微信自己就收起搜索面板、头部已经显示目标会话，再补一个
    # Escape 反而会多按一下（曾经把面板的动画文字读成 "movlng tne mo.."，于是刚复核通过的会话
    # 又被判成失败——白跑一趟、还要重试）。所以这里只留头部复核通过的结论，只有配置显式要求
    # 时才去做「清搜索词 + 二次复核」。
    if cfg.get("search_escape_after_open", False):
        try:
            client.call("Shortcut", {"shortcut": "escape"})
            time.sleep(float(cfg.get("search_escape_wait_s", 0.4)))
        except Exception:
            pass
        still, header2 = chat_header_matches(client, cfg, rect, target)
        dbg["header_after_escape"] = header2
        return bool(still or header_matches_surface(header2.get("texts", []), target)), dbg
    return True, dbg


def open_chat(client, cfg, target, plan, do_ocr=True, assume_current=False):
    """Focus WeChat, click the target's sidebar row, and make sure its chat owns the keyboard.

    Every failure path writes the reason into `plan`, so callers never type blind.

    `assume_current=True` is the cheap path for a burst of messages to one contact: the
    previous send already left that contact's chat open, so only the small header region
    is OCR'd to confirm it still shows the target and the full sidebar scan is skipped.
    A header mismatch silently falls back to the normal scan, so nothing can be mis-sent.
    """
    hwnd = find_wechat(cfg)
    if not hwnd:
        return fail(plan, "WeChat window not found (closed to tray and no window to restore)")
    plan["window_rect"] = list(window_rect(hwnd))
    # Windows refuses SetForegroundWindow from a background process; without this the
    # click lands on whatever is on top and the text is typed into the wrong app.
    # 用户不会把微信置顶 → 这里主动从最小化/托盘把它唤起（含 AttachThreadInput 与 ALT 轻推），
    # 唤不起来就放弃，绝不把点击打到别的窗口上。
    if not focus(hwnd):
        raised = bool(winio is not None and winio.raise_window(hwnd))
        plan["raised_by"] = "winio.raise_window" if raised else "failed"
        if not raised or not focus(hwnd):
            return fail(plan, "could not bring WeChat to the foreground "
                              "(window is minimized or behind another window)")
    rect = restored_rect(hwnd)
    plan["window_rect"] = list(rect)
    if not rect_usable(rect):
        return fail(plan, "WeChat window is minimized/hidden (rect=%s); put it back "
                          "on screen and retry" % (list(rect),))
    if do_ocr and not find_window(cfg, target, fuzzy=True):
        reuse, header_recent = _warm_get(cfg, target, rect)
        if reuse and header_recent:
            # 常驻 worker 刚给这个人发过消息，但**绝不能因此就跳过确认**：2026-10-22 实测
            # 一次「暖会话直接粘贴」把给天文学家的翻唱 mp3 发进了杏园6-508（中间有别的
            # 发送把会话切走了）。这里只花 ~120ms 看一眼聊天头部，命中了才敢粘贴。
            _warm_ok, _warm_hd = chat_header_matches(client, cfg, rect, target)
            _warm_row = None
            if not _warm_ok:
                try:
                    _warm_row = selected_row_text(cfg, rect)
                except Exception:
                    _warm_row = None
                if _warm_row and name_match(target, _row_text_of(_warm_row)) >= NAME_MATCH_LENIENT:
                    _warm_ok = True
            if _warm_ok:
                plan["claimed"] = "chat already on target (warm session: header verified)"
                plan["target_verified"] = True
                plan["warm_header_verified"] = True
                plan["assume_current"] = {"matched": True, "warm": True,
                                          "header_texts": _warm_hd.get("texts", [])[:6]}
                plan["row_point"] = []
                plan["open_attempts"] = 0
                plan["chat_header"] = _warm_hd
                return _chat_ready_to_type(cfg, plan, target)
            plan["warm_rejected"] = {"header_texts": _warm_hd.get("texts", [])[:4],
                                     "selected_row": _warm_row}
            # 暖状态已失效：清掉，让后面的常规扫描重新定位并点击。
            _WARM["target"] = ""
            _WARM["clean"] = ""
    if do_ocr and assume_current and not find_window(cfg, target, fuzzy=True):
        # 同一联系人的连发：上一条消息已经把他的会话开在当前窗口上。这里只截「聊天头部」
        # 那一小块做 OCR 确认（比扫整个侧边栏便宜得多），命中就直接进入输入校验，省掉一次
        # 全量扫描；没命中就落回下面的常规扫描，所以省这一次 OCR 不会带来发错人的风险。
        matched, header0 = chat_header_matches(client, cfg, rect, target)
        plan["assume_current"] = {"matched": bool(matched),
                                  "header_texts": header0.get("texts", [])[:6],
                                  "png": header0.get("png")}
        if matched:
            plan["claimed"] = "chat already on target (assume-current: sidebar OCR skipped)"
            plan["target_verified"] = True
            plan["warm_header_verified"] = True
            plan["row_point"] = []
            plan["open_attempts"] = 0
            plan["chat_header"] = header0
            return _chat_ready_to_type(cfg, plan, target)
    if do_ocr:
        # 2026-10-21（用户目标：OCR 两秒内定位任意聊天，优先判断当前会话是不是目标）：
        # 顺序彻底改掉 —— 以前是「先滚到侧栏顶部再翻 6 页 OCR」（实测 Scroll 一次 3.3s、
        # 12 次归位就是 40s，而且侧栏根本不跟着滚），现在改成：
        #   ① 先看**当前已经打开的会话**是不是目标（一次小区域截图 + OCR，约 0.3s）；
        #   ② 不是就点搜索框、粘贴名字、OCR 结果列表、点名对上才点（约 1.5s，精确）；
        #   ③ 侧栏逐行 OCR 只当最后兜底（`locate_sidebar_fallback`，默认关闭）。
        already, header0 = chat_header_matches(client, cfg, rect, target)
        looks, how = header_looks_like(cfg, target, header0.get("texts", []))
        plan["current_chat_check"] = {"matched": bool(already or looks),
                                      "by": how or ("strict" if already else ""),
                                      "header_texts": header0.get("texts", [])[:6],
                                      "png": header0.get("png")}
        if already or looks:
            # 2026-10-22 事故：目标 文件传输助手，界面实际停在 雾中人耍起，头部 OCR 走了
            # 宽匹配就宣称「已经在目标会话」，结果 mic/发送点到错的会话上。这里用侧栏绿底
            # 高亮复核：选中行读得出来但不是目标 → 绝不认「已打开」，老老实实去定位。
            sel = None
            try:
                sel = selected_row_text(cfg, rect)
            except HumanBusy:
                raise
            except Exception:  # noqa: BLE001
                sel = None
            if sel and fit(sel.get("text") or ""):
                if name_match(target, sel["text"]) < NAME_MATCH_LENIENT:
                    plan["header_rejected_by_row"] = {"header": (header0.get("texts") or [])[:4],
                                                      "row": sel["text"], "by": how or "strict"}
                    already = looks = False
            if already or looks:
                plan["claimed"] = "chat already on target (header check: no click)"
                plan["selected_row"] = sel["text"] if sel else None
                plan["warm_header_verified"] = True
                plan["row_point"] = []
                plan["open_attempts"] = 0
                plan["chat_header"] = header0
                return _chat_ready_to_type(cfg, plan, target)
        if cfg.get("locate_search_first", False):
            searched, sdbg = open_chat_by_search(client, cfg, rect, target)
            plan["search_fallback"] = sdbg
            if searched:
                plan["claimed"] = "opened by search box (click + paste + pick)"
                plan["row_point"] = []
                plan["open_attempts"] = 0
                return _chat_ready_to_type(cfg, plan, target)
        if not cfg.get("locate_sidebar_fallback", False):
            # 目标可能被折进「折叠置顶聊天」里（侧栏 OCR 完全看不到）：
            # 展开一次再决定要不要放弃。
            if _expand_pinned_section(client, cfg, rect):
                plan["pinned_expanded"] = True
                # 展开后列表多出一堆置顶行，原先“已在顶部”的缓存不再成立，
                # 且目标会被挤到更下面：复位滚动状态，并临时放宽翻页上限。
                _SCROLL_POS["at_top"] = False
                _SCROLL_POS["ts"] = 0.0
                cfg = dict(cfg)
                cfg["sidebar_scroll_pages"] = int(cfg.get("sidebar_scroll_pages", 6)) + 4
            else:
                    _hint = session_exists_in_weflow(cfg, target)
                    plan["weflow_session"] = _hint
                    _note = ""
                    if _hint:
                        _note = "；WeFlow 里有这个会话（%s），但它不在当前聊天列表里（被删/被归档/需要滚动）" % (
                            _hint.get("displayName") or _hint.get("username"))
                    elif _hint == {}:
                        _note = "；WeFlow 会话列表里也没有它"
                    return fail(plan, "could not locate target chat (target=%s; header=%s; search: %s)%s"
                            % (target,
                               " / ".join(header0.get("texts", [])[:4]) or "nothing",
                               (plan.get("search_fallback") or {}).get("error") or "not tried",
                               _note))
        point, dbg = find_row(client, cfg, rect, target)
        plan["ocr"] = {"png": dbg.get("png"), "engine": dbg.get("engine"),
                       "error": dbg.get("error"), "region": dbg.get("region"),
                       "ms": dbg.get("ms") or {},
                       "scanned_where": dbg.get("scanned_where"),
                       "scrolled_pages": dbg.get("scrolled_pages"),
                       "picked": dbg.get("picked") if point else None}
        if not point:
            plan["ocr_candidates"] = dbg.get("candidates", [])[:20]
            if dbg.get("control_preempted"):
                plan["control_preempted"] = True
                return fail(plan, "desktop control preempted: windows-mcp refused Screenshot "
                                  "with CONTROL_PREEMPTED (a fresh windows-mcp client is required)")
            seen = [c.get("text") for c in (dbg.get("candidates") or []) if c.get("text")][:8]
            # OCR 读不出这一行（短名/生僻字/被 emoji 挤压）时，改用微信搜索精确跳转。
            # 目标身份仍由聊天头部 OCR 复核，复核不过就当没开，绝不盲发。
            if cfg.get("sidebar_search_fallback", False):
                searched, sdbg = open_chat_by_search(client, cfg, rect, target)
                plan["search_fallback"] = sdbg
                if searched:
                    plan["claimed"] = "opened by Ctrl+F search"
                    plan["row_point"] = []
                    plan["open_attempts"] = 0
                    plan["ocr_failed_sidebar"] = " / ".join(seen) or "nothing"
                    return _chat_ready_to_type(cfg, plan, target)
            return fail(plan, "could not locate target row by OCR (target=%s, aliases=%s; "
                              "sidebar shows: %s; search fallback: %s)"
                        % (target, alias_names(cfg, target) or "none",
                           " / ".join(seen) or "nothing",
                           (plan.get("search_fallback") or {}).get("error") or "not tried"))
        x, y = point
    else:
        x, y = row_point(cfg, rect, int(cfg.get("assume_row_index", 0)))
    # 2026-10-21（用户实测：打开聊天窗口后又被点了一次）：点之前先确认「当前显示的
    # 就是这个会话」。名字 OCR 读不准的群（如「测试群」被读成「测讠羊」）名字改写命不中，
    # 于是每次都去点侧栏同一行——这一下就是用户看到的「双击」。这里除了名字，再拿刚扫到的
    # 那一行 OCR 文本跟聊天头部做「同源」比较，命中就完全跳过点击。
    picked_text = ((plan.get("ocr") or {}).get("picked") or {}).get("text")
    if do_ocr and not find_window(cfg, target, fuzzy=True):
        already, header0 = chat_header_matches(client, cfg, rect, target)
        surface_ok = bool(picked_text) and header_matches_surface(header0.get("texts", []), picked_text)
        if already or surface_ok:
            plan["claimed"] = "chat already open" if already else "chat already open (surface match)"
            plan["target_verified"] = True
            plan["warm_header_verified"] = True
            plan["row_point"] = [x, y]
            plan["open_attempts"] = 0
            plan["chat_header"] = header0
            return _chat_ready_to_type(cfg, plan, target)

    # 同一个目标刚刚点过（多为 send 的重试），就别再点：只要头部画面跟点完那一刻一致，
    # 就认为会话还开着，直接进入打字环节。
    _dedup_s = float(cfg.get("row_click_dedup_s", 20.0))
    _memo = _CLICK_MEMO.get(target)
    if _memo and (time.time() - _memo["ts"]) < _dedup_s:
        _probe_until = time.time() + float(cfg.get("row_click_dedup_probe_s", 2.0))
        while True:
            _matched, _hd = chat_header_matches(client, cfg, rect, target)
            _same = bool(_memo.get("header_texts")) and header_matches_surface(
                _hd.get("texts", []), " ".join(_memo["header_texts"]))
            _texts_now = [t for t in (_hd.get("texts") or []) if fit(t)]
            if not _matched and not _same and _texts_now:
                # 头部读得出字、但压根不是这个会话 —— 说明点完这一行之后会话被切走了
                # （实测：先点「测试群」再点别的聊天，回到「测试群」时会误判成「已打开」，
                # 消息就发进了上一个会话）。这种情况必须重新点。
                plan["click_dedup"] = {"age_s": round(time.time() - _memo["ts"], 1),
                                       "stale_header": _texts_now[:4]}
                break
            if _matched or _same:
                plan["claimed"] = "chat already open (click dedup)"
                plan["target_verified"] = True
                plan["warm_header_verified"] = True
                plan["row_point"] = list(_memo["point"])
                plan["click_dedup"] = {"age_s": round(time.time() - _memo["ts"], 1)}
                plan.update(open_attempts=0, chat_header=_hd)
                return _chat_ready_to_type(cfg, plan, target)
            if time.time() >= _probe_until:
                # 认不出来也绝不补第二下（用户看到的「点两次」就是这么来的）。但「认不出来」
                # 不等于「就是这个会话」：2026-10-22 一次文件发送就是这样落进了别的群。
                # 侧栏选中行的绿底高亮能读出名字时以它为准，读不出就直接走常规定位。
                _row_now = None
                try:
                    _row_now = selected_row_text(cfg, rect)
                except Exception:
                    _row_now = None
                if _row_now and name_match(target, _row_text_of(_row_now)) >= NAME_MATCH_LENIENT:
                    plan["claimed"] = "chat already open (click dedup: selected row match)"
                    plan["target_verified"] = True
                    plan["warm_header_verified"] = False
                    plan["row_point"] = list(_memo["point"])
                    plan["click_dedup"] = {"age_s": round(time.time() - _memo["ts"], 1),
                                           "clicked": False, "selected_row": _row_now}
                    plan.update(open_attempts=0, chat_header=_hd)
                    return _chat_ready_to_type(cfg, plan, target)
                plan["click_dedup"] = {"age_s": round(time.time() - _memo["ts"], 1),
                                       "clicked": False, "selected_row": _row_now,
                                       "give_up_shortcut": True}
                break
            time.sleep(float(cfg.get("after_click_poll_s", 0.12)))

    if do_ocr:
        _sidebar_stable(client, cfg, clamp_region([rect[0] + cfg["sidebar_capture_x_offset"],
                                                   rect[1] + cfg["sidebar_capture_y_offset"],
                                                   rect[0] + cfg["sidebar_capture_x2_offset"],
                                                   rect[3] - cfg["sidebar_capture_bottom_margin"]],
                                                  rect),
                        os.path.join(ROOT, "logs", "wmcp"), timeout=0.4)
    plan.update(row_point=[x, y])
    # A window restored from minimized swallows the first click (it only activates), so
    # keep clicking until the chat header really shows the target — that is the signal the
    # conversation is open, not that a click was sent. Repeat clicks are deliberately rare
    # (max_row_clicks, at least repeat_click_min_interval_s apart): two clicks in quick
    # succession are a double click, which opens the chat in a separate window.
    # 2026-10-21（用户实测）：点开群聊之后聊天头部的 OCR 要等一会儿才出字，过去「1.4s
    # 没认出名字就再点一次同一行」会被微信当成第二次点击——刚打开的会话又被收起/弹出，
    # 于是永远复核不过。现在只点一次，然后**轮询**头部（默认 4 次 × 0.7s），认不出就
    # 失败返回，绝不重复点击；确实需要重复点击时把 max_row_clicks 显式调大。
    max_clicks = max(1, int(cfg.get("max_row_clicks", 1)))
    min_gap = float(cfg.get("repeat_click_min_interval_s", 2.0))
    click_wait = float(cfg.get("after_click_wait", 1.6))
    # 点完不要盲等：微信切换会话通常 150~400ms 就出标题，等到 `click_wait` 才复核等于每条
    # 消息白付 1.2s。改成「短间隔轮询、命中就返回、最多等到 after_click_wait」。
    poll_step = float(cfg.get("after_click_poll_s", 0.12))
    attempts, header, last_click = 0, {}, 0.0
    confirmed = False
    while True:
        attempts += 1
        if last_click:
            gap = min_gap - (time.time() - last_click)
            if gap > 0:
                time.sleep(gap)
        # 2026-10-23：同一行在几秒内被点两次，微信会当成双击 → 会话弹成独立窗口。
        # 这里给「同一个目标、同一个落点」加 2.5s 冷却：重试路径不会再把同一行点第二下，
        # 需要真正重开时上层会重新扫描拿到新的落点。
        _prev = _CLICK_MEMO.get(target)
        if _prev and (time.time() - _prev["ts"]) < float(cfg.get("row_click_cooldown_s", 2.5)):
            _pp = _prev.get("point") or (0, 0)
            if abs(x - _pp[0]) <= 8 and abs(y - _pp[1]) <= 8:
                plan["click_cooldown_skip"] = {"age_s": round(time.time() - _prev["ts"], 2),
                                               "point": list(_pp)}
                break
        try:
            plan["click_driver"] = ui_click(client, cfg, (x, y))
        except HumanBusy:
            raise
        last_click = time.time()
        deadline = last_click + click_wait
        time.sleep(poll_step)
        while True:
            if find_window(cfg, target, fuzzy=True) or not do_ocr:
                confirmed = True
                break
            _sel = _row_selected_live(cfg, rect, (x, y))
            if _sel:
                # 绿底选中 = 微信自己确认了会话已切换，比头部 OCR 快也更稳（群名常被读错）。
                plan["row_selected_verified"] = True
                confirmed = True
                break
            matched, header = chat_header_matches(client, cfg, rect, target)
            if matched or header_matches_surface(header.get("texts", []), picked_text):
                confirmed = True
                break
            if time.time() >= deadline:
                break
            time.sleep(poll_step)
        if confirmed or attempts >= max_clicks:
            break
    _note_click(target, (x, y), (header or {}).get("texts"), why="open_chat")
    # 点出来的会话不对（典型：刚点开 A 之后侧栏正把它往顶部做滚动动画，我们在动画中间截的
    # 那一帧里 B 的位置还没定，于是点下去落在 A 下面一行）。这时列表已经静止，重新扫描一次、
    # 按新的行位置补点一下——是**另一行**，不会被系统合成双击，比把消息塞进重发队列划算。
    if (not confirmed and do_ocr and cfg.get("locate_reclick_on_mismatch", True)):
        retry = {}
        rpoint, rdbg = find_row(client, cfg, rect, target)
        if rpoint and abs(rpoint[1] - y) > 8:
            plan["reclick"] = {"from": [x, y], "to": list(rpoint)}
            plan["reclick_ocr"] = {"picked": rdbg.get("picked"), "ms": rdbg.get("ms")}
            x, y = rpoint
            attempts += 1
            try:
                plan["click_driver"] = ui_click(client, cfg, (x, y))
            except HumanBusy:
                raise
            last_click = time.time()
            deadline = last_click + click_wait
            time.sleep(poll_step)
            while True:
                if find_window(cfg, target, fuzzy=True):
                    confirmed = True
                    break
                matched, header = chat_header_matches(client, cfg, rect, target)
                if matched or header_matches_surface(header.get("texts", []), picked_text):
                    confirmed = True
                    break
                if time.time() >= deadline:
                    break
                time.sleep(poll_step)
            _note_click(target, (x, y), (header or {}).get("texts"), why="open_chat-reclick")
    plan["open_attempts"] = attempts
    plan["chat_header"] = header
    header_ok = bool(header_names_target(target_keys(cfg, target), header.get("texts", []))) or         bool(header_matches_surface(header.get("texts", []), picked_text))
    if not header_ok and plan.get("row_selected_verified"):
        header_ok = True
    plan["header_surface_check"] = {"picked": picked_text, "ok": header_ok}
    if do_ocr and not find_window(cfg, target, fuzzy=True) and not header_ok:
        return fail(plan, "open chat header does not show the target")
    # 这一条路径确认过「头部名字/独立会话窗口标题」的，才算把 warm 状态置为「名字已确认」。
    plan["warm_header_verified"] = bool(find_window(cfg, target, fuzzy=True)) or header_ok
    # WeChat 4.x puts the chat in its own Qt window titled with the contact name.
    # Type only once that window (or the main window) really owns the keyboard.
    return _chat_ready_to_type(cfg, plan, target)


def _assemble(plan, attempt_log, found, kind, cfg, talker, text=None):
    """Turn the attempt log into the final plan (shared by send and send_image)."""
    plan["attempts"] = len(attempt_log)
    plan["attempt_log"] = attempt_log
    plan["verified"] = bool(found)
    plan["ok"] = bool(found)
    if found:
        plan.pop("error", None)
        plan.pop("retryable", None)
        plan["localId"] = found.get("localId")
        plan["createTime"] = found.get("createTime")
        if kind == "image":
            plan["localType"] = found.get("localType")
        return plan
    # 最后一搏：WeFlow 的回读经常比输入慢 10–30s。以前这里直接判失败，bridge 就
    # 重发 → 同一条消息刷屏 12 次（2026-10-22）。再给 25 秒的宽容窗口，按内容找一次。
    if text:
        try:
            late = verify(cfg, talker, text, time.time() + 25, 0, time.time() - 150)
        except Exception:                      # noqa: BLE001
            late = None
        if late:
            plan.update(verified=True, ok=True, localId=late.get("localId"),
                        createTime=late.get("createTime"), late_verified=True)
            plan.pop("error", None)
            plan.pop("retryable", None)
            if kind == "image":
                plan["localType"] = late.get("localType")
            log.info("[wxui] ✅ 补验证成功（晚到回读）%s localId=%s", talker, late.get("localId"))
            return plan
    if not plan.get("error"):
        plan["error"] = ("%s not read back from WeChat DB within timeout"
                         % ("message" if kind == "text" else kind))
    plan["retryable"] = True
    tail = api(cfg, "/api/v1/messages", talker=talker, limit=5).get("messages", [])
    plan["recent"] = [{"isSend": m.get("isSend"), "content": (m.get("content") or "")[:60]}
                      for m in tail]
    return plan


def _identity_recheck(cfg, target):
    """打字中途再确认一次「现在开着的还是不是目标会话」。

    2026-10-23 事故：给「桃桃真可爱」的消息被连发三份到「陈奥琦」——校验那一刻还是
    桃桃，真正 Ctrl+V 的时候会话已经被用户切到陈奥琦。这里只做一件事：**只有明确
    认出「另一个像人名的会话名」才否决**；OCR 读不出、读到界面噪声（`楼 上 6 巧 08`
    这种）一律放行，否则会把正常发送全部误拦（实测第一版就误拦了文件传输助手）。
    """
    def _plausible(text):
        t = "".join(str(text or "").split())
        if not (2 <= len(t) <= 12):
            return False
        if re.search(r"[0-9（）()\[\]/\|:：、,，]", t):
            return False
        return bool(re.fullmatch(r"[一-鿿A-Za-z_]+", t))

    verdict_other = 0
    reason = ""
    for _ in range(2):
        try:
            hwnd = find_wechat(cfg)
            if not hwnd:
                return True, "wechat window gone (allow)"
            rect = restored_rect(hwnd)
            ok, hd = chat_header_matches(None, cfg, rect, target)
            if ok:
                return True, "header"
            texts = [t for t in (hd.get("texts") or []) if fit(t)]
            other = None
            for t in texts:
                if _plausible(t) and name_match(target, t) < 0.3:
                    other = t
                    break
            if other:
                verdict_other += 1
                reason = "header=%s" % other
            else:
                verdict_other = 0
                reason = "header unreadable"
        except Exception as e:
            return True, "recheck error (allow): %s" % e
        time.sleep(0.12)
    if verdict_other >= 2:
        return False, reason
    return True, reason or "header unreadable (allow)"


def send(cfg, target, text, commit=False, do_ocr=True, assume_current=False, client=None):

    """Send text, retrying with a fresh read-back check until it is verified delivered.

    Every attempt is: wait for a usable session → open the chat → clear the input box →
    paste → Enter → read the message back from WeChat's DB. Before re-sending, the DB is
    checked once more, so an attempt that did arrive late is never sent a second time.

    `client` 是常驻 worker 复用进来的 windows-mcp 客户端；为 None 时本函数自己起一个再关掉
    （一次性模式，行为与过去一致）。
    """
    rows = api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
    talker = talker_for(cfg, target, rows)
    plan = {"target": target, "talker": talker, "kind": "text"}
    # 防御：bridge 偶尔会把 None 当文本传进来（实测报错
    # `desktop input refused: object of type 'NoneType' has no len()`），
    # 直接拒绝比让它一路走到 set_clipboard 再炸更清楚。
    if not isinstance(text, str):
        if text is None:
            plan.update(ok=False, error="empty text: caller passed None", retryable=False)
            return plan
        text = str(text)
    if not text.strip():
        plan.update(ok=False, error="empty text: nothing to send", retryable=False)
        return plan
    if not talker:
        plan.update(ok=False, error="target not found in WeFlow session list / contacts",
                    retryable=False, known=[s.get("displayName") for s in rows[:20]])
        return plan

    attempts_max = max(1, int(cfg.get("send_attempts", 3)))
    retry_wait = float(cfg.get("retry_wait_s", 2.0))
    verify_timeout = float(cfg.get("verify_timeout_s", 12))
    attempt_log, since, found = [], 0, None

    own_client = client is None
    if own_client:
        client = Client()
    # 本进程内上一条给同一联系人的消息刚刚拿到回读确认、窗口也没动 → 这一条不必再探测会话
    trusted = warm_session_trusted(cfg, target, plan)
    # 同一次回读确认还说明：输入框已被回车清空、且仍由微信持有键盘 → 第 1 次尝试只需一个 delete
    fresh_input = input_box_trusted_clean(cfg, target)
    _warm_forget_clean()   # 本条还没验证送达；失败的话下一条必须重新探测
    try:
        if not trusted and not wait_for_session(client, cfg, plan):
            return plan
        if cfg.get("dry_run", True) and not commit:
            if not open_chat(client, cfg, target, plan, do_ocr=do_ocr, assume_current=assume_current):
                return plan
            plan.update(ok=True, dry_run=True, verified=False)
            return plan

        before = last_message(cfg, talker)
        since = (before or {}).get("localId") or 0
        t_start = time.time()
        # 2026-10-22 刷屏事故：回读判断如果失败，每条消息会被重发 3 次再进队列，
        # 队列每 20s 再补一遍，同一条「翻唱完成」在群里连发 12 条。
        # 发之前先看 180s 内是不是已经有同样的文本 —— 有就不发。
        _recent = verify(cfg, talker, text, time.time() + 2, 0, time.time() - 180)
        if _recent:
            plan.update(ok=True, verified=True, localId=_recent.get("localId"),
                        dedup_recent=True)
            log.info("[wxui] ↺ 跳过重复发送 %s（180s 内已有同样文本 localId=%s）",
                     target, _recent.get("localId"))
            return plan
        for attempt in range(1, attempts_max + 1):
            if attempt > 1:
                # The previous attempt may have arrived after its read-back window: confirm
                # before re-sending, otherwise the contact gets the same message twice.
                already = verify(cfg, talker, text, time.time() + cfg.get("precheck_timeout_s", 3),
                                 since, t_start - 3)
                if already:
                    found = already
                    attempt_log.append({"attempt": attempt, "stage": "precheck_delivered",
                                        "localId": already.get("localId")})
                    break
            if attempt > 1 and not wait_for_session(client, cfg, plan):
                # 第 1 次尝试的会话探测在进循环前已经做过（热路径上连那次都省了），
                # 只有重试才需要在 retry_wait 之后重新确认会话还在。
                attempt_log.append({"attempt": attempt, "stage": "session",
                                    "error": plan.get("error")})
                break
            try:
                opened = open_chat(client, cfg, target, plan, do_ocr=do_ocr, assume_current=assume_current)
            except Exception as e:  # windows-mcp refuses input while the human is driving
                opened = False
                plan.update(error="desktop input refused: %s" % e, retryable=True)
            if not opened:
                attempt_log.append({"attempt": attempt, "stage": "open_chat",
                                    "error": plan.get("error")})
                if not plan.get("retryable"):
                    break
                time.sleep(retry_wait)
                continue
            try:
                plan["input_clear"] = clear_input(client, cfg, plan.get("window_rect"),
                                                  focused_clean=fresh_input and attempt == 1)
                # 2026-10-21（用户实测：听到系统提示音、键鼠自己乱动）：键盘事件会打到**当前
                # 前台窗口**。用户从不把微信置顶，所以打字前必须再确认一次前台就是微信，
                # 不在前台就放弃本次尝试（宁可重发，也不能把内容敲进别人正在用的窗口）。
                if _native_on(cfg, "native_input"):
                    fg_hwnd, fg_title, _fg_cls = winio.foreground_info()
                    main_hwnd = find_wechat(cfg)
                    if not main_hwnd or fg_hwnd != main_hwnd:
                        raise RuntimeError("foreground is not the WeChat main window "
                                           "(fg=%r title=%r); refusing to type"
                                           % (fg_hwnd, fg_title))
                if _native_on(cfg, "native_input") and cfg.get("defer_to_human", True) and winio.human_busy():
                    # 人正在用键鼠时不抢输入：此刻敲的每个键都算在用户头上，
                    # 会话也可能正被用户切来切去（2026-10-23 连发三份到别人的事故）。
                    plan.update(error="deferred: user is using the computer", retryable=False,
                                no_requeue=True)
                    attempt_log.append({"attempt": attempt, "stage": "defer_to_human"})
                    break
                if _native_on(cfg, "native_input"):
                    # 原生粘贴：写剪贴板 + Ctrl+V + Enter ≈ 20ms，而三次 windows-mcp 输入调用
                    # 要 ~1.7s（每条消息都付一遍，这是“发一条要好几秒”的主要来源）。
                    winio.set_clipboard(text)
                    winio.chord(winio.VK_CTRL, winio.VK_V)
                    if cfg.get("identity_recheck_before_enter", True):
                        _id_ok, _id_why = _identity_recheck(cfg, target)
                        if not _id_ok:
                            # 粘贴之后会话被切走了：删掉刚粘进去的内容，绝不回车。
                            winio.chord(winio.VK_CTRL, winio.VK_A)
                            winio.chord(winio.VK_BACK)
                            plan.update(error="chat switched mid-send (%s); input cleared" % _id_why,
                                        retryable=False, no_requeue=True)
                            attempt_log.append({"attempt": attempt, "stage": "identity_recheck",
                                                "why": _id_why})
                            break
                    time.sleep(float(cfg.get("after_paste_wait_text", 0.3)))
                    winio.chord(winio.VK_RETURN)
                else:
                    client.call("Clipboard", {"mode": "set", "text": text})
                    client.call("Shortcut", {"shortcut": "ctrl+v"})
                    # 分段回复提速：正文粘贴几乎是瞬时的，0.5s 是白等；0.3s 足够微信接收粘贴内容
                    time.sleep(float(cfg.get("after_paste_wait_text", 0.3)))
                    client.call("Shortcut", {"shortcut": "enter"})
            except Exception as e:
                # Desktop handed back to the human mid-send (USER_CONTROL / TAKEOVER_PENDING).
                # Leave nothing behind: the next attempt clears the box before pasting.
                attempt_log.append({"attempt": attempt, "stage": "input",
                                    "error": "desktop input refused: %s" % e})
                plan.update(error="desktop input refused: %s" % e, retryable=True)
                time.sleep(retry_wait)
                continue
            found = verify(cfg, talker, text, time.time() + verify_timeout, since, t_start - 3)
            attempt_log.append({"attempt": attempt, "verified": bool(found),
                                "localId": (found or {}).get("localId"),
                                "open_attempts": plan.get("open_attempts")})
            if not found and cfg.get("identity_recheck_before_enter", True):
                # 回读失败时先看会话是不是被切走了：如果文字其实敲进了别人的窗口，
                # 再「重试」只会让那个人收到第二份、第三份（2026-10-23 连发三份事故）。
                _id2_ok, _id2_why = _identity_recheck(cfg, target)
                if not _id2_ok:
                    plan.update(error=("sent into a different chat (%s); not retrying "
                                       "to avoid duplicate delivery" % _id2_why),
                                retryable=False, no_requeue=True)
                    attempt_log.append({"attempt": attempt, "stage": "identity_after_send",
                                        "why": _id2_why})
                    break
            if found:
                # 只有拿到回读确认才算「输入框已空、窗口仍在原位」，下一条才敢走热路径
                _warm_mark(target, plan.get("window_rect"), header_verified=True, clean=True)
                learned = _learn_alias(cfg, target, plan)
                if learned:
                    plan["learned_alias"] = learned
                break
            time.sleep(retry_wait)
    finally:
        if own_client:
            client.close()

    plan["before_local_id"] = since
    if not found and (plan.get("identity_check") or {}).get("verified_by") in ("", None):
        # 身份闸拦下的发送：一个字都没敲出去，重发只会把同一条内容再塞进当前会话。
        # 标记 no_requeue，让 wxui_sender 不要把它塞进重发队列（2026-10-22 连发 10 条事故）。
        plan["no_requeue"] = True
    return _assemble(plan, attempt_log, found, "text", cfg, talker, text=text)


def send_image(cfg, target, image_path, commit=False, do_ocr=True, assume_current=False,
               client=None, as_file=False):
    """Send a picture/sticker: click the chat, put the file on the clipboard, Ctrl+V, Enter.

    Same retry/verify contract as send(): an image only counts as sent once it is read
    back out of WeChat's own database.

    2026-10-20: `as_file=True` 复用同一条通道发**任意文件**（AI 点歌的翻唱 mp3 走这里）。
    微信对「剪贴板里的文件路径」Ctrl+V 的落法：图片走图片消息，非图片扩展名走 appmsg
    文件卡片（localType 低字节 49 + <type>6</type> + title=文件名）—— `classify_outgoing`
    已经认得这种形态，所以发送/回读验证逻辑可以完全共用。
    """
    rows = api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
    talker = talker_for(cfg, target, rows)
    plan = {"target": target, "talker": talker, "kind": "file" if as_file else "image"}
    plan["image" if not as_file else "file"] = image_path
    if not os.path.exists(image_path or ""):
        plan.update(ok=False, error=("file not found" if as_file else "image file not found"),
                    retryable=False)
        return plan
    animated = False if as_file else is_animated_image(image_path)
    plan["image_animated"] = animated
    plan["clipboard_mode"] = "file_drop" if (animated or as_file) else "bitmap"
    expect_name = os.path.basename(image_path)
    expect_md5 = file_md5(image_path)
    plan["image_md5"] = expect_md5
    if animated:
        # 数据库回读只能证明「图片已发出」，证明不了动画 —— 需要微信端目视确认。
        plan["verify_note"] = "动图：DB 回读无法证明动画帧，需在微信端目视确认"
    if not talker:
        plan.update(ok=False, error="target not found in WeFlow session list / contacts",
                    retryable=False, known=[s.get("displayName") for s in rows[:20]])
        return plan

    attempts_max = max(1, int(cfg.get("send_attempts", 3)))
    retry_wait = float(cfg.get("retry_wait_s", 2.0))
    verify_timeout = float(cfg.get("verify_timeout_image_s", 15))
    attempt_log, since, found = [], 0, None

    own_client = client is None
    if own_client:
        client = Client()
    # 本进程内上一条给同一联系人的消息刚刚拿到回读确认、窗口也没动 → 这一条不必再探测会话
    trusted = warm_session_trusted(cfg, target, plan)
    # 同一次回读确认还说明：输入框已被回车清空、且仍由微信持有键盘 → 第 1 次尝试只需一个 delete
    fresh_input = input_box_trusted_clean(cfg, target)
    _warm_forget_clean()   # 本条还没验证送达；失败的话下一条必须重新探测
    try:
        if not trusted and not wait_for_session(client, cfg, plan):
            return plan
        if cfg.get("dry_run", True) and not commit:
            if not open_chat(client, cfg, target, plan, do_ocr=do_ocr, assume_current=assume_current):
                return plan
            plan.update(ok=True, dry_run=True, verified=False)
            return plan

        before = last_message(cfg, talker)
        since = (before or {}).get("localId") or 0
        t_start = time.time()
        for attempt in range(1, attempts_max + 1):
            if attempt > 1 and since:
                already = verify_image(cfg, talker,
                                       time.time() + cfg.get("precheck_timeout_s", 3), since,
                                       expect_name=expect_name, expect_md5=expect_md5,
                                       since_time=t_start - 3)
                if already:
                    found = already
                    attempt_log.append({"attempt": attempt, "stage": "precheck_delivered",
                                        "localId": already.get("localId")})
                    break
            if attempt > 1 and not wait_for_session(client, cfg, plan):
                # 第 1 次尝试的会话探测在进循环前已经做过（热路径上连那次都省了），
                # 只有重试才需要在 retry_wait 之后重新确认会话还在。
                attempt_log.append({"attempt": attempt, "stage": "session",
                                    "error": plan.get("error")})
                break
            try:
                opened = open_chat(client, cfg, target, plan, do_ocr=do_ocr, assume_current=assume_current)
            except Exception as e:  # windows-mcp refuses input while the human is driving
                opened = False
                plan.update(error="desktop input refused: %s" % e, retryable=True)
            if not opened:
                attempt_log.append({"attempt": attempt, "stage": "open_chat",
                                    "error": plan.get("error")})
                if not plan.get("retryable"):
                    break
                time.sleep(retry_wait)
                continue
            try:
                plan["input_clear"] = clear_input(client, cfg, plan.get("window_rect"),
                                                  focused_clean=fresh_input and attempt == 1)
                ok, err = set_clipboard_image(image_path,
                                              plan.get("clipboard_mode") == "file_drop")
                if not ok:
                    plan.update(ok=False,
                                error="could not put image on the clipboard: %s" % err,
                                retryable=False)
                    return plan
                client.call("Shortcut", {"shortcut": "ctrl+v"})
                time.sleep(cfg.get("after_paste_wait", 1.2))
                client.call("Shortcut", {"shortcut": "enter"})
            except Exception as e:  # desktop handed back to the human mid-send
                attempt_log.append({"attempt": attempt, "stage": "input",
                                    "error": "desktop input refused: %s" % e})
                plan.update(error="desktop input refused: %s" % e, retryable=True)
                time.sleep(retry_wait)
                continue
            found = verify_image(cfg, talker, time.time() + verify_timeout, since,
                                 expect_name=expect_name, expect_md5=expect_md5,
                                 since_time=t_start - 3)
            attempt_log.append({"attempt": attempt, "verified": bool(found),
                                "localId": (found or {}).get("localId"),
                                "delivered_as": classify_outgoing(found, expect_name, expect_md5) if found else None})
            if found:
                _warm_mark(target, plan.get("window_rect"), header_verified=True, clean=True)
                learned = _learn_alias(cfg, target, plan)
                if learned:
                    plan["learned_alias"] = learned
                break
            time.sleep(retry_wait)
    finally:
        if own_client:
            client.close()

    plan["before_local_id"] = since
    plan["delivered_as"] = (classify_outgoing(found, expect_name, expect_md5)
                            if found else None)
    return _assemble(plan, attempt_log, found, plan.get("kind") or "image", cfg, talker)


def send_file(cfg, target, file_path, commit=False, do_ocr=True, assume_current=False,
              client=None):
    """Send any file (e.g. the AI点歌 cover mp3) as a WeChat file message.

    WeChat PC turns 「剪贴板里的文件」+ Ctrl+V into an appmsg file card (or an image, when
    the extension is an image); `classify_outgoing` recognises both, so this reuses the
    verified send_image pipeline instead of inventing a second delivery path.
    """
    return send_image(cfg, target, file_path, commit, do_ocr=do_ocr,
                      assume_current=assume_current, client=client, as_file=True)


def verify_delivered(cfg, spec):
    """Is this exact message already in WeChat's DB? Asked before every resend.

    Verification is what makes retrying safe: a message that did arrive (only the read-back
    was slow) is detected here and never sent twice.
    """
    target = spec.get("target")
    rows = api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
    talker = talker_for(cfg, target, rows)
    plan = {"target": target, "talker": talker, "ok": True, "verified": False}
    if not talker:
        return {**plan, "ok": False, "error": "target not found in WeFlow session list / contacts"}
    since = int(spec.get("since_local_id") or 0)
    since_time = float(spec.get("since_time") or 0) or None
    deadline = time.time() + float(spec.get("timeout", 3))
    if spec.get("text"):
        m = verify(cfg, talker, spec["text"], deadline, since, since_time)
    elif spec.get("path"):
        if not since and not since_time:
            return {**plan, "error": "since_local_id is required to check an image"}
        _p = str(spec.get("path") or "")
        m = verify_image(cfg, talker, deadline, since,
                         expect_name=os.path.basename(_p),
                         expect_md5=file_md5(_p) if os.path.exists(_p) else None,
                         since_time=since_time)
    else:
        return {**plan, "ok": False, "error": "spec needs text or path"}
    return {**plan, "verified": bool(m), "localId": (m or {}).get("localId"),
            "createTime": (m or {}).get("createTime")}


def probe_session(cfg, client=None):
    """One-shot usability probe: window, restored rect, screen, desktop control.

    Returns (hwnd, rect, ok, reason, control_state, control_wait_s). Used by `info` and
    `session`, and mirrors what wait_for_session() insists on before typing.
    `client` 由常驻 worker 复用传入；为 None 时自查自关。
    """
    hwnd = find_wechat(cfg)
    rect = restored_rect(hwnd) if hwnd else None
    ok, reason, ctl, wait = False, "WeChat window not found", None, 0.0
    if rect:
        own_client = client is None
        if own_client:
            client = Client()
        try:
            ok, reason = screen_capturable(client, cfg, rect)
            ctl, wait = control_state(client)
            if ok and ctl not in (None, "ready", "ai"):
                ok = False
                reason = "user is using the desktop (control=%s, ~%.0fs)" % (ctl, wait)
        finally:
            if own_client:
                client.close()
    return hwnd, rect, ok, reason, ctl, wait


def worker_dispatch(cfg, client, subcmd, spec):
    """One JSON request → one plan dict. Used by the resident worker."""
    if subcmd == "send":
        return send(cfg, spec.get("target"), spec.get("text"), bool(spec.get("commit")),
                    do_ocr=bool(spec.get("do_ocr", True)),
                    assume_current=bool(spec.get("assume_current")), client=client)
    if subcmd == "send-image":
        return send_image(cfg, spec.get("target"), spec.get("path"),
                          bool(spec.get("commit")),
                          do_ocr=bool(spec.get("do_ocr", True)),
                          assume_current=bool(spec.get("assume_current")), client=client)
    if subcmd == "send-file":
        return send_file(cfg, spec.get("target"), spec.get("path"),
                         bool(spec.get("commit")),
                         do_ocr=bool(spec.get("do_ocr", True)),
                         assume_current=bool(spec.get("assume_current")), client=client)
    if subcmd == "verify":
        return verify_delivered(cfg, spec)
    if subcmd == "info":
        hwnd, rect, ok, reason, ctl, wait = probe_session(cfg, client=client)
        return {"ok": ok, "session_ok": ok, "session_reason": reason, "hwnd": hwnd,
                "rect": list(rect) if rect else None, "control_state": ctl,
                "control_wait_s": wait,
                "control_preempted": str(reason or "").startswith("desktop control preempted")}
    return {"ok": False, "retryable": False, "error": "unknown worker cmd %r" % (subcmd,)}


def worker_loop(cfg):
    """常驻模式：一个进程复用同一个 windows-mcp 客户端，逐行读 JSON 请求。

    2026-10-16 加入（用户要求：分段回复别每条都重新验证窗口位置和名字）。一次性进程每次
    都要付 ~3s 的 Windows-MCP 启动成本，3~6 段回复就是十几秒；常驻后这笔成本只付一次，
    而且「聊天窗口现在停在谁身上」这份状态（_WARM）能跨消息复用，同一联系人的第 2 条起
    不再扫侧边栏、不点行、也不必重新 OCR 名字，直接粘贴→回车。

    协议：stdin 每行一个 JSON {"id":N,"cmd":"send|send-image|verify|info","spec":{...}}；
    stdout 每行一个响应 {"id":N,"ok":bool,"data":{...}}。stdin 关闭（父进程退出）即结束。
    """
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    client = Client()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except ValueError:
                continue
            resp = {"id": req.get("id")}
            try:
                data = worker_dispatch(cfg, client, str(req.get("cmd") or ""),
                                       req.get("spec") or {})
            except Exception as exc:  # 单条请求出错不能让常驻进程死掉
                data = {"ok": False, "retryable": True,
                        "error": "%s: %s" % (type(exc).__name__, exc)}
            resp["ok"] = bool(isinstance(data, dict)
                              and (data.get("ok") or data.get("verified")))
            resp["data"] = data
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        try:
            client.close()
        except Exception:
            pass
    return 0


def sidebar_rows(cfg):
    """Read-only: OCR the sidebar and report which names it can actually see.

    Exists because "the bot cannot find that chat any more" is almost always a rename:
    WeFlow still serves the old displayName while WeChat draws the new remark. This dumps
    what OCR reads, so the fix (one line in contact_aliases) is obvious instead of guessed.
    """
    hwnd = find_wechat(cfg)
    if not hwnd:
        return {"ok": False, "error": "WeChat window not found"}
    rect = restored_rect(hwnd)
    if not rect_usable(rect):
        return {"ok": False, "error": "WeChat window is minimized/hidden (rect=%s)" % (list(rect),)}
    left, top = rect[0], rect[1]
    region = clamp_region(
        [left + cfg["sidebar_capture_x_offset"], top + cfg["sidebar_capture_y_offset"],
         left + cfg["sidebar_capture_x2_offset"], rect[3] - cfg["sidebar_capture_bottom_margin"]],
        rect,
    )
    client = Client()
    try:
        png, err = shot_ex(client, region, os.path.join(ROOT, "logs", "wmcp", "probe"))
    finally:
        client.close()
    if not png:
        return {"ok": False, "error": "no image from Screenshot (%s)" % (_brief(err) or "empty region"),
                "control_preempted": bool(preempted(err))}
    res = ocr_lines(png)
    return {"ok": bool(res.get("ok")), "png": png, "engine": res.get("engine"),
            "lines": [{"text": ln.get("text"), "y": ln.get("y")}
                      for ln in (res.get("lines") or [])],
            "aliases": cfg.get("contact_aliases") or {}}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("worker", help="stay resident and serve JSON-line send/verify requests "
                                  "with one warm Windows-MCP client (used by the bridge)")
    sub.add_parser("sessions")
    sub.add_parser("rows", help="read-only: print the names OCR currently reads in the "
                                "sidebar (diagnose a renamed contact)")
    sub.add_parser("session", help="probe whether WeChat is usable right now "
                                   "(window / minimized / screen capturable)")
    p = sub.add_parser("send")
    p.add_argument("--target")
    p.add_argument("--text")
    p.add_argument("--spec", help="UTF-8 JSON file {target,text,commit} — used by the bridge, "
                                  "because argv encoding mangles Chinese from a spawned process")
    p.add_argument("--commit", action="store_true")
    p.add_argument("--no-ocr", action="store_true", help="skip OCR, use assume_row_index")
    p.add_argument("--assume-current", action="store_true",
                   help="the target's chat is already open (burst to the same contact): "
                        "verify only the chat header and skip the sidebar OCR scan; a "
                        "mismatch falls back to the full scan, so nothing is mis-sent")
    pi = sub.add_parser("send-image")
    pi.add_argument("--target")
    pi.add_argument("--path", help="image file to paste into the chat (png/jpg/gif/webp)")
    pi.add_argument("--spec", help="UTF-8 JSON file {target,path,commit}")
    pi.add_argument("--commit", action="store_true")
    pi.add_argument("--no-ocr", action="store_true")
    pi.add_argument("--assume-current", action="store_true")
    pf = sub.add_parser("send-file", help="send any file (mp3 etc.) as a WeChat file card")
    pf.add_argument("--target")
    pf.add_argument("--path")
    pf.add_argument("--spec", help="UTF-8 JSON file {target,path,commit}")
    pf.add_argument("--commit", action="store_true")
    pf.add_argument("--no-ocr", action="store_true")
    pf.add_argument("--assume-current", action="store_true")
    pv = sub.add_parser("verify", help="read-only check that a message is already delivered "
                                       "(used before a resend so nothing is sent twice)")
    pv.add_argument("--spec", help="UTF-8 JSON file {target,text|path,since_local_id,timeout}")
    args = ap.parse_args()

    cfg = config()
    if args.cmd == "worker":
        return worker_loop(cfg)
    if args.cmd == "info":
        hwnd, rect, ok, reason, ctl, wait = probe_session(cfg)
        out = {"hwnd": hwnd, "rect": list(window_rect(hwnd)) if hwnd else None,
               "rect_restored": list(rect) if rect else None,
               "session_ok": ok, "session_reason": reason,
               "control_state": ctl, "control_wait_s": wait,
               "control_preempted": str(reason or "").startswith("desktop control preempted"),
               "dry_run": cfg.get("dry_run", True), "geometry": {
                   "sidebar_x_offset_px": cfg["sidebar_x_offset_px"],
                   "first_row_offset_px": cfg["first_row_offset_px"],
                   "row_pitch_px": cfg["row_pitch_px"]}}
    elif args.cmd == "rows":
        out = sidebar_rows(cfg)
    elif args.cmd == "session":
        hwnd, rect, ok, reason, ctl, wait = probe_session(cfg)
        out = {"ok": ok, "usable": ok, "reason": reason, "hwnd": hwnd,
               "rect": list(rect) if rect else None,
               "control_state": ctl, "control_wait_s": wait,
               "control_preempted": str(reason or "").startswith("desktop control preempted")}
    elif args.cmd == "verify":
        if not args.spec:
            print(json.dumps({"ok": False, "error": "--spec required"}, ensure_ascii=False))
            return 2
        with open(args.spec, encoding="utf-8") as fh:
            out = verify_delivered(cfg, json.load(fh))
    elif args.cmd == "sessions":
        rows = api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
        out = [{"i": i, "name": s.get("displayName"), "username": s.get("username"),
                "last": s.get("lastTimestamp")} for i, s in enumerate(rows)]
    elif args.cmd == "send":
        target, text, commit = args.target, args.text, args.commit
        assume_current = bool(args.assume_current)
        if args.spec:
            with open(args.spec, encoding="utf-8") as fh:
                spec = json.load(fh)
            target, text = spec.get("target"), spec.get("text")
            commit = bool(spec.get("commit"))
            assume_current = bool(spec.get("assume_current", assume_current))
        if not target or not text:
            print(json.dumps({"ok": False, "error": "--target/--text or --spec required"},
                             ensure_ascii=False))
            return 2
        out = send(cfg, target, text, commit, do_ocr=not args.no_ocr,
                   assume_current=assume_current)
    else:  # send-image / send-file
        target, path, commit = args.target, args.path, args.commit
        assume_current = bool(args.assume_current)
        if args.spec:
            with open(args.spec, encoding="utf-8") as fh:
                spec = json.load(fh)
            target, path = spec.get("target"), spec.get("path")
            commit = bool(spec.get("commit"))
            assume_current = bool(spec.get("assume_current", assume_current))
        if not target or not path:
            print(json.dumps({"ok": False, "error": "--target/--path or --spec required"},
                             ensure_ascii=False))
            return 2
        fn = send_file if args.cmd == "send-file" else send_image
        out = fn(cfg, target, path, commit, do_ocr=not args.no_ocr,
                 assume_current=assume_current)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = out.get("ok") or out.get("dry_run") if isinstance(out, dict) else True
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
