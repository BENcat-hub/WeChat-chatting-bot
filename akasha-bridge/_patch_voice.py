"""一次性补丁：给 bridge 加入「收到微信语音 → 推 record 段给 AstrBot → STT 转文字」链路。

之前 should_ignore 直接丢弃 34/“[语音]”，入向语音永远到不了 AstrBot。
"""
from __future__ import annotations

import pathlib

p = pathlib.Path("bridge_core.py")
src = p.read_text(encoding="utf-8")

# 1) 不再丢弃语音
old_ignore = '''        if msg_type in (34,):  # 34=语音
            return True
        if content and "[语音]" in content:
            return True
'''
new_ignore = '''        # 34=语音 / "[语音]" 不再丢弃：AstrBot 侧装了 MiMo STT，bridge 负责把
        # 语音导出成 wav 并以 OneBot record 段推过去，由 AstrBot 转写成文字。
        # 语音消息的 content 通常是 "[语音]"，没有正文，所以不能走下面的空内容过滤。
'''
assert old_ignore in src, "should_ignore 锚点没找到"
src = src.replace(old_ignore, new_ignore, 1)

# 2) add_to_buffer 里接语音分支
old_branch = '''        if content in ("[动画表情]", "[表情]"):'''
new_branch = '''        if content in ("[语音]", "[Voice]", "[voice]"):
            # 群聊 mention 模式下语音里没有 @ 字样，无法判断是否在叫机器人，直接跳过。
            if is_group and state.group_reply_mode == "mention":
                log.info("⏭️ 群语音跳过（mention 模式无法检测 @）")
                return
            threading.Thread(target=self.process_voice_message,
                             args=(data,), daemon=True).start()
            return

        if content in ("[动画表情]", "[表情]"):'''
assert old_branch in src
src = src.replace(old_branch, new_branch, 1)

# 3) 取语音 + 推事件，插在 listen_sse 之前
anchor = "    def listen_sse(self):"
assert anchor in src

methods = '''    def _fetch_wechat_voice(self, talker: str, attempts: int = 4,
                            delay: float = 1.5) -> str | None:
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
                incoming = [m for m in voices if not m.get("isSend")]
                chosen = (incoming or voices or [None])[0]
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

    def process_voice_message(self, data):
        """收到语音：导出 wav → 以 OneBot record 段推送，AstrBot 用 STT 转文字。"""
        session_id = data.get("sessionId", "") or ""
        source_name = data.get("sourceName", "") or "未知"
        group_name = data.get("groupName", "")
        rawid = data.get("rawid", "") or data.get("serverId", "")
        is_group = bool(group_name) or "@chatroom" in session_id

        log.info(f"🎤 收到语音: {source_name}" +
                 (f" (群:{group_name})" if group_name else ""))

        path = self._fetch_wechat_voice(session_id)
        if not path:
            return

        segments = [{"type": "record",
                     "data": {"file": pathlib.Path(path).resolve().as_uri()}}]
        mid = onebot_message_id(rawid)

        if is_group:
            sender_name = data.get("senderName", "") or source_name
            user_id = state._wxid_to_int(f"{session_id}_{sender_name}")
            group_id = state._group_id_int(session_id)
            base_name = re.sub(r'\\s*\\(\\d+\\)\\s*$', '', group_name or source_name).strip()
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
            log.info(f"✅ 语音已推送至 AstrBot（等 STT 转写）[{source_name}]")
        else:
            log.warning(f"⚠️ AstrBot 离线，语音事件已入缓存队列 [{source_name}]")

'''
src = src.replace(anchor, methods + anchor, 1)
src = src.replace("import requests\n", "import pathlib\n\nimport requests\n", 1)

p.write_text(src, encoding="utf-8")
print("bridge_core.py patched")
