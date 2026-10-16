"""只读诊断：核对「会话 / 用户档案归属」是否把 用户A 与 用户B 搞反了。

比对四方事实（不改任何文件、不写库）：
  1) WeFlow /api/v1/sessions          —— 微信侧 wxid ↔ 显示名（真名）
  2) WeFlow /api/v1/messages          —— 微信侧每个人最近的消息原文（真话）
  3) AstrBot data_v4.db conversations —— AI 侧每个 umo 记的最近消息原文
  4) companions.json                  —— 用户档案的身份戳 / 昵称 / 主动投递字段

用法：
    python scripts/diag_person_mixup.py            # 全部
    python scripts/diag_person_mixup.py sessions   # 只看某一节
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

STACK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "<FILL_ME_WEFLOW_TOKEN>"
BASE = "http://127.0.0.1:5031/api/v1"

WXID = {
    "用户A": "wxid_CONTACT_B",
    "用户B": "wxid_CONTACT_C",
    "用户C": "wxid_CONTACT_A",
}
CANON = {  # 稳定 id（akasha-bridge state.stable_int）
    "用户A": 1000000001,
    "用户B": 1000000003,
    "用户C": 1000000006,
}


def api(path: str):
    for sep in ("&", "?"):
        url = f"{BASE}{path}{sep}access_token={TOKEN}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
    return {"__error__": last}


def show_sessions() -> None:
    print("=" * 72)
    print("1) WeFlow sessions（微信侧真名）")
    data = api("/sessions")
    items = data.get("data") or data.get("sessions") or data
    if isinstance(items, dict):
        items = items.get("list") or []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        print(
            "   {username!s:34} {displayName!s:16} type={sessionType} last={lastTimestamp}".format(
                username=it.get("username"),
                displayName=it.get("displayName"),
                sessionType=it.get("sessionType"),
                lastTimestamp=it.get("lastTimestamp"),
            )
        )


def show_wechat_messages(limit: int = 6) -> None:
    print("=" * 72)
    print(f"2) WeFlow messages（微信侧真话，最近 {limit} 条）")
    for name, wxid in WXID.items():
        data = api(f"/messages?talker={wxid}&limit={limit}")
        if "__error__" in data:
            print(f"   [{name}] 取消息失败: {data['__error__']}")
            continue
        items = data.get("data") or data.get("messages") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("messages") or []
        print(f"   [{name}] {wxid} —— {len(items) if isinstance(items, list) else '?'} 条")
        for it in (items if isinstance(items, list) else [])[-limit:]:
            if not isinstance(it, dict):
                continue
            text = (it.get("content") or it.get("text") or "").replace("\n", " ")
            print(
                "      {ts}  out={out}  {sender:12} {text}".format(
                    ts=it.get("createTime") or it.get("timestamp"),
                    out=it.get("isSend") if "isSend" in it else it.get("out"),
                    sender=str(it.get("senderName") or it.get("sourceName") or "")[:12],
                    text=text[:70],
                )
            )


def show_astrbot_conversations(limit: int = 3) -> None:
    print("=" * 72)
    print("3) AstrBot data_v4.db conversations（AI 侧记录）")
    db = os.path.join(STACK, "astrbot", "data", "data_v4.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select user_id, title, updated_at, content from conversations order by updated_at desc"
        ).fetchall()
        for user_id, title, updated, content in rows:
            try:
                msgs = json.loads(content)
            except Exception:  # noqa: BLE001
                msgs = []
            if isinstance(msgs, dict):
                msgs = msgs.get("messages") or []
            texts = []
            for m in msgs if isinstance(msgs, list) else []:
                if not isinstance(m, dict):
                    continue
                body = m.get("content")
                if isinstance(body, list):  # 多模态
                    body = " ".join(
                        str(p.get("text") or p.get("image_url", {}).get("url") or "")
                        for p in body
                        if isinstance(p, dict)
                    )
                texts.append(f"{m.get('role', '?')}:{str(body)[:60]}")
            print(f"   {user_id}  title={title!r}  updated={updated}  msgs={len(msgs)}")
            for line in texts[-limit:]:
                print(f"      {line}")
    finally:
        con.close()


def show_companions() -> None:
    print("=" * 72)
    print("4) companions.json 用户档案（身份戳 / 昵称 / 主动投递）")
    path = os.path.join(
        STACK,
        "astrbot",
        "data",
        "plugin_data",
        "astrbot_plugin_private_companion",
        "companions.json",
    )
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    users = blob.get("users") or {}
    for key, rec in users.items():
        if not isinstance(rec, dict):
            continue
        if key not in {str(v) for v in CANON.values()} and not any(
            str(v) in json.dumps(rec, ensure_ascii=False) for v in CANON.values()
        ):
            continue
        nick = rec.get("nickname") or rec.get("display_name") or rec.get("name")
        print(f"   key={key}  nick={nick!r}")
        for field in (
            "identity_subject_id",
            "identity_bot_id",
            "identity_platform",
            "identity_adapter",
            "umo",
            "last_inbound_umo",
            "last_proactive_delivery_umo",
            "preferred_delivery_umo",
            "proactive_private_enabled",
            "alias_user_ids",
        ):
            if field in rec:
                print(f"      {field} = {json.dumps(rec[field], ensure_ascii=False)[:120]}")
    print("   —— umo_aliases (auto_name) ——")
    db = os.path.join(STACK, "astrbot", "data", "data_v4.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for umo, auto, alias, creator in con.execute(
            "select umo, auto_name, user_alias, creator_sender_id from umo_aliases"
        ).fetchall():
            print(f"      {umo:38} auto={auto!r:16} alias={alias!r:16} creator={creator}")
    finally:
        con.close()


def show_reminder_ids() -> None:
    """扫会话内容里的 <system_reminder>User ID: N, Nickname: X</system_reminder>，
    看每个 umo 下出现过哪些历史 id / 昵称——这是判断「谁被归给了谁」的直接证据。"""
    import collections
    import re

    print("=" * 72)
    print("5) 会话内容里出现过的 User ID / Nickname（谁被归给了谁）")
    db = os.path.join(STACK, "astrbot", "data", "data_v4.db")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select user_id, updated_at, content from conversations order by updated_at desc"
        ).fetchall()
        for user_id, updated, content in rows:
            ids = collections.Counter(re.findall(r"User ID:\s*(\d+)", content or ""))
            nicks = collections.Counter(
                n.strip() for n in re.findall(r"Nickname:\s*([^\s<,，]+)", content or "")
            )
            if not ids and not nicks:
                continue
            print(f"   {user_id}  updated={updated}")
            if ids:
                print(f"      User ID: {dict(ids)}")
            if nicks:
                print(f"      Nickname: {dict(nicks)}")
    finally:
        con.close()


SECTIONS = {
    "sessions": show_sessions,
    "wechat": show_wechat_messages,
    "astrbot": show_astrbot_conversations,
    "companions": show_companions,
    "ids": show_reminder_ids,
}


def main() -> int:
    try:  # Windows 控制台默认 GBK，遇到 emoji/特殊符号会炸
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    wanted = sys.argv[1:] or list(SECTIONS)
    for name in wanted:
        fn = SECTIONS.get(name)
        if fn is None:
            print(f"未知小节: {name}（可选: {', '.join(SECTIONS)}）")
            return 2
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            print(f"!! {name} 失败: {exc!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
