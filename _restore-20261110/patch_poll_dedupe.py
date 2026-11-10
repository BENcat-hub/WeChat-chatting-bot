# -*- coding: utf-8 -*-
"""修漏推兜底的两个 bug：
1) SSE 路径从不登记去重键 -> 轮询把同一条消息再注入一次（12:38:39 实测）
2) 轮询补投的群消息 sourceName 用了会话 displayName（群名）-> 署名变成群名
"""
import io, os, re, shutil, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "akasha-bridge", "bridge_core.py")
src = io.open(P, encoding="utf-8").read()
orig = src

# ---------- 1) handle_push_payload 里去重 ----------
anchor1 = """        if self.should_ignore(data):
            return False
"""
add1 = """        if self.should_ignore(data):
            return False
        # 去重（2026-11-10）：SSE 与轮询兜底会看到同一条消息，此前只有轮询登记键，
        # SSE 从不登记 -> 轮询每隔 20s 就把刚推过的消息再补投一次（实测 12:38:25 与
        # 12:38:39 同一句进了两遍，第二条署名还被写成群名）。
        if not self._remember_msg_key(data):
            log.info(f"🩹 重复消息已跳过: [{data.get('sourceName','')}] {(content if 'content' in dir() else data.get('content',''))[:40]!r}")
            return False
"""
assert anchor1 in src
src = src.replace(anchor1, add1, 1)

# ---------- 2) 去重键：新增宽松键（容忍秒级偏差） ----------
anchor2 = """    def _remember_msg_key(self, data: dict) -> bool:
        \"\"\"登记去重键；已登记过返回 False（调用方据此跳过）。\"\"\"
        key = self._msg_key(data)
        if not key:
            return True
        with self._poll_lock:
            if key in self._seen_msg_keys:
                return False
            self._seen_msg_keys.add(key)
"""
add2 = """    def _loose_msg_key(self, data: dict):
        \"\"\"宽松去重键：会话 + 内容哈希（不含时间戳）。

        SSE 的 timestamp 与 /api/v1/messages 的 createTime 可能差几秒（或一方缺字段），
        精确键就配不上；这里用 120s 窗口的宽松键兜住同一句话的重复投递。
        \"\"\"
        sid = data.get("sessionId") or ""
        content = (data.get("content") or "").strip()
        if not sid or not content:
            return None
        digest = hashlib.sha1(content.encode("utf-8", "ignore")).hexdigest()[:12]
        return f"{sid}|{digest}"

    def _remember_msg_key(self, data: dict) -> bool:
        \"\"\"登记去重键；已登记过返回 False（调用方据此跳过）。\"\"\"
        now = time.time()
        loose = self._loose_msg_key(data)
        with self._poll_lock:
            if loose:
                prev = self._seen_loose.get(loose)
                if prev is not None and now - prev <= 120.0:
                    return False
                self._seen_loose[loose] = now
                if len(self._seen_loose) > 2000:
                    cut = now - 600.0
                    self._seen_loose = {k: v for k, v in self._seen_loose.items() if v >= cut}
        key = self._msg_key(data)
        if not key:
            return True
        with self._poll_lock:
            if key in self._seen_msg_keys:
                return False
            self._seen_msg_keys.add(key)
"""
assert anchor2 in src
src = src.replace(anchor2, add2, 1)

# ---------- 3) __init__ 里加 _seen_loose ----------
anchor3 = "        self._seen_msg_keys = set()\n"
add3 = "        self._seen_msg_keys = set()\n        self._seen_loose = {}\n"
assert anchor3 in src
src = src.replace(anchor3, add3, 1)

# ---------- 4) 群成员名字缓存 ----------
anchor4 = "    def _query_poll_messages(self, talker: str) -> list:\n"
add4 = """    _GROUP_MEMBER_TTL = 600.0

    def _group_member_name(self, chatroom: str, wxid: str) -> str:
        \"\"\"群消息的发送者昵称：/api/v1/group-members 的 displayName（带 600s 缓存）。\"\"\"
        if not chatroom or not wxid or not config.ACCESS_TOKEN:
            return ""
        now = time.time()
        cached = self._group_members_cache.get(chatroom)
        if not cached or now - cached[0] > self._GROUP_MEMBER_TTL:
            url = f"{config.WE_FLOW_BASE_URL}/api/v1/group-members"
            try:
                resp = requests.get(url, params={"access_token": config.ACCESS_TOKEN,
                                                 "talker": chatroom}, timeout=10)
                members = {}
                if resp.status_code == 200:
                    for row in (resp.json() or {}).get("members") or []:
                        w = row.get("wxid") or ""
                        if w:
                            members[w] = (row.get("groupNickname") or row.get("displayName")
                                          or row.get("nickname") or "")
                self._group_members_cache[chatroom] = (now, members)
                cached = (now, members)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"漏推兜底：取群成员失败 {chatroom}: {exc}")
                return ""
        return (cached[1] or {}).get(wxid, "")

    def _query_poll_messages(self, talker: str) -> list:
"""
assert anchor4 in src
src = src.replace(anchor4, add4, 1)

anchor5 = "        self._seen_loose = {}\n"
add5 = "        self._seen_loose = {}\n        self._group_members_cache = {}\n"
assert anchor5 in src
src = src.replace(anchor5, add5, 1)

# ---------- 5) 轮询补投：署名用成员名 + 交给 handle_push_payload 统一去重 ----------
old_poll = """                data = {
                    "event": "message",
                    "sessionId": username,
                    "sessionType": "group" if is_group else "private",
                    "rawid": f"poll-{m.get('localId')}",
                    "avatarUrl": "",
                    "sourceName": display,
                    "talkerId": username,
                    "talkerName": display,
                    "type": int(m.get("localType") or 1),
                    "content": content,
                    "timestamp": ts,
                }
                if not self._remember_msg_key(data):
                    newest = max(newest, ts)
                    continue
                log.info(f"🩹 漏推补投: [{display}] {content[:40]!r}")
                self.handle_push_payload(data)
"""
new_poll = """                sender_wxid = (m.get("senderUsername") or "").strip()
                if is_group:
                    sender_label = (self._group_member_name(username, sender_wxid)
                                    or sender_wxid or display)
                else:
                    sender_label = display
                data = {
                    "event": "message",
                    "sessionId": username,
                    "sessionType": "group" if is_group else "private",
                    "rawid": f"poll-{m.get('localId')}",
                    "avatarUrl": "",
                    "sourceName": sender_label,
                    "senderName": sender_label,
                    "senderId": sender_wxid,
                    "talkerId": username,
                    "talkerName": display,
                    "type": int(m.get("localType") or 1),
                    "content": content,
                    "timestamp": ts,
                }
                if not self.handle_push_payload(data):
                    newest = max(newest, ts)
                    continue
                log.info(f"🩹 漏推补投: [{sender_label}] {content[:40]!r}")
"""
assert old_poll in src
src = src.replace(old_poll, new_poll, 1)

# 上面那句 log 里的 dir() 取巧不干净，改成直接数据取 content
src = src.replace(
    """            log.info(f"🩹 重复消息已跳过: [{data.get('sourceName','')}] {(content if 'content' in dir() else data.get('content',''))[:40]!r}")""",
    """            log.info(f"🩹 重复消息已跳过: [{data.get('sourceName','')}] {(data.get('content') or '')[:40]!r}")""",
    1)

if src == orig:
    raise SystemExit("nothing changed")

bak = P + ".bak-pollfix-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(P, bak)
io.open(P, "w", encoding="utf-8", newline="").write(src)
print("patched", P)
print("backup", bak)
