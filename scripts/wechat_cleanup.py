r"""把微信的「独立会话窗口」强制关掉 / 隐藏，并恢复主窗口。

用法：
    python -X utf8 scripts\wechat_cleanup.py            # 干跑，只报告
    python -X utf8 scripts\wechat_cleanup.py --apply    # 执行关闭/隐藏

背景：微信 4.x 里双击会话行会弹出独立聊天窗口，用户反馈「悬浮聊天栏关不掉，
只能退出微信」。这里用窗口级操作处理：先 WM_CLOSE，再 SC_CLOSE，最后
SW_HIDE + 挪出屏幕（工具窗口只认隐藏）。
"""
import json
import sys

import wxui
import winio


def main() -> int:
    apply = "--apply" in sys.argv
    cfg = wxui.config()
    out = {"apply": apply, "strays_before": [], "closed": [], "raised": False}
    try:
        out["strays_before"] = winio.list_wechat_chat_windows()
    except Exception as exc:  # noqa: BLE001
        out["list_error"] = str(exc)
    main_hwnd = wxui.find_wechat(cfg)
    out["main_hwnd"] = main_hwnd
    if not main_hwnd:
        print(json.dumps(out, ensure_ascii=False))
        return 2
    try:
        out["main_rect"] = list(wxui.window_rect(main_hwnd) or [])
    except Exception:  # noqa: BLE001
        out["main_rect"] = []
    if apply:
        try:
            out["raised"] = bool(winio.raise_window(main_hwnd))
        except Exception as exc:  # noqa: BLE001
            out["raise_error"] = str(exc)
        try:
            out["closed"] = winio.close_detached_chat(force=True)
        except Exception as exc:  # noqa: BLE001
            out["close_error"] = str(exc)
        try:
            out["strays_after"] = winio.list_wechat_chat_windows()
        except Exception:  # noqa: BLE001
            out["strays_after"] = []
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
