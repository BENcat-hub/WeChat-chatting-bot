"""
消息发送器模块：发送后端工厂。
================================
通过 config.json 的 sender_backend 字段选择发送通道：

  - "weemessenger"：推荐。
        文本 / 表情包 → WeeMessenger 的 HTTP 指令通道（由 WeeMessenger 用 UIA 完成真实点击）
        图片         → 默认由 bridge 用剪贴板贴图发送（见 weemessenger_image_mode）
  - "uia"：Akasha 原方案，全部由 bridge 自己的 UiaSender 完成（纯键盘 + 剪贴板模拟）

两种后端对外接口一致，切换只需改配置 + 重启 bridge。
"""

import logging

import config

log = logging.getLogger("ob11-bridge")


def create_sender():
    """按配置创建发送后端。"""
    backend = (getattr(config, "SENDER_BACKEND", "weemessenger") or "weemessenger").lower()

    if backend == "uia":
        from uia_sender import UiaSender

        log.info("发送后端：UIA（bridge 直接操作微信窗口）")
        return UiaSender()

    if backend == "wxui":
        from wxui_sender import WxuiSender

        log.info("发送后端：wxui（Windows-MCP 点击 + WeFlow 回读验证）")
        return WxuiSender(
            script=config.WXUI_SCRIPT,
            python_exe=config.WXUI_PYTHON,
            wxui_timeout=config.WXUI_TIMEOUT,
            retry_queue_path=config.WXUI_RETRY_QUEUE,
            queue_interval=config.WXUI_QUEUE_INTERVAL,
            queue_max_attempts=config.WXUI_QUEUE_MAX_ATTEMPTS,
        queue_enabled=config.WXUI_QUEUE_ENABLED,
            group_gather_seconds=config.WXUI_GROUP_GATHER_SECONDS,
            warm_worker=config.WXUI_WARM_WORKER,
            whitelist=config.SEND_WHITELIST,
            blacklist=config.SEND_BLACKLIST,
            min_interval_contact=config.SEND_MIN_INTERVAL_CONTACT,
            min_interval_global=config.SEND_MIN_INTERVAL_GLOBAL,
            dry_run=config.SEND_DRY_RUN,
            max_text_length=config.SEND_MAX_TEXT_LENGTH,
            image_mode=config.WEEMESSENGER_IMAGE_MODE,
            ui_handoff_wait=config.UI_HANDOFF_WAIT,
        )

    if backend in ("weemessenger", "weemessenger+uia", "hybrid"):
        from weemessenger_sender import WeMessengerSender

        log.info("发送后端：WeeMessenger 指令通道")
        return WeMessengerSender(
            http_base=config.WEEMESSENGER_HTTP_BASE,
            timeout=config.WEEMESSENGER_TIMEOUT,
            whitelist=config.SEND_WHITELIST,
            blacklist=config.SEND_BLACKLIST,
            min_interval_contact=config.SEND_MIN_INTERVAL_CONTACT,
            min_interval_global=config.SEND_MIN_INTERVAL_GLOBAL,
            dry_run=config.SEND_DRY_RUN,
            max_text_length=config.SEND_MAX_TEXT_LENGTH,
            image_mode=config.WEEMESSENGER_IMAGE_MODE,
            ui_handoff_wait=config.UI_HANDOFF_WAIT,
        )

    raise ValueError(
        f"未知 sender_backend: {backend}（可选值：weemessenger / uia）"
    )

