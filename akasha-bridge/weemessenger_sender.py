"""
WeeMessenger 发送后端
=====================
把 AstrBot（OneBot v11）下发的发送请求转成 WeeMessenger 的 HTTP 指令，
由 WeeMessenger 用 UI Automation 完成真实点击 / 粘贴 / 回车。

链路定位：
    AstrBot ──OneBot API 调用──→ bridge ──HTTP 指令──→ WeeMessenger ──UIA──→ 微信

对外接口与 uia_sender.BaseSender 完全一致（鸭子类型，可直接替换）：
    send_text(contact: str, text: str) -> bool
    send_image(contact: str, image_path: str) -> bool
    send_sticker(contact: str, gif_path: str) -> bool   # 额外能力

出站护栏（所有发送统一经过 _guard）：
    1. 白名单 / 黑名单         —— 只允许回复指定联系人
    2. 全局限频 + 每联系人限频  —— 防止刷屏、降低风控
    3. dry_run                —— 只打日志不发送，首次联调务必打开
    4. 文本长度截断

UI 互斥说明（重要）：
    WeeMessenger 与 bridge 的 UiaSender 都是在操作同一个微信窗口。
    weemessenger_image_mode="uia" 时 bridge 自己会贴图，因此发送前会等待
    ui_handoff_wait 秒，尽量错开 WeeMessenger 的 UI 动作；若要绝对的单一
    UI 驱动，把 weemessenger_image_mode 设为 "sticker"（图片走 WeeMessenger
    表情包通道，bridge 完全不碰 UI，代价是只支持 GIF 且颜色受限）。
"""

import base64
import logging
import os
import threading
import time
from typing import List, Optional

import requests

try:  # 复用同一个 BaseSender 定义，保证接口一致
    from uia_sender import BaseSender, UiaSender
except Exception:  # pragma: no cover - 理论上不会发生
    BaseSender = object  # type: ignore
    UiaSender = None  # type: ignore

log = logging.getLogger("ob11-bridge")

DEFAULT_HTTP_BASE = "http://127.0.0.1:9877"
DEFAULT_MAX_TEXT = 2000


class WeMessengerSender(BaseSender):  # type: ignore[misc]
    """通过 WeeMessenger 的 HTTP 指令通道发送微信消息。"""

    def __init__(
        self,
        http_base: str = DEFAULT_HTTP_BASE,
        timeout: float = 30.0,
        whitelist: Optional[List[str]] = None,
        blacklist: Optional[List[str]] = None,
        min_interval_contact: float = 2.0,
        min_interval_global: float = 1.0,
        settle_seconds: float = 0.0,
        dry_run: bool = False,
        max_text_length: int = DEFAULT_MAX_TEXT,
        image_mode: str = "uia",
        ui_handoff_wait: float = 3.0,
    ) -> None:
        self.http_base = http_base.rstrip("/")
        self.timeout = timeout
        self.whitelist = [w.strip() for w in (whitelist or []) if str(w).strip()]
        self.blacklist = [b.strip() for b in (blacklist or []) if str(b).strip()]
        self.min_interval_contact = max(0.0, min_interval_contact)
        self.min_interval_global = max(0.0, min_interval_global)
        self.settle_seconds = max(0.0, settle_seconds)
        self.dry_run = dry_run
        self.max_text_length = max(1, max_text_length)
        self.image_mode = (image_mode or "uia").lower()
        self.ui_handoff_wait = max(0.0, ui_handoff_wait)

        self._lock = threading.Lock()
        self._last_global = 0.0
        self._last_contact: dict[str, float] = {}
        self._uia = None  # 延迟创建，避免没用上还去碰 UI

        log.info(
            "[WeMessenger] 发送后端已加载 | 指令地址=%s | 图片模式=%s | dry_run=%s | 白名单=%s",
            self.http_base,
            self.image_mode,
            self.dry_run,
            self.whitelist or "（空=不限）",
        )

    # ==================== 对外接口 ====================

    def send_text(self, contact: str, text: str) -> bool:
        if not text:
            return False
        text = text[: self.max_text_length]
        if not self._guard(contact, kind="文本"):
            return False
        ok = self._post({"type": "send_message", "target": contact, "message": text})
        self._settle()
        return ok

    def send_image(self, contact: str, image_path: str) -> bool:
        """发送图片。image_mode=uia 时用剪贴板贴图（支持 png/jpg/gif，画质无损）；
        image_mode=sticker 时走 WeeMessenger 表情包通道（只支持 GIF）。"""
        if not image_path or not os.path.exists(image_path):
            log.error("[WeMessenger] 图片不存在: %s", image_path)
            return False
        if not self._guard(contact, kind="图片"):
            return False

        if self.image_mode == "sticker":
            return self.send_sticker(contact, image_path)

        if self._uia is None:
            if UiaSender is None:
                log.error("[WeMessenger] 无法加载 UiaSender，图片发送不可用")
                return False
            log.info("[WeMessenger] 初始化 UiaSender（图片通道）")
            self._uia = UiaSender()
        time.sleep(self.ui_handoff_wait)  # 让 WeeMessenger 先把手上的 UI 动作做完
        ok = bool(self._uia.send_image(contact, image_path))
        self._settle()
        return ok

    def send_sticker(self, contact: str, gif_path: str) -> bool:
        """通过 WeeMessenger 表情包通道发送 GIF（base64 内联）。"""
        if not gif_path or not os.path.exists(gif_path):
            log.error("[WeMessenger] 表情包不存在: %s", gif_path)
            return False
        if not self._guard(contact, kind="表情包"):
            return False
        try:
            with open(gif_path, "rb") as f:
                data_b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            log.error("[WeMessenger] 读取表情包失败: %s", e)
            return False
        ok = self._post({"type": "send_sticker", "target": contact, "sticker_data": data_b64})
        self._settle()
        return ok

    # ==================== 内部实现 ====================

    def _post(self, payload: dict) -> bool:
        if self.dry_run:
            summary = payload.get("message") or payload.get("type")
            log.warning(
                "[WeMessenger][dry_run] 不实际发送 → target=%s type=%s 内容=%s",
                payload.get("target"),
                payload.get("type"),
                (str(summary)[:80] + "…") if len(str(summary)) > 80 else summary,
            )
            return True
        try:
            r = requests.post(self.http_base + "/", json=payload, timeout=self.timeout)
            if r.status_code == 200:
                log.info(
                    "[WeMessenger] 已投递指令 %s → %s",
                    payload.get("type"),
                    payload.get("target"),
                )
                return True
            log.error(
                "[WeMessenger] 指令被拒绝 HTTP %s: %s",
                r.status_code,
                (r.text or "")[:200],
            )
            return False
        except requests.exceptions.ConnectionError:
            log.error(
                "[WeMessenger] 无法连接指令服务 %s —— 小信使没在运行？(config.yaml 里 http.enabled 需要为 true)",
                self.http_base,
            )
            return False
        except Exception as e:
            log.error("[WeMessenger] 指令发送异常: %s", e)
            return False

    def _guard(self, contact: str, kind: str) -> bool:
        """白名单 / 黑名单 / 限频 三道闸门。"""
        name = (contact or "").strip()
        if not name:
            log.error("[WeMessenger] 目标为空，已拦截（%s）", kind)
            return False

        if self.blacklist and name in self.blacklist:
            log.warning("[WeMessenger] 目标 %s 在黑名单，已拦截（%s）", name, kind)
            return False

        if self.whitelist and name not in self.whitelist:
            log.warning(
                "[WeMessenger] 目标 %s 不在白名单 %s，已拦截（%s）",
                name,
                self.whitelist,
                kind,
            )
            return False

        with self._lock:
            now = time.time()
            wait = 0.0
            since_global = now - self._last_global
            if since_global < self.min_interval_global:
                wait = max(wait, self.min_interval_global - since_global)
            since_contact = now - self._last_contact.get(name, 0.0)
            if since_contact < self.min_interval_contact:
                wait = max(wait, self.min_interval_contact - since_contact)
            if wait > 0:
                log.info("[WeMessenger] 限频等待 %.1fs（%s → %s）", wait, kind, name)
                time.sleep(wait)
            stamp = time.time()
            self._last_global = stamp
            self._last_contact[name] = stamp
        return True

    def _settle(self) -> None:
        """给 WeeMessenger 的 UI 动作留出时间（0 表示不等待）。"""
        if self.settle_seconds > 0:
            time.sleep(self.settle_seconds)

    # ==================== 诊断 ====================

    def health(self) -> dict:
        """探测 WeeMessenger 指令服务是否在线（用于自检脚本 / 面板）。"""
        url = self.http_base + "/health"
        try:
            r = requests.get(url, timeout=5)
            return {"url": url, "ok": r.status_code == 200, "status": r.status_code, "body": (r.text or "")[:120]}
        except Exception as e:
            return {"url": url, "ok": False, "error": str(e)}
