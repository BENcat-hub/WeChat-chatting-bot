# -*- coding: utf-8 -*-
"""用微信原始聊天记录做「地面真值」比对：某个关键词到底是谁说的。

只读脚本。用法：
  akasha-bridge\\.venv\\Scripts\\python.exe scripts\\diag_chat_groundtruth.py 体育 早自习 不想动
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(W, "akasha-bridge"))
import config  # noqa: E402

TALKERS = {
    "用户B": "wxid_CONTACT_C",
    "用户A": "wxid_CONTACT_B",
    "用户C": "wxid_CONTACT_A",
    "用户D": "wxid_CONTACT_D",
}
CST = timedelta(hours=8)


def fetch(talker: str, limit: int = 600) -> list:
    q = urllib.parse.urlencode(
        {"talker": talker, "limit": limit, "access_token": config.ACCESS_TOKEN}
    )
    url = f"{config.WE_FLOW_BASE_URL}/api/v1/messages?{q}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        for key in ("data", "messages", "items", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data


def main() -> None:
    keywords = sys.argv[1:] or ["体育"]
    for who, wxid in TALKERS.items():
        try:
            msgs = fetch(wxid)
        except Exception as exc:  # noqa: BLE001
            print(f"--- {who}: 读取失败 {exc}")
            continue
        print(f"\n=== {who} ({wxid}) 共 {len(msgs)} 条 ===")
        hits = 0
        for m in msgs:
            text = str(m.get("content") or m.get("text") or "")
            if not any(k in text for k in keywords):
                continue
            hits += 1
            ts = m.get("createTime") or m.get("timestamp") or 0
            when = datetime.fromtimestamp(int(ts) + 8 * 3600).strftime("%m-%d %H:%M:%S")
            who_said = "我(小号发出)" if m.get("isSender") or m.get("isSend") else who
            print(f"  [{when}] {who_said}: {text[:160]}")
        if not hits:
            print(f"  （没有命中 {keywords}）")


if __name__ == "__main__":
    main()
