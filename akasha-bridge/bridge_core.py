"""
桥接核心模块：WeFlowBridge 类。

职责：
1. 连接 WeFlow SSE 推送，接收微信消息
2. 消息缓冲合并（BUFFER_SECONDS）
3. 构造 OneBot 事件，推送给 AstrBot
4. 多层消息去重（rawid、内容、自回复）
"""

import json
import html
import logging
import os
import queue
import re
import threading
import time
from collections import defaultdict
from datetime import datetime

import pathlib

import requests

import state
import config
from ob_protocol import push_event, make_message_event, onebot_message_id
from image_forward import build_image_payload, normalize_forward_mode, text_to_segments

log = logging.getLogger("ob11-bridge")


def _forward_mode() -> str:
    """当前图片转发策略：面板/state 优先，其次 config，非法值回退 direct。"""
    return normalize_forward_mode(getattr(state, "image_forward_mode", None) or config.IMAGE_FORWARD_MODE)


def _image_payload(image_path, mode: str) -> str:
    """按模式生成注入文本。

    direct 模式下**不调用**描述模型——图片本体已经直通 AstrBot，主模型自己看，
    描述只会在 caption / both 模式下生成。
    """
    if not image_path:
        return build_image_payload(None, None, mode)
    if normalize_forward_mode(mode) == "direct":
        return build_image_payload(image_path, None, mode)
    caption = caption_image_via_ollama(image_path)
    return build_image_payload(image_path, caption, mode)



def prefill_id_map() -> int:
    """启动时枚举 WeFlow 会话，把 OneBot id ↔ 联系人名 映射补齐。

    只依赖 WeFlow HTTP API；任何失败都只记日志，不影响 bridge 启动。
    联系人名取 displayName（与 SSE 的 sourceName 同源），私聊/群聊分别落在 user/group 段位。
    """
    url = f"{config.WE_FLOW_BASE_URL}/api/v1/sessions"
    try:
        resp = requests.get(url, params={"access_token": config.ACCESS_TOKEN}, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        log.warning(f"⚠️ 预填 id 映射失败（WeFlow 未就绪？）: {exc}")
        return 0
    sessions = payload.get("sessions") if isinstance(payload, dict) else payload
    if not isinstance(sessions, list):
        return 0
    written = 0
    for item in sessions:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        name = str(item.get("displayName") or "").strip()
        kind = str(item.get("sessionType") or "").strip().lower()
        if not username or not name:
            continue
        if kind == "group" or username.endswith("@chatroom"):
            ob_id = state._group_id_int(username)
        elif kind == "private":
            ob_id = state._wxid_to_int(username)
        else:
            continue
        if state._ob_id_to_contact.get(ob_id) != name:
            written += 1
        state.remember_ob_contact(ob_id, name)
    log.info(f"预填 id ↔ 联系人映射：扫描 {len(sessions)} 个会话，新增/更新 {written} 条")
    return written


# ============ 桥接核心 ============


def _is_voice_text(content: str) -> bool:
    """判断消息正文是不是微信语音的占位文本。

    实测 WeFlow 对 localType=34 返回的 content 是 "[语音消息]"（不是 "[语音]"），
    不同版本/来源还可能给 "[语音]"、"[Voice]"、"[voice]"，统一在这里判定。
    """
    text = (content or "").strip()
    if not text:
        return False
    if text in ("[语音]", "[语音消息]", "[Voice]", "[voice]", "[Voice Message]"):
        return True
    return text.startswith("[语音")


# 转发「聊天记录」卡片的占位文本（WeFlow 推送 content 里就是这几个字）
_RECORD_PLACEHOLDERS = ("[聊天记录]", "[聊天记录消息]", "[转发聊天记录]", "[聊天记录 ]")


def _is_chat_record_type(local_type) -> bool:
    """判断 WeFlow 的 localType 是不是「聊天记录」卡片。

    实测这类 appmsg 卡片的 localType 是 81604378673（高 32 位为 19），
    所以按位判断而不是只比 19。
    """
    try:
        lt = int(local_type or 0)
    except (TypeError, ValueError):
        return False
    if lt in (19, 81604378673):
        return True
    return (lt >> 32) == 19


def _parse_chat_record_xml(xml: str) -> dict | None:
    """把微信「聊天记录」卡片的 appmsg XML 解析成 {title, count, items}。

    XML 里真正有用的是 <title> 与 <datalist> 下每一条 <dataitem> 的
    <datadesc>（正文，图片/表情是 [图片] 之类占位）和 <sourcename>（说话人）。
    parsedContent 字段实测是空串，所以只能自己解析。
    """
    text = xml or ""
    if "<datalist" not in text and "<recordinfo" not in text:
        return None
    title_match = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", text, re.S) or \
        re.search(r"<title>(.*?)</title>", text, re.S)
    title = (title_match.group(1).strip() if title_match else "").strip()
    items = []
    for block_match in re.finditer(r"<dataitem\b[^>]*>(.*?)</dataitem>", text, re.S):
        block = block_match.group(1)
        desc = re.search(r"<datadesc>(.*?)</datadesc>", block, re.S)
        who = re.search(r"<sourcename>(.*?)</sourcename>", block, re.S)
        if not desc:
            continue
        items.append((
            (who.group(1).strip() if who else ""),
            desc.group(1).strip(),
        ))
    if not title and not items:
        return None
    count_match = re.search(r'<datalist[^>]*count="(\d+)"', text)
    try:
        count = int(count_match.group(1)) if count_match else len(items)
    except ValueError:
        count = len(items)
    return {"title": title, "count": count, "items": items}


def _format_chat_record(record: dict, max_items: int = 25, max_chars: int = 1200) -> str:
    """把解析结果压成给大模型看的可读文本。"""
    title = (record.get("title") or "聊天记录").strip()
    items = record.get("items") or []
    count = record.get("count") or len(items)
    lines = [f"[转发聊天记录]「{title}」共 {count} 条，前 {min(len(items), max_items)} 条："]
    for who, desc in items[:max_items]:
        desc = re.sub(r"\s+", " ", html.unescape(str(desc or ""))).strip()
        if not desc:
            continue
        lines.append(f"{who or '某人'}：{desc}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "…"
    return text


# 菜单回执别名：插件 selection.py 认这些词（取消/翻页）
_MENU_REPLY_ALIASES = {"取消", "取消选择", "上一页", "上页", "下一页", "下页"}


def _is_menu_reply(text: str) -> bool:
    """判断消息是不是“菜单回执”（纯序号或翻页/取消词）。

    插件 astrbot_plugin_matsuko_cover 的菜单用
    `re.fullmatch(r"[0-9]{1,6}", event.message_str)` 匹配整句，群消息若被加上
    “某某在群里说：”前缀就永远匹配不上，导致回序号选不了歌。
    """
    t = (text or "").strip()
    if not t:
        return False
    if re.fullmatch(r"[0-9]{1,6}", t):
        return True
    return t in _MENU_REPLY_ALIASES


class WeFlowBridge:
    """WeFlow ↔ AstrBot 桥接器（OneBot v11 版）。"""

    def __init__(self, sender):
        self.sender = sender
        self.processed_ids = set()
        self.start_timestamp = int(time.time())
        self.pending_buffers = {}
        self.buffer_lock = threading.Lock()
        self.chat_histories = defaultdict(list)
        self.contact_map = {}
        self._sse_session = None
        self._recent_seen = {}
        self._sent_recently = {}
        self._sse_event_keys = {}
        self._pending_image = {}  # talkerId → {"caption": None|str, "event": threading.Event()}
        self._pending_mention_images = {}  # session_id → {"data": data, "time": timestamp} 先图后文暂存

    def should_ignore(self, data):
        content = data.get("content", "")
        msg_type = data.get("type", 0) or data.get("msgType", 0)
        if data.get("sourceName", "") in config.BOT_NICKNAMES:
            return True
        if config.BOT_WXID and data.get("talkerId", "") == config.BOT_WXID:
            return True
        # 34=语音 / "[语音]" 不再丢弃：bridge 负责把语音导出成 wav、调 ASR 转成
        # 文字后以纯文字推给 AstrBot。语音消息的 content 可能是 "[语音]" 也可能是空，
        # 所以必须在空内容过滤之前放行。
        if msg_type == 34 or _is_voice_text(content):
            return False
        if not content or content.strip() == "":
            return True
        return False

    def _is_mentioned(self, data):
        """检测群消息是否 @ 了机器人。

        WeFlow SSE 推送不含 @ 结构字段，只能从 content 文本检测。
        """
        content = data.get("content", "")
        if not content:
            return False

        for nick in config.BOT_NICKNAMES:
            # 标准 @昵称（ASCII @）
            if f"@{nick}" in content:
                return True
            # 全角 @昵称（部分微信版本可能用全角符号）
            if f"＠{nick}" in content:
                return True

        # 日志：content 以 @ 开头但未匹配到任何昵称（便于排查）
        if (content.startswith("@") or content.startswith("＠")) and len(content) > 1:
            log.debug(f"⚠️ content 以@开头但未匹配昵称: content={content[:40]!r} nicknames={config.BOT_NICKNAMES}")

        return False

    def add_to_buffer(self, data):
        """将消息加入缓冲区，等待合并后统一推送给 AstrBot。"""
        content = data.get("content", "")
        source_name = data.get("sourceName", "") or data.get("talkerName", "") or "未知"

        # 先判断群聊/私聊（图片/表情分支也需要用到）
        session_id_data = data.get("sessionId", "") or source_name
        group_name_raw = data.get("groupName", "")
        is_group = (data.get("sessionType", "") == "group") or bool(group_name_raw) or "@chatroom" in session_id_data

        if content in _RECORD_PLACEHOLDERS or _is_chat_record_type(
                data.get("type", 0) or data.get("msgType", 0)):
            # 转发的「聊天记录」卡片：把 XML 里的条目解析成可读文本再往下走，
            # 否则 AstrBot 只能看到字面量「[聊天记录]」。
            resolved_record = self._resolve_chat_record(data)
            if resolved_record:
                data = dict(data)
                data["content"] = resolved_record
                content = resolved_record

        if content == "[图片]":
            # 图片消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存图片，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"📸 暂存图片，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_image_message,
                           args=(data,), daemon=True).start()
            return

        _msg_type_raw = data.get("type", 0) or data.get("msgType", 0)
        if _is_voice_text(content) or _msg_type_raw == 34:
            # 群聊 mention 模式下语音里没有 @ 字样，无法判断是否在叫机器人，直接跳过。
            if is_group and state.group_reply_mode == "mention":
                log.info("⏭️ 群语音跳过（mention 模式无法检测 @）")
                return
            threading.Thread(target=self.process_voice_message,
                             args=(data,), daemon=True).start()
            return

        if content in ("[动画表情]", "[表情]"):
            # 表情包消息（mention 模式下需 @ 才处理）
            if is_group and state.group_reply_mode == "mention" and not self._is_mentioned(data):
                # 先图后文：暂存表情，等后续同人发 @ 文字时合并
                self._pending_mention_images[session_id_data] = {"data": data, "time": time.time()}
                log.info(f"😀 暂存表情，等待关联 @ 文字 (session={session_id_data})")
                return
            threading.Thread(target=self.process_emoji_message,
                           args=(data,), daemon=True).start()
            return

        now = time.time()
        if content and content in self._sent_recently and now - self._sent_recently[content] < 120:
            log.info(f"⏭️ 自回复去重跳过: {content[:30]}")
            return

        sender_in_group = data.get("senderName", "") or data.get("sender", "") or data.get("sourceName", "")

        if is_group:
            if state.group_reply_mode == "mention" and not self._is_mentioned(data):
                log.debug(f"⏭️ mention 模式跳过（未检测到 @）: data keys={list(data.keys())} nickname={config.BOT_NICKNAMES} content={content[:40]}")
                return
            group_raw = group_name_raw or source_name
            base_name = re.sub(r'\s*\(\d+\)\s*$', '', group_raw).strip()
            contact = base_name
        else:
            contact = source_name

        if is_group and state.group_reply_mode == "batch":
            buffer_key = f"__batch__{base_name}"
        elif is_group and sender_in_group:
            buffer_key = f"{session_id_data}_{sender_in_group}"
        else:
            buffer_key = session_id_data

        with self.buffer_lock:
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": contact,
                    "is_group": is_group,
                    "source_name": source_name,
                    "group_name": base_name if is_group else "",
                    "sender_in_group": sender_in_group if is_group else "",
                    "session_id_data": session_id_data,
                }
            entry = self.pending_buffers[buffer_key]
            if is_group and state.group_reply_mode == "batch" and sender_in_group:
                entry["messages"].append(f'成员"{sender_in_group}"在群"{base_name}"中对你说：{content}')
            else:
                entry["messages"].append(content)
            # 记住本条消息的 WeFlow rawid：推送时转成 OneBot message_id，
            # 否则 AstrBot 侧 message_id 会成为字符串 "None"，下游防抖会把同一人的后续消息误判为重复
            entry.setdefault("rawids", []).append(str(data.get("rawid") or ""))
            entry.setdefault("message_ts", []).append(time.time())

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]

                # 检查是否有暂存的图片（先图后文场景）
                has_pending_image = False
                if is_group and state.group_reply_mode == "mention":
                    cached = self._pending_mention_images.pop(session_id_data, None)
                    if cached and time.time() - cached["time"] < 15:
                        has_pending_image = True
                        log.info(f"📸 检测到关联图片，延长缓冲等待描述")
                        # 异步下载并描述图片，完成后注入 buffer
                        threading.Thread(
                            target=self._inject_cached_image,
                            args=(cached["data"].get("sessionId", session_id_data),
                                  buffer_key, version),
                            daemon=True,
                        ).start()

                buffer_delay = 15 if has_pending_image else config.BUFFER_SECONDS
                log.info(f"📩 收到来自 {contact} 的消息，等待 {buffer_delay}s 后统一推送")
                timer = threading.Timer(buffer_delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer

    def process_sender(self, sender_id, version=None):
        """缓冲到期：通过 OneBot 事件推送给 AstrBot。"""
        with self.buffer_lock:
            if sender_id not in self.pending_buffers:
                return
            entry = self.pending_buffers[sender_id]
            if version is not None and entry.get("timer_version", 0) != version:
                return
            if not entry["messages"]:
                return
            msgs = entry["messages"].copy()
            rawids = list(entry.get("rawids") or [])
            stamps = list(entry.get("message_ts") or [])
            if stamps and len(stamps) == len(msgs):
                keep = [i for i, ts in enumerate(stamps)
                        if time.time() - float(ts) <= self.BUFFER_MESSAGE_TTL_SECONDS]
                if len(keep) != len(msgs):
                    log.info("⏭️ 丢弃 %s 条过期缓冲消息（>%ss）[%s]",
                             len(msgs) - len(keep), int(self.BUFFER_MESSAGE_TTL_SECONDS),
                             entry.get("contact", sender_id))
                msgs = [msgs[i] for i in keep]
                if len(rawids) == len(stamps):
                    rawids = [rawids[i] for i in keep]
            if not msgs:
                entry["messages"] = []
                entry["rawids"] = []
                entry["message_ts"] = []
                entry["processing"] = False
                return
            entry["messages"] = []
            entry["rawids"] = []
            entry["message_ts"] = []
            entry["processing"] = True
            if entry["timer"]:
                entry["timer"].cancel()
                entry["timer"] = None

        contact = entry.get("contact", sender_id)
        is_group = entry.get("is_group", False)
        combined = "\n".join(msgs)
        log.info(f"推送 {len(msgs)} 条消息 [{'群' if is_group else '私'}|{contact}]")

        # 批次里最后一个 rawid → OneBot message_id（同一批重复推送得到同一个值，可被下游去重）
        last_rawid = next((r for r in reversed(rawids) if r), "")
        onebot_mid = onebot_message_id(last_rawid)

        # 构建 OneBot 事件（user_id 要用发言人身份，不能用群 sessionId）
        if is_group:
            sender_wxid = entry.get("session_id_data", "") + "_" + (entry.get("sender_in_group", "") or entry.get("source_name", ""))
        else:
            sender_wxid = entry.get("session_id_data", sender_id)
        user_id = state._wxid_to_int(sender_wxid)

        if is_group:
            # group_id 必须由稳定的 @chatroom id 派生（群昵称会改名，不能进 ID）
            group_id = state._group_id_int(entry.get("session_id_data") or contact)
            sender_name = entry.get("sender_in_group", "") or entry.get("source_name", "未知")

            if state.group_reply_mode == "batch":
                # 批处理模式：消息已预格式化好，直接使用
                formatted = combined
            else:
                # 去掉消息中的 @机器人 纯文本，换为 OneBot at 元素
                clean_text = combined
                for nick in config.BOT_NICKNAMES:
                    at_pattern = f"@{nick}"
                    if at_pattern in clean_text:
                        clean_text = clean_text.replace(at_pattern, "").strip()

                formatted = clean_text
                # 指令直通：以唤醒前缀开头的消息（/svc 歌名）不能加"某某在群里说："前缀，
                # 否则 AstrBot 的唤醒检查看到首字符不是 "/"，插件指令不会触发。
                is_command = any(clean_text.startswith(p) for p in config.COMMAND_PREFIXES if p)
                # 菜单回执直通：插件的“选择歌曲/选择音色”菜单只认整句序号（
                # selection.py 用 re.fullmatch(r"[0-9]{1,6}") 匹配 message_str），
                # 加了"某某在群里说："前缀就永远匹配不上，序号选歌会失效。
                is_menu_reply = _is_menu_reply(clean_text)
                if sender_name and not is_command and not is_menu_reply:
                    formatted = f'{sender_name}在群{entry.get("group_name", contact)}中说：{clean_text}'

            # 消息段：mention 模式带 at 机器人标记；文本里的 CQ 图片码会拆成图片段
            if state.group_reply_mode == "mention":
                msg_segments = [
                    {"type": "at", "data": {"qq": str(state._self_id_int)}},
                    {"type": "text", "data": {"text": " "}},
                ]
                msg_segments.extend(text_to_segments(formatted))
            else:
                msg_segments = text_to_segments(formatted)
            event = make_message_event("group", user_id, msg_segments,
                                       group_id=group_id,
                                       group_name=entry.get("group_name", contact),
                                       nickname=sender_name,
                                       message_id=onebot_mid)
        else:
            sender_name = entry.get("source_name", contact)
            event = make_message_event("private", user_id,
                                       text_to_segments(combined),
                                       nickname=sender_name,
                                       message_id=onebot_mid)

        # 记录 user_id/group_id → contact 映射，供 API 回复时查找（并落盘，重启不丢）
        if is_group:
            state.remember_ob_contact(state._group_id_int(entry.get("session_id_data") or contact), contact)
        else:
            state.remember_ob_contact(user_id, contact)

        sent = push_event(event)
        if sent > 0:
            log.info(f"✅ 已推送至 {sent} 个 AstrBot 客户端 [{contact}]")
        else:
            log.warning(f"⚠️ AstrBot 离线，事件已缓存待重连补发 [{contact}]")

        with self.buffer_lock:
            if sender_id in self.pending_buffers:
                entry = self.pending_buffers[sender_id]
                entry["processing"] = False
                # 推送期间新到的消息（例如图片比文本晚几秒）不会自己挂计时器，
                # 这里补一次，否则它们会一直卡在队列里直到下一条消息到来。
                if entry["messages"]:
                    self._arm_flush_locked(sender_id, entry, 1.0)

    def _fetch_wechat_voice(self, talker: str, attempts: int = 8,
                            delay: float = 1.5, expect_id: str = "") -> str | None:
        """从 WeFlow 导出最新一条语音为 wav 并落盘。

        WeFlow 的 /api/v1/messages 支持 voice=true，会把 localType=34 语音从
        silk 转成 wav 暴露成 mediaUrl（mediaType=voice）。微信客户端把语音写进
        数据库到 WeFlow 能导出之间有延迟，所以带重试。
        """
        try:
            chosen = None
            for attempt in range(1, attempts + 1):
                url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages"
                params = {
                    "access_token": config.ACCESS_TOKEN,
                    "talker": talker,
                    "media": "true",
                    "voice": "true",
                    "limit": 6,
                }
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code != 200:
                    log.error(f"WeFlow 语音API: HTTP {resp.status_code}")
                    return None
                payload = resp.json()
                messages = payload.get("messages") if isinstance(payload, dict) else payload
                voices = [m for m in (messages or [])
                          if m.get("localType") == 34 and m.get("mediaUrl")]
                # 优先按 SSE 带来的 serverId/rawid 精确定位这条语音；
                # 精确定位不到时只接受「刚发来」的语音，避免把历史语音误转写。
                now = time.time()
                fresh = [m for m in voices
                         if int(m.get("createTime") or 0) >= now - 180]
                if expect_id:
                    def _same_id(a, b):
                        a = re.sub(r"\D", "", str(a or ""))
                        b = re.sub(r"\D", "", str(b or ""))
                        if not a or not b:
                            return False
                        if a == b:
                            return True
                        return len(a) >= 10 and len(b) >= 10 and (a.endswith(b) or b.endswith(a))
                    exact = [m for m in voices
                             if _same_id(m.get("serverId", ""), expect_id)
                             or _same_id(m.get("localId", ""), expect_id)]
                    voices = exact or fresh
                else:
                    voices = fresh
                incoming = [m for m in voices if not m.get("isSend")]
                chosen = (incoming or voices or [None])[0]
                if chosen and chosen.get("isSend"):
                    log.info(f"⏭️ 最新一条语音是自己发出的（isSend=1），跳过 (talker={talker})")
                    return None
                if chosen:
                    break
                if attempt < attempts:
                    log.info(f"⏳ 语音还没导出（第 {attempt}/{attempts} 次），等待重试")
                    time.sleep(delay)

            if not chosen:
                log.warning(f"消息列表没有可导出的语音 mediaUrl (talker={talker})")
                return None

            media_url = chosen["mediaUrl"]
            sep = "&" if "?" in media_url else "?"
            dl_url = f"{media_url}{sep}access_token={config.ACCESS_TOKEN}"
            voice_resp = requests.get(dl_url, timeout=60)
            if voice_resp.status_code != 200:
                log.error(f"下载语音失败: HTTP {voice_resp.status_code} ({media_url})")
                return None
            if len(voice_resp.content) < 1024:
                log.error(f"语音文件太小，疑似导出失败: {len(voice_resp.content)} 字节")
                return None

            save_dir = os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_voices")
            os.makedirs(save_dir, exist_ok=True)
            filename = f"voice_{chosen.get('localId')}_{int(time.time())}.wav"
            save_path = os.path.join(save_dir, filename)
            with open(save_path, "wb") as f:
                f.write(voice_resp.content)
            log.info(f"🎤 微信语音已导出: {save_path} "
                     f"({len(voice_resp.content) // 1024}KB, localId={chosen.get('localId')})")
            return save_path
        except Exception as e:
            log.error(f"获取微信语音异常: {e}")
            return None

    def _transcribe_voice(self, wav_path: str) -> str | None:
        """调用 MiMo ASR 把语音 wav 转成文字。

        该网关的 ASR 接口只接受纯 audio：带任何 text part 都会返回
        {"code":400,"param":"ASR request must not include text parts"}，
        所以请求体里只有一个 user + input_audio，没有 system/text。
        """
        if not config.ASR_ENABLED:
            log.warning("ASR 未启用（asr_enabled=false），忽略语音")
            return None
        if not config.ASR_API_KEY:
            log.warning("ASR 未配置 api_key，无法转写语音")
            return None
        try:
            import base64
            import mimetypes
            with open(wav_path, "rb") as f:
                raw = f.read()
            if len(raw) < 1024:
                return None
            b64 = base64.b64encode(raw).decode("ascii")
            mime = mimetypes.guess_type(wav_path)[0] or "audio/wav"
            payload = {
                "model": config.ASR_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [{
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:{mime};base64,{b64}",
                            "format": "wav",
                        },
                    }],
                }],
                "max_completion_tokens": 1024,
            }
            url = config.ASR_API_BASE.rstrip("/") + "/chat/completions"
            t0 = time.time()
            resp = requests.post(
                url, json=payload, timeout=config.ASR_TIMEOUT,
                headers={"Authorization": f"Bearer {config.ASR_API_KEY}"},
            )
            if resp.status_code != 200:
                log.error(f"ASR 转写失败: HTTP {resp.status_code} {resp.text[:200]}")
                return None
            data = resp.json()
            choices = data.get("choices") or [{}]
            text = (choices[0].get("message", {}) or {}).get("content") or ""
            text = text.strip()
            log.info(f"📝 语音转写({time.time() - t0:.1f}s): {text!r}")
            return text or None
        except Exception as e:
            log.error(f"ASR 转写异常: {e}")
            return None

    def _notify_asr_failure(self, source_name: str, group_name: str = "") -> None:
        """语音没听清时给用户一句提示，避免「发了语音但完全没反应」。"""
        msg = getattr(config, "ASR_FAIL_REPLY", "") or ""
        if not msg:
            return
        try:
            inst = getattr(state, "sender_instance", None)
            target = group_name or source_name
            if inst is None or not target:
                return
            inst.send_text(target, msg)
            log.info(f"💬 已提示语音未识别: {target}")
        except Exception as e:
            log.warning(f"发送语音失败提示出错: {e}")

    def process_voice_message(self, data):
        """收到语音：导出 wav → bridge 侧 ASR 转文字 → 以纯文字推给 AstrBot。"""
        session_id = data.get("sessionId", "") or ""
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        rawid = data.get("rawid", "") or data.get("serverId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        log.info(f"🎤 收到语音: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        # 自己发出去的语音（比如 TTS 语音回复）回流时不能当作用户说话，否则会自问自答
        if data.get("isSend") or data.get("isSelf") or data.get("fromSelf"):
            log.info(f"⏭️ 自己发出的语音跳过 [{source_name}]")
            return

        # 同一条语音可能被 SSE/轮询重复推送，60 秒内同一 localId/serverId 只处理一次
        dedupe_seed = str(rawid or data.get("localId") or "")
        if dedupe_seed:
            dedupe_key = f"{session_id}:{dedupe_seed}"
            now_ts = time.time()
            recent = getattr(self, "_recent_voice_keys", None)
            if recent is None:
                recent = self._recent_voice_keys = {}
            for stale in [k for k, ts in recent.items() if now_ts - ts > 120]:
                recent.pop(stale, None)
            if now_ts - recent.get(dedupe_key, 0.0) < 60:
                log.info(f"↩️ 重复语音事件已跳过 [{source_name}] key={dedupe_key}")
                return
            recent[dedupe_key] = now_ts

        # 调试入口 /test_voice 会设置 _voice_override，跳过 WeFlow 导出直接用本地 wav
        path = getattr(self, "_voice_override", None) or self._fetch_wechat_voice(
            session_id, expect_id=str(rawid or ""))
        if not path:
            return

        transcript = self._transcribe_voice(path)
        if not transcript:
            log.warning("⚠️ 语音转写为空/失败，本条不推送（record 段会触发 AstrBot STT 400）")
            self._notify_asr_failure(source_name, group_name)
            return
        text = (config.ASR_TEXT_PREFIX or "") + transcript

        # 同一句话 30 秒内只推一次，防止语音自我回流造成连环回复
        recent_texts = getattr(self, "_recent_voice_texts", None)
        if recent_texts is None:
            recent_texts = self._recent_voice_texts = {}
        now_v = time.time()
        for stale in [k for k, ts in recent_texts.items() if now_v - ts > 120]:
            recent_texts.pop(stale, None)
        if now_v - recent_texts.get(text, 0.0) < 30:
            log.info(f"↩️ 重复语音内容已跳过 [{source_name}]: {text[:30]!r}")
            return
        recent_texts[text] = now_v

        segments = [{"type": "text", "data": {"text": text}}]
        mid = onebot_message_id(rawid)

        if is_group:
            sender_name = data.get("senderName", "") or source_name
            user_id = state._wxid_to_int(f"{session_id}_{sender_name}")
            group_id = state._group_id_int(session_id)
            base_name = re.sub(r'\s*\(\d+\)\s*$', '', group_name or source_name).strip()
            event = make_message_event("group", user_id, segments,
                                       group_id=group_id, group_name=base_name,
                                       nickname=sender_name, message_id=mid)
            state.remember_ob_contact(group_id, base_name)
        else:
            user_id = state._wxid_to_int(session_id)
            event = make_message_event("private", user_id, segments,
                                       nickname=source_name, message_id=mid)
            state.remember_ob_contact(user_id, source_name)

        if push_event(event):
            log.info(f"✅ 语音转写文字已推送至 AstrBot [{source_name}]: {text[:40]!r}")
        else:
            log.warning(f"⚠️ AstrBot 离线，语音事件已入缓存队列 [{source_name}]")

    def push_test_text(self, session_id: str, text: str,
                       nickname: str = "", group_name: str = "") -> bool:
        """调试/自检用：把一条文字当作用户消息推给 AstrBot。

        走的是 bridge 现有的同一条 WebSocket，因此 AstrBot 的回复也仍然从
        bridge 出站，不会出现「第二个 OneBot 客户端抢回包」的问题。
        """
        segments = [{"type": "text", "data": {"text": text}}]
        is_group = bool(group_name) or "@chatroom" in session_id
        mid = onebot_message_id("")
        if is_group:
            user_id = state._wxid_to_int(f"{session_id}_{nickname or session_id}")
            group_id = state._group_id_int(session_id)
            event = make_message_event("group", user_id, segments,
                                       group_id=group_id, group_name=group_name,
                                       nickname=nickname or session_id, message_id=mid)
            state.remember_ob_contact(group_id, group_name)
        else:
            user_id = state._wxid_to_int(session_id)
            event = make_message_event("private", user_id, segments,
                                       nickname=nickname or session_id, message_id=mid)
            state.remember_ob_contact(user_id, nickname or session_id)
        log.info(f"🧪 注入测试消息 → {nickname or session_id}: {text!r}")
        return bool(push_event(event))

    def handle_push_payload(self, data: dict) -> bool:
        """处理一条 WeFlow 推送（SSE 与自检入口共用同一条入口）。

        真实推送只带 event/sessionId/sessionType/rawid/avatarUrl/sourceName/
        content/timestamp，没有 localType、isSend、mediaUrl —— 所以语音的
        媒体要回查 /api/v1/messages 才能拿到（见 _fetch_wechat_voice）。
        """
        msg_time = data.get("timestamp", 0)
        if msg_time and msg_time < self.start_timestamp:
            return False
        raw_id = data.get("rawid", "")
        if raw_id and raw_id in self.processed_ids:
            return False
        if raw_id:
            self.processed_ids.add(raw_id)
        if self.should_ignore(data):
            return False
        content = data.get("content", "") or ""
        if data.get("sessionType", "") == "group" or "@chatroom" in data.get("sessionId", ""):
            log.info(f"📩 群消息 [{data.get('sourceName','')}]: {content[:60]}")
            if state.group_reply_mode == "mention":
                mentioned = any(f"@{n}" in content for n in config.BOT_NICKNAMES)
                log.info(f"   @={mentioned}")
        else:
            log.info(f"📩 收到: {data.get('sourceName','')} → {content[:50]}")
        self.add_to_buffer(data)
        return True

    def listen_sse(self):
        """连接 WeFlow SSE 推送。"""
        sse_url = f"{config.WE_FLOW_BASE_URL}/api/v1/push/messages?access_token={config.ACCESS_TOKEN}"
        # 日志脱敏：token 不能落到日志文件里
        _safe_url = re.sub(r"(access_token=)[^&]+", r"\1***", sse_url)
        log.info(f"连接 WeFlow 推送服务: {_safe_url}")
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}

        try:
            self._sse_session = requests.get(sse_url, headers=headers, stream=True, timeout=None)
            if self._sse_session.status_code != 200:
                log.error(f"连接失败: HTTP {self._sse_session.status_code}")
                return
            log.info("✅ 已连接到 WeFlow 推送")

            for line in self._sse_session.iter_lines(decode_unicode=True):
                if not state.running:
                    break
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    self.handle_push_payload(data)

        except requests.exceptions.ConnectionError:
            log.error("无法连接 WeFlow")
        except Exception as e:
            log.error(f"SSE 异常: {e}")
        finally:
            self._sse_session = None

    _MEDIA_TYPES = ("image", "sticker", "emoji")

    # 缓冲消息保鲜期：超过这个秒数的排队消息在推送前丢弃。
    # 背景（2026-10-16）：入向图片取图失败/迟到时，消息会在 pending_buffers 里滞留十几分钟，
    # 之后和新文本一起推送，AI 会把十几分钟前的老图（甚至机器人自己发的人设图）
    # 当成本轮新图来解读。这里用 TTL 兜底，保证推给 AstrBot 的都是新鲜消息。
    BUFFER_MESSAGE_TTL_SECONDS = 120.0

    def _arm_flush_locked(self, buffer_key, entry, delay):
        """在已持有 buffer_lock 的前提下给条目挂上推送计时器（已有存活计时器则不动）。"""
        if entry.get("processing"):
            return
        timer = entry.get("timer")
        if timer is not None and getattr(timer, "is_alive", lambda: False)():
            return
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass
        entry["timer_version"] = int(entry.get("timer_version", 0) or 0) + 1
        version = entry["timer_version"]
        new_timer = threading.Timer(delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
        new_timer.daemon = True
        new_timer.start()
        entry["timer"] = new_timer

    def _query_messages_with_media(self, talker: str, limit: int = 6) -> list:
        """取 WeFlow 最近消息（含媒体字段）。失败返回空列表。"""
        url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages"
        params = {
            "access_token": config.ACCESS_TOKEN,
            "talker": talker,
            "media": "true",
            "limit": limit,
        }
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            log.error(f"WeFlow 消息API: HTTP {resp.status_code}")
            return []
        data = resp.json()
        messages = data if isinstance(data, list) else data.get("messages", data.get("data", []))
        return messages if isinstance(messages, list) else []

    def _resolve_chat_record(self, data: dict) -> str | None:
        """把「[聊天记录]」占位换成可读文本。

        WeFlow 的消息推送只带 content="[聊天记录]"，真正的条目在
        /api/v1/messages 返回的 content/rawContent 里的 appmsg XML 中
        （<datalist> 下每条 <dataitem> 的 datadesc/sourcename）。
        这里回查该会话最近消息，取最新一条聊天记录卡片解析。
        """
        talker = (data.get("sessionId") or data.get("groupName")
                  or data.get("sourceName") or "").strip()
        if not talker:
            return None
        try:
            messages = self._query_messages_with_media(talker, limit=40)
        except Exception as exc:
            log.warning(f"📜 聊天记录回查失败: {exc}")
            return None
        cards = [m for m in messages if _is_chat_record_type(m.get("localType"))]
        if not cards:
            log.info(f"📜 未找到聊天记录卡片（talker={talker}，最近 {len(messages)} 条）")
            return None

        def _sort_key(msg):
            try:
                return int(msg.get("localId") or 0)
            except (TypeError, ValueError):
                return 0

        cards.sort(key=_sort_key)
        for card in reversed(cards[-3:]):
            # media=true 时 content 只有占位文本「[聊天记录]」，XML 在 rawContent 里
            xml = card.get("rawContent") or ""
            if "<dataitem" not in xml:
                xml = card.get("content") or ""
            record = _parse_chat_record_xml(xml)
            if not record:
                continue
            formatted = _format_chat_record(record)
            log.info(f"📜 聊天记录已解析：{formatted.splitlines()[0][:60]}")
            return formatted
        return None

    @staticmethod
    def _pick_media_message(messages: list) -> dict | None:
        """挑出可下载的图片/表情消息：优先"别人发来的"，其次任意方向。"""
        with_media = [m for m in messages
                      if m.get("mediaType") in WeFlowBridge._MEDIA_TYPES and m.get("mediaUrl")]
        if not with_media:
            return None
        incoming = [m for m in with_media if not m.get("isSend")]
        return (incoming or with_media)[0]

    def _fetch_wechat_image(self, talker: str, attempts: int = 5,
                            delay: float = 1.5) -> str | None:
        """从 WeFlow REST API 获取最新图片并保存到本地。

        WeChat 的高清原图要等 WeChat 客户端真的把文件下下来之后 WeFlow 才导得出
        mediaUrl（WeFlow 的 img_helper.dll 只对白名单会话触发下载）。消息推送与
        下载完成之间有竞态，所以这里带重试：只要最新一条是图片且还没有 mediaUrl
        就等一会儿再查。
        """
        try:
            attempts = max(1, int(attempts))
            chosen = None
            for attempt in range(1, attempts + 1):
                messages = self._query_messages_with_media(talker)
                chosen = self._pick_media_message(messages)
                if chosen:
                    break
                newest = messages[0] if messages else None
                newest_is_media = bool(newest) and (
                    newest.get("mediaType") in self._MEDIA_TYPES
                    or newest.get("localType") == 3
                    or newest.get("content") == "[图片]"
                )
                if attempt < attempts and newest_is_media:
                    log.info(f"⏳ 图片还没有本地缓存（等 WeChat 下载，第 {attempt}/{attempts} 次）")
                    time.sleep(delay)
                    continue
                break

            if not chosen:
                log.warning(f"消息列表无图片 mediaUrl (talker={talker})")
                return None

            media_url = chosen["mediaUrl"]
            sep = "&" if "?" in media_url else "?"
            dl_url = f"{media_url}{sep}access_token={config.ACCESS_TOKEN}"

            img_resp = requests.get(dl_url, timeout=30)
            if img_resp.status_code != 200:
                log.error(f"下载图片失败: HTTP {img_resp.status_code} ({media_url})")
                return None

            # 根据 Content-Type 确定扩展名
            ct = img_resp.headers.get("Content-Type", "")
            ext = ".jpg"
            if "png" in ct: ext = ".png"
            elif "gif" in ct: ext = ".gif"
            elif "webp" in ct: ext = ".webp"

            filename = f"wechat_{int(time.time())}{ext}"
            save_dir = os.path.join(config.ASTRBOT_ATTACHMENTS, "wechat_images")
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, filename)

            with open(save_path, "wb") as f:
                f.write(img_resp.content)

            log.info(f"✅ 微信图片已保存: {save_path} (localId={chosen.get('localId')})")
            return save_path
        except Exception as e:
            log.error(f"获取微信图片异常: {e}")
            return None

    def process_image_message(self, data):
        """处理图片消息：从 WeFlow 取图 → ollama 描述 → 注入缓冲区"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        rawid = data.get("rawid", "")

        log.info(f"🖼️ 收到图片: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        # 注册待处理的图片（ollama 完成前标记为 pending）
        img_event = threading.Event()
        self._pending_image[talker_id] = {"caption": None, "event": img_event}

        try:
            # 取图 → 按转发策略决定直通原图还是生成描述
            image_path = self._fetch_wechat_image(session_id)
            mode = _forward_mode()
            caption_text = _image_payload(image_path, mode)
            if image_path and normalize_forward_mode(mode) == "direct":
                log.info(f"🖼️ 已转发原图给 AstrBot: {os.path.basename(image_path)}")
            elif image_path:
                log.info(f"📝 图片描述[{mode}]: {caption_text[:60]}...")
            else:
                log.info("⚠️ 取图失败，图片降级为文字")

            # 注入图片描述到缓冲区
            with self.buffer_lock:
                self._pending_image[talker_id] = {"caption": caption_text, "event": img_event}

                # 批处理模式用群共享 key
                if is_group and state.group_reply_mode == "batch" and group_name:
                    g_base = re.sub(r'\s*\(\d+\)\s*$', '', group_name).strip()
                    batch_key = f"__batch__{g_base}"
                    if batch_key in self.pending_buffers:
                        entry = self.pending_buffers[batch_key]
                        entry["messages"].insert(0, f'成员"{source_name}"在群"{group_name}"中对你说：{caption_text}')
                        entry.setdefault("message_ts", []).insert(0, time.time())
                        entry["image_ready"] = True
                        self._arm_flush_locked(batch_key, entry, 5.0)
                        log.info(f"📝 图片已注入批处理队列")
                        return
                    # 没有文字排队，用 batch key 创建独立条目
                    self.pending_buffers[batch_key] = {
                        "messages": [f'成员"{source_name}"在群"{group_name}"中对你说：{caption_text}'],
                        "message_ts": [time.time()],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name,
                        "is_group": True,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name,
                        "sender_in_group": source_name,
                    }
                    log.info(f"📩 图片无文本跟随，创建批处理图片条目")
                    version = 1
                    timer = threading.Timer(5, lambda v=version, sid=batch_key: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[batch_key]["timer"] = timer
                    self.pending_buffers[batch_key]["timer_version"] = version
                elif talker_id in self.pending_buffers:
                    # 已有文本在排队，注入图片上下文
                    entry = self.pending_buffers[talker_id]
                    entry["messages"].insert(0, f"{caption_text}")
                    entry.setdefault("message_ts", []).insert(0, time.time())
                    entry["image_ready"] = True
                    # 关键：老条目可能早已推送过、计时器已死。不补计时器的话这张图会一直卡在队列里
                    # （2026-10-16 那次“AI 读到的其实是十几分钟前的人设图”就是这个原因）。
                    self._arm_flush_locked(talker_id, entry, 2.0)
                    log.info(f"📝 图片已注入待处理文本队列")
                else:
                    # 没有文本排队，创建单条图片消息处理
                    log.info(f"📩 图片无文本跟随，直接处理")
                    self.pending_buffers[talker_id] = {
                        "messages": [f"{caption_text}"],
                        "message_ts": [time.time()],
                        "timer": None,
                        "timer_version": 0,
                        "processing": False,
                        "contact": group_name if is_group and group_name else source_name,
                        "is_group": is_group,
                        "source_name": source_name,
                        "session_id_data": session_id,
                        "group_name": group_name if is_group else "",
                        "sender_in_group": "",
                    }
                    version = 1
                    timer = threading.Timer(2, lambda v=version, sid=talker_id: self.process_sender(sid, v))
                    timer.daemon = True
                    timer.start()
                    self.pending_buffers[talker_id]["timer"] = timer
                    self.pending_buffers[talker_id]["timer_version"] = version
        finally:
            # 确保 Event 被设置
            img_event.set()

    def process_emoji_message(self, data):
        """处理表情包消息：尝试下载图片并描述，失败则保留原文转发"""
        session_id = data.get("sessionId", "")
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        content = data.get("content", "[表情]")

        log.info(f"😀 收到表情包: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        talker_id = data.get("talkerId", "") or data.get("sessionId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        # 取图 → 按转发策略直通原图或生成描述
        try:
            image_path = self._fetch_wechat_image(session_id)
            mode = normalize_forward_mode(_forward_mode())
            if image_path and mode == "direct":
                content = build_image_payload(image_path, None, mode)
                log.info(f"😀 表情包已转发原图: {os.path.basename(image_path)}")
            elif image_path:
                caption = caption_image_via_ollama(image_path)
                if caption:
                    emoji_text = f"[表情: {caption}]"
                    content = f"{build_image_payload(image_path, None, 'direct')}{emoji_text}" if mode == "both" else emoji_text
                    log.info(f"😀 表情包已描述[{mode}]: {caption[:60]}...")
                else:
                    content = build_image_payload(image_path, None, "direct") if mode == "both" else content
                    log.info("😀 表情包图片描述失败，保留原文")
            else:
                log.info("😀 表情包无可用图片，保留原文")

            # 直接注入缓冲区（不等待，立即推送）
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id)
        except Exception as e:
            log.warning(f"😀 表情包处理异常: {e}")
            # 异常时也保底发送原文
            self.add_text_to_buffer(talker_id, source_name, group_name,
                                    session_id, content, is_group, talker_id)

    def _inject_cached_image(self, session_id, buffer_key, version):
        """下载缓存图片 → 按转发策略注入到 buffer 条目（在缓冲计时器到期前完成）"""
        try:
            img_path = self._fetch_wechat_image(session_id, attempts=2)
            text = _image_payload(img_path, _forward_mode())

            with self.buffer_lock:
                if buffer_key in self.pending_buffers:
                    entry = self.pending_buffers[buffer_key]
                    # 版本匹配才注入（版本变了说明被新消息重置过）
                    if entry.get("timer_version") == version:
                        entry["messages"].insert(0, text)
                        entry.setdefault("message_ts", []).insert(0, time.time())
                        log.info(f"📸 缓存图片已注入: {text[:60]}")
                    else:
                        log.info(f"📸 缓存图片跳过（buffer 版本已变更）")
        except Exception as e:
            log.warning(f"📸 缓存图片处理异常: {e}")

    def add_text_to_buffer(self, session_id_data, source_name, group_name,
                           session_id, content, is_group, sender_key):
        """通用：将一段文本直接加入缓冲队列（供表情/图片等异步处理完后调用）"""
        with self.buffer_lock:
            buffer_key = sender_key
            if buffer_key not in self.pending_buffers:
                self.pending_buffers[buffer_key] = {
                    "messages": [],
                    "timer": None,
                    "timer_version": 0,
                    "processing": False,
                    "contact": group_name if is_group and group_name else source_name,
                    "is_group": is_group,
                    "source_name": source_name,
                    "session_id_data": session_id,
                    "group_name": group_name if is_group else "",
                    "sender_in_group": source_name if is_group else "",
                }
            entry = self.pending_buffers[buffer_key]
            entry["messages"].append(content)
            entry["message_ts"] = [*entry.get("message_ts", []), time.time()]
            entry.setdefault("rawids", []).append("")  # 图片/表情分支没有 rawid，留空占位

            if not entry["processing"]:
                if entry["timer"]:
                    entry["timer"].cancel()
                entry["timer_version"] += 1
                version = entry["timer_version"]
                delay = 2  # 表情/图片单独推送，短缓冲
                timer = threading.Timer(delay, lambda v=version, sid=buffer_key: self.process_sender(sid, v))
                timer.daemon = True
                timer.start()
                entry["timer"] = timer
                entry["timer_version"] = version
def caption_image_via_ollama(image_path: str) -> str | None:
    """对图片进行文字描述，支持 ollama 和 OpenAI 兼容 API 两种后端。"""
    try:
        import base64
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")

        if state.image_caption_provider == "openai":
            if not state.image_caption_api_base:
                log.warning("⚠️ provider=openai 但未配置 api_base，跳过图片描述")
                return None
            # OpenAI 兼容 API
            resp = requests.post(
                f"{state.image_caption_api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {state.image_caption_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": state.image_caption_model,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": state.image_caption_prompt},
                            {"type": "image_url", "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            }},
                        ],
                    }],
                    # 注意：deepseek-flash 这类「边想边答」的模型会先输出 reasoning_content。
                    # 预算给太小（曾用 300）时整段预算被思考吃掉 → content 为空、finish_reason=length，
                    # 表现就是「表情包图片描述失败，保留原文」。所以要给足预算，并在 content 为空时退回思考文本。
                    "max_tokens": 1500,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                choice = resp.json()["choices"][0]
                msg = choice.get("message") or {}
                caption = (msg.get("content") or "").strip()
                if not caption:
                    reason_text = (msg.get("reasoning_content") or "").strip()
                    if reason_text:
                        log.info(f"🖼️ content 为空（finish={choice.get('finish_reason')}），改用思考文本作描述")
                        caption = reason_text
                if caption:
                    log.info(f"🖼️ 图片描述: {caption[:80]}...")
                    return caption
                log.warning(f"⚠️ 图片描述为空：finish_reason={choice.get('finish_reason')} usage={resp.json().get('usage')}")
            else:
                log.warning(f"OpenAI API 返回 HTTP {resp.status_code}: {resp.text[:200]}")
        else:
            # ollama 原生 API
            resp = requests.post(
                f"{state.ollama_base_url}/api/generate",
                json={
                    "model": state.image_caption_model,
                    "prompt": state.image_caption_prompt,
                    "images": [img_b64],
                    "stream": False,
                },
                timeout=state.ollama_timeout,
            )
            if resp.status_code == 200:
                caption = resp.json().get("response", "").strip()
                if caption:
                    log.info(f"🖼️ 图片描述: {caption[:80]}...")
                    return caption
            else:
                log.warning(f"ollama 返回 HTTP {resp.status_code}: {resp.text[:100]}")

    except requests.Timeout:
        log.warning(f"图片描述超时 (30s)")
    except Exception as e:
        log.warning(f"图片描述失败: {e}")
    return None
