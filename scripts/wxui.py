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

try:  # noqa: E402  - must match Windows-MCP's physical-pixel space
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

CONFIG_PATH = os.path.join(HERE, "wxui-config.json")


def config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def token(cfg):
    with open(os.path.join(ROOT, cfg["token_file"]), encoding="utf-8") as fh:
        return fh.read().strip()


def api(cfg, path, **params):
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
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def visit(hwnd, _):
        t = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, t, 512)
        user32.GetClassNameW(hwnd, cls, 512)
        if cls.value.startswith(cfg["window_class_prefix"]) and user32.IsWindowVisible(hwnd):
            if (name_match(title, t.value) > 0) if fuzzy else (t.value == title):
                hits.append(hwnd)
        return True

    user32.EnumWindows(proc(visit), 0)
    return hits[0] if hits else None


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
    # 2026-10-16: 分段回复每条都要聚焦一次，而 ShowWindow + 0.45s 的等待只有「窗口不在前台
    # 或已最小化」时才需要。已经在最前面就直接返回，省掉每条消息 0.45s。
    if user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
        return True
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.45)  # a window restored from minimized ignores clicks for a moment
    for _ in range(3):
        if user32.GetForegroundWindow() == hwnd:
            return True
        fg = user32.GetForegroundWindow()
        mine = kernel32.GetCurrentThreadId()
        theirs = user32.GetWindowThreadProcessId(fg, None)
        user32.AttachThreadInput(mine, theirs, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            user32.AttachThreadInput(mine, theirs, False)
        time.sleep(0.3)
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
    if focused_clean and cfg.get("input_fast_clear", True):
        client.call("Shortcut", {"shortcut": "delete"})
        time.sleep(0.05)
        return {"x": x, "y": y, "fast": True}
    client.call("Click", {"loc": [x, y], "clicks": 1})
    time.sleep(0.15)
    client.call("Shortcut", {"shortcut": "ctrl+a"})
    time.sleep(0.08)
    client.call("Shortcut", {"shortcut": "delete"})
    time.sleep(0.12)
    return {"x": x, "y": y}


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
    if not shot(client, tiny, os.path.join(ROOT, "logs", "wmcp", "probe"), tries=1):
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
    while True:
        hwnd = find_wechat(cfg) or hwnd
        if hwnd:
            ctl, wait = control_state(client)
            if ctl not in (None, "ready", "ai"):
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
    """Whitespace-insensitive comparison key (Windows OCR puts spaces in CJK)."""
    return "".join((text or "").split()).lower()


# Windows OCR cannot read emoji and drops most symbols, and WeChat truncates a long name
# in the sidebar — so a group named 示例群聊B comes back as "情VIP508" (or split
# over two lines). Comparing raw strings can therefore never match, whatever the remark is.
_NAME_KEEP = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
NAME_MATCH_CLEAR = 0.9         # one side contains the other: trust it like today's partial
NAME_MATCH_MIN_RATIO = 0.7     # otherwise: this share of the shorter name, as one run
NAME_MATCH_MIN_CHARS = 3       # ...and at least this many characters in a row
NAME_MATCH_UNIQUE_MARGIN = 0.15  # a fuzzy winner must beat the runner-up by this much


def name_key(text):
    """Emoji/symbol/whitespace-insensitive key for comparing a chat name with OCR text."""
    return _NAME_KEEP.sub("", (text or "")).lower()


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
    if run < NAME_MATCH_MIN_CHARS:
        return 0.0
    ratio = run / float(min(len(a), len(b)))
    return ratio if ratio >= NAME_MATCH_MIN_RATIO else 0.0


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


def shot(client, region, keep_dir, tries=2):
    """Screenshot `region`; return the saved PNG path (see wmcp CLI for decoding)."""
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
        if attempt + 1 < tries:  # the first frame after a window restore can come back empty
            time.sleep(0.5)
    return None


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
    """True when the open chat's header OCR names `key`.

    Exactness or containment on any line (or on the join of the lines, because a remarked
    chat shows the remark with the real group name underneath) is always enough. A merely
    similar line is accepted only when it is clearly the best one in the header — the row
    was already located from this same name, so "similar and unique" is what OCR noise
    (dropped emoji, a truncated title) leaves behind.
    """
    lines = [t for t in (texts or []) if t]
    if name_matches_any(key, lines):
        return True
    scored = sorted((name_match(key, t) for t in lines + ([" ".join(lines)] if len(lines) > 1 else [])),
                    reverse=True)
    if not scored or scored[0] <= 0:
        return False
    runner_up = scored[1] if len(scored) > 1 else 0.0
    return scored[0] - runner_up >= NAME_MATCH_UNIQUE_MARGIN


def find_row(client, cfg, rect, target):
    """Click point of `target`'s sidebar row, located by OCR. Returns ((x, y), debug).

    The click lands on the matched name box itself, so neither the row pitch nor the
    first-row offset has to be exact (they drift as the list scrolls, the window is
    resized or WeChat changes its layout); the box x is used too, so a resized window
    no longer pushes the click into the chat pane.
    """
    left, top, right, bottom = rect
    region = clamp_region(
        [left + cfg["sidebar_capture_x_offset"], top + cfg["sidebar_capture_y_offset"],
         left + cfg["sidebar_capture_x2_offset"], bottom - cfg["sidebar_capture_bottom_margin"]],
        rect,
    )
    png = shot(client, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        return None, {"error": "no image from Screenshot (region off screen?)",
                      "region": [int(v) for v in region]}
    size = image_size(png)
    res = ocr_lines(png)
    if not res.get("ok"):
        return None, {"error": "ocr failed", "region": [int(v) for v in region], "detail": res}
    img_w, img_h = size if size else (region[2] - region[0], region[3] - region[1])
    scale_y = (region[3] - region[1]) / float(img_h)
    scale_x = (region[2] - region[0]) / float(img_w)
    key = fit(target)
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
                           "exact": text == key, "partial": key in text or text in key,
                           "score": round(name_match(target, line["text"]), 3)})
    exact = [c for c in candidates if c["exact"]]
    partial = [c for c in candidates if c["partial"]]
    pick = (exact or partial)
    fuzzy = False
    if not pick:
        # Emoji-laden or truncated names never satisfy exact/partial, so fall back to a
        # similarity match — but only when one row is clearly the best candidate, since a
        # wrong pick would click the wrong chat.
        scored = sorted((c for c in candidates if c["score"] > 0),
                        key=lambda c: (-c["score"], c["y"]))
        if scored:
            runner_up = scored[1]["score"] if len(scored) > 1 else 0.0
            if scored[0]["score"] - runner_up >= NAME_MATCH_UNIQUE_MARGIN:
                pick, fuzzy = [scored[0]], True
    debug = {"png": png, "engine": res.get("engine"), "candidates": candidates[:40],
             "key": key, "fuzzy": fuzzy}
    if not pick:
        return None, debug
    pick.sort(key=lambda c: (not c["exact"], c["y"]))
    best = pick[0]
    debug["picked"] = {"text": best["text"], "score": best.get("score"), "fuzzy": fuzzy}
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
    key = fit(target)
    best_score, best_user = 0.0, None
    for s in sessions:
        if fit(s.get("displayName")) == key:
            return s.get("username")
        score = name_match(target, s.get("displayName"))
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
        if any(fit(n) == key for n in names):
            return c.get("username")
        score = max(name_match(target, n) for n in names)
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


def verify(cfg, talker, text, deadline, since_local_id=0):
    """The outgoing text message read back from WeChat's DB, or None."""
    key = norm(text)
    if not key:
        return None
    while time.time() < deadline:
        msgs = api(cfg, "/api/v1/messages", talker=talker, limit=10).get("messages", [])
        for m in reversed(msgs):
            if m.get("isSend") != 1:
                continue
            if since_local_id and (m.get("localId") or 0) <= since_local_id:
                continue
            if key in norm(m.get("content")):
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


def verify_image(cfg, talker, deadline, since_local_id, expect_name=None, expect_md5=None):
    """A new outgoing image/emoji/file message read back from WeChat's DB, or None."""
    while time.time() < deadline:
        msgs = api(cfg, "/api/v1/messages", talker=talker, limit=10).get("messages", [])
        for m in reversed(msgs):
            if since_local_id and (m.get("localId") or 0) <= since_local_id:
                continue
            if classify_outgoing(m, expect_name, expect_md5):
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
    left, top, right, bottom = rect
    region = clamp_region(
        [left + cfg["chat_header_x_offset_px"], top + cfg["chat_header_y_offset_px"],
         left + cfg["chat_header_x2_offset_px"], top + cfg["chat_header_y2_offset_px"]],
        rect,
    )
    png = shot(client, region, os.path.join(ROOT, "logs", "wmcp"))
    if not png:
        return False, {"error": "no header image"}
    res = ocr_lines(png)
    texts = [ln["text"] for ln in res.get("lines", [])] if res.get("ok") else []
    key = fit(target)
    matched = header_names_target(target, texts)
    return matched, {"png": png, "texts": texts[:6], "key": key, "matched": matched}


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
    """
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
        return fail(plan, "WeChat window not found")
    plan["window_rect"] = list(window_rect(hwnd))
    # Windows refuses SetForegroundWindow from a background process; without this the
    # click lands on whatever is on top and the text is typed into the wrong app.
    if not focus(hwnd):
        return fail(plan, "could not bring WeChat to the foreground")
    rect = restored_rect(hwnd)
    plan["window_rect"] = list(rect)
    if not rect_usable(rect):
        return fail(plan, "WeChat window is minimized/hidden (rect=%s); put it back "
                          "on screen and retry" % (list(rect),))
    if do_ocr and not find_window(cfg, target, fuzzy=True):
        reuse, header_recent = _warm_get(cfg, target, rect)
        if reuse and header_recent:
            # 常驻 worker 刚给这个人发过消息：窗口没动过、头部名字也是近期确认过的。
            # 这一步就是用户要的「直接往对话框粘贴发送」——不扫侧边栏、不点行、不 OCR。
            # header_at 不刷新，所以超过 warm_header_recheck_s 后下一条会自动再确认一次名字。
            plan["claimed"] = "chat already on target (warm session: no OCR)"
            plan["warm_header_verified"] = False
            plan["assume_current"] = {"matched": True, "warm": True}
            plan["row_point"] = []
            plan["open_attempts"] = 0
            return _chat_ready_to_type(cfg, plan, target)
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
            plan["warm_header_verified"] = True
            plan["row_point"] = []
            plan["open_attempts"] = 0
            plan["chat_header"] = header0
            return _chat_ready_to_type(cfg, plan, target)
    if do_ocr:
        point, dbg = find_row(client, cfg, rect, target)
        plan["ocr"] = {"png": dbg.get("png"), "engine": dbg.get("engine"),
                       "error": dbg.get("error"), "region": dbg.get("region"),
                       "picked": dbg.get("candidates", [{}])[0] if point else None}
        if not point:
            plan["ocr_candidates"] = dbg.get("candidates", [])[:20]
            return fail(plan, "could not locate target row by OCR")
        x, y = point
    else:
        x, y = row_point(cfg, rect, int(cfg.get("assume_row_index", 0)))
    # The chat may already be the open one (a retry, or the contact opened by hand): a
    # click is then unnecessary, and an unnecessary click on an already-open row is what
    # turns into a double click — WeChat pops the conversation out into its own window.
    if do_ocr and not find_window(cfg, target, fuzzy=True):
        already, header0 = chat_header_matches(client, cfg, rect, target)
        if already:
            plan["claimed"] = "chat already open"
            plan["warm_header_verified"] = True
            plan["row_point"] = [x, y]
            plan["open_attempts"] = 0
            plan["chat_header"] = header0
            return _chat_ready_to_type(cfg, plan, target)

    plan.update(row_point=[x, y])
    # A window restored from minimized swallows the first click (it only activates), so
    # keep clicking until the chat header really shows the target — that is the signal the
    # conversation is open, not that a click was sent. Repeat clicks are deliberately rare
    # (max_row_clicks, at least repeat_click_min_interval_s apart): two clicks in quick
    # succession are a double click, which opens the chat in a separate window.
    max_clicks = max(1, int(cfg.get("max_row_clicks", 2)))
    min_gap = float(cfg.get("repeat_click_min_interval_s", 1.6))
    attempts, header, last_click = 0, {}, 0.0
    for attempts in range(1, max_clicks + 1):
        if last_click:
            gap = min_gap - (time.time() - last_click)
            if gap > 0:
                time.sleep(gap)
        client.call("Click", {"loc": [x, y], "clicks": 1})
        last_click = time.time()
        time.sleep(cfg.get("after_click_wait", 1.4))
        if find_window(cfg, target, fuzzy=True):
            break
        if not do_ocr:
            break
        matched, header = chat_header_matches(client, cfg, rect, target)
        if matched:
            break
    plan["open_attempts"] = attempts
    plan["chat_header"] = header
    if do_ocr and not find_window(cfg, target, fuzzy=True) and not header_names_target(
            target, header.get("texts", [])):
        return fail(plan, "open chat header does not show the target")
    # 这一条路径确认过「头部名字/独立会话窗口标题」的，才算把 warm 状态置为「名字已确认」。
    plan["warm_header_verified"] = bool(find_window(cfg, target, fuzzy=True)) or bool(
        header_names_target(target, header.get("texts", [])))
    # WeChat 4.x puts the chat in its own Qt window titled with the contact name.
    # Type only once that window (or the main window) really owns the keyboard.
    return _chat_ready_to_type(cfg, plan, target)


def _assemble(plan, attempt_log, found, kind, cfg, talker):
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
    if not plan.get("error"):
        plan["error"] = ("%s not read back from WeChat DB within timeout"
                         % ("message" if kind == "text" else "image"))
    plan["retryable"] = True
    tail = api(cfg, "/api/v1/messages", talker=talker, limit=5).get("messages", [])
    plan["recent"] = [{"isSend": m.get("isSend"), "content": (m.get("content") or "")[:60]}
                      for m in tail]
    return plan


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
        for attempt in range(1, attempts_max + 1):
            if attempt > 1:
                # The previous attempt may have arrived after its read-back window: confirm
                # before re-sending, otherwise the contact gets the same message twice.
                already = verify(cfg, talker, text, time.time() + cfg.get("precheck_timeout_s", 3),
                                 since)
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
            found = verify(cfg, talker, text, time.time() + verify_timeout, since)
            attempt_log.append({"attempt": attempt, "verified": bool(found),
                                "localId": (found or {}).get("localId"),
                                "open_attempts": plan.get("open_attempts")})
            if found:
                # 只有拿到回读确认才算「输入框已空、窗口仍在原位」，下一条才敢走热路径
                _warm_mark(target, plan.get("window_rect"), header_verified=True, clean=True)
                break
            time.sleep(retry_wait)
    finally:
        if own_client:
            client.close()

    plan["before_local_id"] = since
    return _assemble(plan, attempt_log, found, "text", cfg, talker)


def send_image(cfg, target, image_path, commit=False, do_ocr=True, assume_current=False,
               client=None):
    """Send a picture/sticker: click the chat, put the file on the clipboard, Ctrl+V, Enter.

    Same retry/verify contract as send(): an image only counts as sent once it is read
    back out of WeChat's own database.
    """
    rows = api(cfg, "/api/v1/sessions", limit=200).get("sessions", [])
    talker = talker_for(cfg, target, rows)
    plan = {"target": target, "talker": talker, "image": image_path, "kind": "image"}
    if not os.path.exists(image_path or ""):
        plan.update(ok=False, error="image file not found", retryable=False)
        return plan
    animated = is_animated_image(image_path)
    plan["image_animated"] = animated
    plan["clipboard_mode"] = "file_drop" if animated else "bitmap"
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
        for attempt in range(1, attempts_max + 1):
            if attempt > 1 and since:
                already = verify_image(cfg, talker,
                                       time.time() + cfg.get("precheck_timeout_s", 3), since,
                                       expect_name=expect_name, expect_md5=expect_md5)
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
                ok, err = set_clipboard_image(image_path, plan.get("image_animated"))
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
                                 expect_name=expect_name, expect_md5=expect_md5)
            attempt_log.append({"attempt": attempt, "verified": bool(found),
                                "localId": (found or {}).get("localId"),
                                "delivered_as": classify_outgoing(found, expect_name, expect_md5) if found else None})
            if found:
                _warm_mark(target, plan.get("window_rect"), header_verified=True, clean=True)
                break
            time.sleep(retry_wait)
    finally:
        if own_client:
            client.close()

    plan["before_local_id"] = since
    plan["delivered_as"] = (classify_outgoing(found, expect_name, expect_md5)
                            if found else None)
    return _assemble(plan, attempt_log, found, "image", cfg, talker)


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
    deadline = time.time() + float(spec.get("timeout", 3))
    if spec.get("text"):
        m = verify(cfg, talker, spec["text"], deadline, since)
    elif spec.get("path"):
        if not since:
            return {**plan, "error": "since_local_id is required to check an image"}
        _p = str(spec.get("path") or "")
        m = verify_image(cfg, talker, deadline, since,
                         expect_name=os.path.basename(_p),
                         expect_md5=file_md5(_p) if os.path.exists(_p) else None)
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
    if subcmd == "verify":
        return verify_delivered(cfg, spec)
    if subcmd == "info":
        hwnd, rect, ok, reason, ctl, wait = probe_session(cfg, client=client)
        return {"ok": ok, "session_ok": ok, "session_reason": reason, "hwnd": hwnd,
                "rect": list(rect) if rect else None, "control_state": ctl,
                "control_wait_s": wait}
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


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sub.add_parser("worker", help="stay resident and serve JSON-line send/verify requests "
                                  "with one warm Windows-MCP client (used by the bridge)")
    sub.add_parser("sessions")
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
               "dry_run": cfg.get("dry_run", True), "geometry": {
                   "sidebar_x_offset_px": cfg["sidebar_x_offset_px"],
                   "first_row_offset_px": cfg["first_row_offset_px"],
                   "row_pitch_px": cfg["row_pitch_px"]}}
    elif args.cmd == "session":
        hwnd, rect, ok, reason, ctl, wait = probe_session(cfg)
        out = {"ok": ok, "usable": ok, "reason": reason, "hwnd": hwnd,
               "rect": list(rect) if rect else None,
               "control_state": ctl, "control_wait_s": wait}
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
    else:  # send-image
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
        out = send_image(cfg, target, path, commit, do_ocr=not args.no_ocr,
                         assume_current=assume_current)

    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = out.get("ok") or out.get("dry_run") if isinstance(out, dict) else True
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
