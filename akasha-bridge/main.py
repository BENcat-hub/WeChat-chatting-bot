"""
入口与生命周期管理模块。

负责桥接的启动、停止、主循环重连逻辑，以及命令行入口。
"""

import json
import logging
import os
import sys
import threading
import time

import requests

import state
import config
from senders import create_sender
from ob_client import _run_ob_client
from bridge_core import WeFlowBridge
from web_panel import WebHandler, PAGE
from http.server import HTTPServer

log = logging.getLogger("ob11-bridge")


# ============ 启动 / 停止 ============


def _start_bridge():
    with state.run_lock:
        if state.running:
            return
        state.running = True
    state.paused.clear()
    state.sender_instance = create_sender()

    if not state.ob_client_started:
        t = threading.Thread(target=_run_ob_client, daemon=True, name="ob11-client")
        t.start()
        state.ob_client_started = True

    state.bridge_thread = threading.Thread(target=_bridge_loop, daemon=True, name="bridge")
    state.bridge_thread.start()
    log.info("[Web] 已启动")


def _stop_bridge():
    with state.run_lock:
        state.running = False

    # 切断 SSE 长连接，让 _bridge_loop 的 listen_sse() 从阻塞中退出
    with state.bridge_lock:
        if state.bridge_instance and state.bridge_instance._sse_session:
            try:
                state.bridge_instance._sse_session.close()
                log.info("[Web] SSE 连接已断开")
            except Exception as e:
                log.warning(f"[Web] 断开 SSE 异常: {e}")

    # 关闭 WebSocket 连接，让 _ob_client_main 从 async for 中退出
    _ws = state._ob_ws
    _loop = state._ob_ws_loop
    if _ws:
        try:
            if _loop and _loop.is_running():
                import asyncio
                asyncio.run_coroutine_threadsafe(
                    _ws.close(), _loop
                )
                log.info("[Web] WebSocket 连接已关闭")
        except Exception as e:
            log.warning(f"[Web] 关闭 WebSocket 异常: {e}")

    state._ob_ws_ready.clear()

    # 重置启动标记，让下次 start 能重新拉起 WebSocket 客户端线程
    state.ob_client_started = False
    state._ob_ws_loop = None

    log.info("[Web] 已停止")


def _sleep_while_running(seconds: int):
    """可被 /stop 打断的等待。"""
    for _ in range(int(seconds)):
        if not state.running:
            return
        time.sleep(1)


def _sender_label() -> str:
    """给日志用的发送后端名称（不依赖具体类的属性是否存在）。"""
    s = state.sender_instance
    if s is None:
        return "未初始化"
    return (getattr(s, "label", None)
            or getattr(s, "name", None)
            or s.__class__.__name__)


def _bridge_loop():
    import ctypes
    ctypes.windll.ole32.CoInitialize(None)

    log.info(f"Bridge | WeFlow: {config.WE_FLOW_BASE_URL} | OB11: {config.ASTRBOT_OB_URL} | 发送: {_sender_label()}")

    bridge = WeFlowBridge(state.sender_instance)
    with state.bridge_lock:
        state.bridge_instance = bridge

    # 设计取舍：WeFlow 侧没就绪（没配 token / token 无效 / WeFlow 没开）时**不退出整个桥接**，
    # 而是每 30 秒重试一次。这样 AstrBot 的连接与"主动发送"通道保持可用，
    # 修好 token 或启动 WeFlow 后无需重启 bridge 就能自动进入 SSE 接收。
    while state.running:
        if not config.ACCESS_TOKEN:
            log.error("❌ 未配置 access_token：WeFlow 消息接收暂停（发送通道仍可用，30s 后重试）")
            _sleep_while_running(30)
            continue

        # 自检用 /api/v1/sessions（不需要 talker）。
        # 注意：/api/v1/messages 的 talker 是必填参数，缺参会返回 HTTP 400，
        # 早期版本用 messages 做自检会导致永远进不去 SSE 循环。
        try:
            r = requests.get(
                f"{config.WE_FLOW_BASE_URL}/api/v1/sessions?limit=1&access_token={config.ACCESS_TOKEN}",
                timeout=5,
            )
            if r.status_code == 200:
                log.info("✅ WeFlow API 正常")
            elif r.status_code == 401:
                log.error("❌ Access Token 无效：WeFlow 消息接收暂停（30s 后重试）")
                _sleep_while_running(30)
                continue
            else:
                # 非 401 的异常状态（400/404/500）不阻断：SSE 是长连接，直接试更有意义
                log.warning(f"⚠️ WeFlow 自检返回 HTTP {r.status_code}，仍尝试连接 SSE")
        except requests.exceptions.ConnectionError:
            log.error("❌ 无法连接 WeFlow（30s 后重试）")
            _sleep_while_running(30)
            continue
        except Exception as e:
            log.error(f"❌ WeFlow 检查异常: {e}（30s 后重试）")
            _sleep_while_running(30)
            continue

        try:
            bridge.listen_sse()
        except Exception as e:
            log.error(f"SSE: {e}")
        if not state.running:
            break
        log.warning("⚠️ SSE 断开，10s 后重连")
        _sleep_while_running(10)

    with state.bridge_lock:
        state.bridge_instance = None


def start_web():
    server = HTTPServer(("127.0.0.1", config.WEB_PORT), WebHandler)
    log.info(f"Web: http://127.0.0.1:{config.WEB_PORT}")
    server.serve_forever()


# ============ 入口 ============

if __name__ == "__main__":
    # 从 config 初始化 state 中需要计算的值
    state._self_id_int = state._self_id_of(config.BOT_WXID or "wechat_bot")
    state.group_reply_mode = config.GROUP_REPLY_MODE

    # 恢复 id ↔ 联系人名 映射（重启后旧会话仍能回复），再用 WeFlow 会话列表补齐
    restored = state.load_ob_id_map()
    log.info(f"恢复 id ↔ 联系人映射：{restored} 条")
    try:
        from bridge_core import prefill_id_map
        prefill_id_map()
    except Exception as exc:
        log.warning(f"⚠️ 预填 id 映射异常: {exc}")

    # 初始化运行时可变的图片描述配置
    state.image_caption_provider = config.IMAGE_CAPTION_PROVIDER
    state.image_caption_model = config.IMAGE_CAPTION_MODEL
    state.image_caption_api_key = config.IMAGE_CAPTION_API_KEY
    state.image_caption_api_base = config.IMAGE_CAPTION_API_BASE
    state.image_caption_prompt = config.IMAGE_CAPTION_PROMPT
    state.image_forward_mode = config.IMAGE_FORWARD_MODE
    state.wxui_group_gather_seconds = config.WXUI_GROUP_GATHER_SECONDS
    state.wxui_warm_worker = config.WXUI_WARM_WORKER
    log.info(f"图片转发策略: {config.IMAGE_FORWARD_MODE}（direct=原图直通主模型 / caption=只转发描述 / both=原图+描述）")
    state.ollama_base_url = config.OLLAMA_BASE_URL
    state.ollama_timeout = config.OLLAMA_TIMEOUT

    PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge.pid")

    def pid_exists(pid):
        try:
            import ctypes
            from ctypes import wintypes
            h = ctypes.windll.kernel32.OpenProcess(0x0400, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return True

    def port_listening(port):
        """web_port 上真的有人在监听吗？

        OpenProcess 对刚被杀掉、句柄还没全部关闭的 PID 会返回成功（PID 也可能被系统复用
        给别的进程），所以"进程还在"不足以证明上一个 bridge 还活着。权威信号是端口：
        活着的 bridge 一定在监听 web_port（8766）。
        """
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", int(port))) == 0

    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if pid_exists(old_pid) and port_listening(config.WEB_PORT):
                log.error("⚠️ bridge.pid 已存在（pid=%s 仍在监听 %s）", old_pid,
                          config.WEB_PORT)
                sys.exit(1)
            log.warning("🧹 清理过期的 bridge.pid（pid=%s 已不在监听 %s）", old_pid,
                        config.WEB_PORT)
            os.remove(PID_FILE)
        except (ValueError, OSError):
            os.remove(PID_FILE)

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        log.info("=" * 50)
        log.info(" WeFlow 微信桥接 (OneBot v11)")
        log.info("=" * 50)
        log.info("Bridge 版本: 2026-06-03 OB11")
        _start_bridge()
        start_web()
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
