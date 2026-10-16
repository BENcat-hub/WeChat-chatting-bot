"""
OneBot v11 协议处理模块。

包括：
- make_message_event() — 构造 OneBot 消息事件 JSON
- push_event() — 通过 WebSocket 推送事件给 AstrBot
- _handle_ob_api() — 处理 AstrBot 发来的 API 请求（send_msg 等）
- _extract_text() — 从 OneBot message 段提取纯文本
"""

import asyncio
import base64
import json
import os
import tempfile
import threading
import time
import logging

import requests

import state
import config

log = logging.getLogger("ob11-bridge")

# 分段回复：AstrBot 把一条回复按标点切成多段、整条链一次性发过来，我们逐段发到微信。
# 每段都是一次 UI 自动化（约 7–15 秒，期间占用鼠标键盘），所以段数要封顶。
# 短段（「哈？」「在呢」）是模型自己换行写出来的，保留原样，不合并。
SEGMENT_MAX_SENDS = 4   # 一次回复最多拆成几条微信消息


def _merge_text_segments(message: list) -> list:
    """只做一件事：文本段超过 SEGMENT_MAX_SENDS 时，把多出来的并进最后一段。"""
    text_idx = [
        i for i, seg in enumerate(message)
        if isinstance(seg, dict) and seg.get("type") == "text"
    ]
    if len(text_idx) <= SEGMENT_MAX_SENDS:
        return message

    pieces = [str(message[i].get("data", {}).get("text", "")) for i in text_idx]
    merged = pieces[:SEGMENT_MAX_SENDS - 1] + ["".join(pieces[SEGMENT_MAX_SENDS - 1:])]

    out, mi = [], 0
    for i, seg in enumerate(message):
        if i in text_idx:
            if mi < len(merged):
                data = dict(seg.get("data") or {})
                data["text"] = merged[mi]
                out.append({**seg, "data": data})
                mi += 1
            # 多余的段：内容已并进上一段，这里丢掉
        else:
            out.append(seg)
    log.info(f"[OB11] 分段合并: {len(pieces)} 段 -> {len(merged)} 段（上限 {SEGMENT_MAX_SENDS}）")
    return out


async def _handle_ob_api(data: dict):
    """处理 AstrBot 发来的 API 请求。"""
    action = data.get("action", "")
    params = data.get("params", {})
    echo = data.get("echo", "")
    log.info(f"[OB11] API: {action} echo={echo}")

    # 先回响应（必须在处理消息前回，否则 AstrBot 超时）
    resp_sent = False
    resp_data = {"status": "ok", "retcode": 0, "data": {}}
    if echo:
        resp_data["echo"] = echo
    # 如果 WS 暂时断连，等一会重试
    for retry in range(10):
        try:
            if state._ob_ws:
                await state._ob_ws.send(json.dumps(resp_data, ensure_ascii=False))
                resp_sent = True
                log.info(f"[OB11] 已回响应: {action}")
                break
            if retry < 9:
                await asyncio.sleep(0.5)
        except Exception as e:
            log.warning(f"[OB11] 回响应失败 (重试 {retry}/10): {e}")
            if retry < 9:
                await asyncio.sleep(0.5)
    if not resp_sent:
        log.warning(f"[OB11] 无法回响应（WS 未连接），消息仍尝试本地处理: {action}")

    if action in ("send_msg", "send_private_msg", "send_group_msg"):
        is_group = action == "send_group_msg"
        target_id = params.get("group_id" if is_group else "user_id", 0)
        message = params.get("message", [])
        if isinstance(message, list):
            message = _merge_text_segments(message)
        contact = state._ob_id_to_contact.get(target_id, str(target_id))

        # 逐段处理：文字和图片分别发送
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {})

            if seg_type == "text":
                text = seg_data.get("text", "")
                if text:
                    ok = await asyncio.to_thread(state.sender_instance.send_text, contact, text)
                    if ok:
                        log.info(f"[OB11] 文字已发送至 {contact}: {text[:50]}")
                    else:
                        log.warning(
                            f"[OB11] ⚠ 文字未验证送达（已交重发队列，稍后自动补发）{contact}: {text[:50]}"
                        )

            elif seg_type == "image":
                file_val = seg_data.get("file", "")
                if not file_val:
                    continue

                img_path = None

                # AstrBot 通过 aiocqhttp 发图片时用 base64:// 格式
                if file_val.startswith("base64://"):
                    try:
                        # 解码 + 写文件在线程池执行，避免大图卡死事件循环
                        b64_data = file_val[9:]
                        img_path = await asyncio.to_thread(_decode_base64_image, b64_data)
                        if img_path:
                            log.info(f"[OB11] 图片已解码: {os.path.basename(img_path)}")
                    except Exception as e:
                        log.warning(f"[OB11] base64 图片解码失败: {e}")
                else:
                    # 文件名模式：先认绝对路径（插件常用），再到附件目录找
                    raw = file_val[7:] if file_val.startswith("file://") else file_val
                    if os.path.isabs(raw) and os.path.exists(raw):
                        img_path = raw
                    elif config.ASTRBOT_ATTACHMENTS:
                        candidates = [
                            os.path.join(config.ASTRBOT_ATTACHMENTS, file_val),
                            os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images", file_val),
                        ]
                        for p in candidates:
                            if os.path.exists(p):
                                img_path = p
                                break
                        if not img_path:
                            log.warning(f"[OB11] 图片文件未找到: {file_val}")

                if img_path:
                    try:
                        # 使用线程池执行同步的 UIA 发送，避免阻塞事件循环
                        ok = await asyncio.to_thread(state.sender_instance.send_image, contact, img_path)
                        if ok:
                            log.info(f"[OB11] 图片已发送至 {contact}")
                        else:
                            log.warning(f"[OB11] ⚠ 图片未验证送达（已交重发队列，稍后自动补发）{contact}")
                    finally:
                        # 临时文件用完删除
                        if img_path and "tmp" in img_path:
                            try:
                                os.unlink(img_path)
                            except Exception:
                                pass

            elif seg_type == "face":
                await asyncio.to_thread(state.sender_instance.send_text, contact, "[表情]")
                log.info(f"[OB11] 表情已发送至 {contact}")

            # 其他类型（record, video 等）忽略

    else:
        log.debug(f"[OB11] 未处理 API: {action}")

    # 注意：API 响应已在函数开头统一发送，此处不再重复


def _extract_text(message: list) -> str:
    """从 OneBot message 段中提取可发送的文本。"""
    text_parts = []
    for seg in message:
        if isinstance(seg, dict):
            t = seg.get("type", "")
            d = seg.get("data", {})
            if t == "text":
                text_parts.append(d.get("text", ""))
            elif t == "image":
                text_parts.append("[图片]")
            elif t == "face":
                text_parts.append("[表情]")
            elif t == "record":
                text_parts.append("[语音]")
            elif t == "video":
                text_parts.append("[视频]")
            elif t == "reply":
                if d.get("text"):
                    text_parts.append(f'"{d["text"]}"')
            elif t == "at":
                text_parts.append(f"@{d.get('qq', d.get('name', ''))}")
            else:
                # 其他未知类型也尝试提取文本
                text_parts.append(d.get("text", ""))
    return "".join(text_parts).strip()


# ============ OneBot 协议处理 ============


ONEBOT_INT32_MAX = 2147483647


def onebot_message_id(rawid: str) -> int:
    """把 WeFlow 的 rawid（19 位十进制串）压进 OneBot v11 的 int32 message_id。

    同一个 rawid 永远映射到同一个值，所以 AstrBot/插件能按 message_id 正确去重；
    不同 rawid 只有相差 2147483647 的整数倍时才会撞车（实际不可能）。
    **不能省略**：没有 message_id 时，AstrBot 的 aiocqhttp 会把 message_id 写成字符串
    "None"，插件的防抖就会用同一个 id 键把 60 秒内同一人的后续消息全部当成重复丢掉。
    """
    digits = "".join(ch for ch in str(rawid or "") if ch.isdigit())
    if digits:
        return int(digits) % ONEBOT_INT32_MAX
    return int(time.time() * 1000) % ONEBOT_INT32_MAX


def make_message_event(message_type: str, user_id: int, message: list,
                       group_id: int = 0, group_name: str = "",
                       nickname: str = "", message_id: int | None = None) -> dict:
    """构造 OneBot v11 消息事件"""
    event = {
        "time": int(time.time()),
        "self_id": state._self_id_int,
        "post_type": "message",
    }
    if message_id is not None:
        event["message_id"] = message_id
    if message_type == "group":
        event["message_type"] = "group"
        event["group_id"] = group_id
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
        event["group_name"] = group_name or str(group_id)
    else:
        event["message_type"] = "private"
        event["user_id"] = user_id
        event["message"] = message
        event["raw_message"] = "".join(
            seg.get("data", {}).get("text", "") for seg in message
            if seg.get("type") == "text"
        )
        event["sender"] = {"user_id": user_id, "nickname": nickname or str(user_id)}
    return event


# 离线补发：AstrBot 重启或被网络断开时，bridge 仍在收微信消息。以前这些事件
# 直接丢掉，人物记忆里就留下一个空洞（用户说了话，Bot 完全不知道）。
# 这里缓存一小段窗口内的事件，重连后按序补发。
_OFFLINE_QUEUE: list[tuple[float, dict]] = []
_OFFLINE_QUEUE_LOCK = threading.Lock()
_OFFLINE_QUEUE_MAX = 30          # 最多缓存 30 条
_OFFLINE_QUEUE_TTL = 600.0       # 超过 10 分钟不再补发（太旧的话补发反而突兀）


def queue_offline_event(event: dict) -> None:
    """AstrBot 不在线时缓存事件，等待重连补发。"""

    now = time.time()
    with _OFFLINE_QUEUE_LOCK:
        _OFFLINE_QUEUE[:] = [
            (ts, item) for ts, item in _OFFLINE_QUEUE if now - ts <= _OFFLINE_QUEUE_TTL
        ]
        _OFFLINE_QUEUE.append((now, event))
        if len(_OFFLINE_QUEUE) > _OFFLINE_QUEUE_MAX:
            dropped = len(_OFFLINE_QUEUE) - _OFFLINE_QUEUE_MAX
            del _OFFLINE_QUEUE[:dropped]
            log.warning(f"[OB11] 离线队列已满，丢弃最旧的 {dropped} 条事件")
        size = len(_OFFLINE_QUEUE)
    log.warning(f"⚠️ AstrBot 不在线，事件已缓存待补发（队列 {size} 条）")


async def _send_event(ws, event: dict) -> bool:
    try:
        await ws.send(json.dumps(event, ensure_ascii=False))
        return True
    except Exception as e:
        log.warning(f"[OB11] 推送事件失败: {e}")
        return False


def push_event(event: dict) -> bool:
    """通过 WebSocket 客户端连接向 AstrBot 推送事件（失败则入离线队列）。"""
    if not state._ob_ws or not state._ob_ws_loop:
        queue_offline_event(event)
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(
            _send_event(state._ob_ws, event),
            state._ob_ws_loop,
        )
        if future.result(timeout=5):
            return True
        queue_offline_event(event)
        return False
    except Exception as e:
        log.warning(f"[OB11] 推送事件失败: {e}")
        queue_offline_event(event)
        return False


async def flush_offline_events(ws=None) -> int:
    """AstrBot 重连后补发离线期间缓存的事件。

    必须在 ``ob_client`` 自己的事件循环里 await，不能用 push_event 的
    run_coroutine_threadsafe 路径（同循环内阻塞等待会死锁）。
    """

    target = ws if ws is not None else state._ob_ws
    now = time.time()
    with _OFFLINE_QUEUE_LOCK:
        pending = list(_OFFLINE_QUEUE)
        _OFFLINE_QUEUE.clear()
    if not pending or target is None:
        if pending and target is None:
            for _ts, item in pending:
                queue_offline_event(item)
        return 0
    sent = 0
    expired = 0
    for index, (ts, item) in enumerate(pending):
        if now - ts > _OFFLINE_QUEUE_TTL:
            expired += 1
            continue
        if await _send_event(target, item):
            sent += 1
            continue
        # 发送失败说明连接又断了：这条和剩下的都放回队列，等下次重连
        for _ts, rest in pending[index:]:
            queue_offline_event(rest)
        break
    if expired:
        log.warning(f"[OB11] 离线队列有 {expired} 条超过 {int(_OFFLINE_QUEUE_TTL)}s，未补发")
    if sent:
        log.info(f"[OB11] ✅ 已补发离线期间缓存的 {sent} 条事件")
    return sent


def _guess_image_suffix(data: bytes) -> str:
    """按魔数判断图片真实格式，用于给临时文件取正确后缀。

    历史上这里硬编码 ".png"，导致 GIF 动图被写成 xxx.png：
    AstrBot 侧是无损透传（media_utils 不重编码），但下游按后缀/格式分支时
    会把动图当静态图处理（见 docs/PLUGINS.zh-CN.md 的动图问题）。
    """
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _decode_base64_image(b64_data: str) -> str | None:
    """在线程池中执行：解码 base64 图片并保存为临时文件（后缀按魔数嗅探）。"""
    import tempfile
    img_data = base64.b64decode(b64_data)
    suffix = _guess_image_suffix(img_data)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(img_data)
    tmp.close()
    return tmp.name
