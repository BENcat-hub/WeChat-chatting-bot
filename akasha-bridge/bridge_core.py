"""
桥接核心模块：WeFlowBridge 类。

职责：
1. 连接 WeFlow SSE 推送，接收微信消息
2. 消息缓冲合并（BUFFER_SECONDS）
3. 构造 OneBot 事件，推送给 AstrBot
4. 多层消息去重（rawid、内容、自回复）
"""

import json
import logging
import os
import queue
import re
import threading
import time
from collections import defaultdict
from datetime import datetime

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
        if msg_type in (34,):  # 34=语音
            return True
        if content and "[语音]" in content:
            return True
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
                if sender_name:
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
                        msg_time = data.get("timestamp", 0)
                        if msg_time < self.start_timestamp:
                            continue
                        raw_id = data.get("rawid", "")
                        if raw_id in self.processed_ids:
                            continue
                        self.processed_ids.add(raw_id)
                        if not self.should_ignore(data):
                            if data.get("sessionType", "") == "group" or "@chatroom" in data.get("sessionId", ""):
                                content = data.get("content", "")
                                log.info(f"📩 群消息 [{data.get('sourceName','')}]: {content[:60]}")
                                if state.group_reply_mode == "mention":
                                    mentioned = any(f"@{n}" in content for n in config.BOT_NICKNAMES)
                                    log.info(f"   @={mentioned}")
                            else:
                                log.info(f"📩 收到: {data.get('sourceName','')} → {data.get('content','')[:50]}")
                            self.add_to_buffer(data)
                    except json.JSONDecodeError:
                        pass

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
