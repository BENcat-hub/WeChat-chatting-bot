"""列出 WeFlow 会话（用于确认 talker/wxid），并显示当前自动下载高清新图白名单。

用法：astrbot 虚拟环境的 python 运行本脚本，无需参数。
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

TOKEN = "<FILL_ME>"
BASE = "http://127.0.0.1:5031"
WEFLOW_CFG = os.path.join(
    os.environ.get("APPDATA", ""), "weflow", "WeFlow-config.json"
)


def main() -> int:
    query = urllib.parse.urlencode({"access_token": TOKEN, "limit": 80})
    with urllib.request.urlopen(f"{BASE}/api/v1/sessions?{query}", timeout=20) as resp:
        data = json.load(resp)
    sessions = data.get("sessions") or []
    print(f"会话数: {len(sessions)}")
    if sessions:
        print("字段:", sorted(sessions[0].keys()))
    for item in sessions:
        username = str(item.get("username") or item.get("talker") or "")
        name = item.get("displayName") or item.get("nickname") or item.get("remark") or ""
        kind = "群" if "@chatroom" in username else "私"
        extra = " ".join(
            f"{key}={item.get(key)}"
            for key in ("lastMessageTime", "updateTime", "unreadCount", "lastMessagePreview")
            if item.get(key) not in (None, "")
        )
        print(f"{kind}  {username}  {name}  {extra[:120]}")

    if os.path.isfile(WEFLOW_CFG):
        with open(WEFLOW_CFG, encoding="utf-8") as handle:
            cfg = json.load(handle)
        print("\nWeFlow 自动下载配置:")
        for key in (
            "autoDownloadHighRes",
            "autoDownloadWhitelist",
            "exportDefaultImageDeepSearchOnMiss",
            "dbPath",
            "myWxid",
        ):
            print(f"  {key} = {cfg.get(key)!r}")
    else:
        print(f"\n未找到 WeFlow 配置: {WEFLOW_CFG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
