"""打印 WeFlow 某会话最近若干条消息的媒体字段（验证图片能否导出）。

用法：
  python weflow_last_messages.py <talker/wxid> [limit]
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

TOKEN = "<FILL_ME_WEFLOW_TOKEN>"
BASE = "http://127.0.0.1:5031"


def main() -> int:
    talker = sys.argv[1] if len(sys.argv) > 1 else "wxid_CONTACT_B"
    limit = sys.argv[2] if len(sys.argv) > 2 else "8"
    query = urllib.parse.urlencode(
        {
            "access_token": TOKEN,
            "talker": talker,
            "media": "true",
            "limit": limit,
        }
    )
    with urllib.request.urlopen(f"{BASE}/api/v1/messages?{query}", timeout=30) as resp:
        data = json.load(resp)
    messages = data.get("messages") or []
    print(f"talker={talker} count={len(messages)} keys={sorted(data.keys())}")
    for item in messages:
        print("-" * 60)
        print(
            "localId={localId} isSend={isSend} type={localType} time={createTime}".format(
                **{k: item.get(k) for k in ("localId", "isSend", "localType", "createTime")}
            )
        )
        print(f"  content={str(item.get('content'))[:60]!r}")
        print(f"  mediaType={item.get('mediaType')!r} mediaFileName={item.get('mediaFileName')!r}")
        print(f"  mediaUrl={item.get('mediaUrl')!r}")
        print(f"  mediaLocalPath={item.get('mediaLocalPath')!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
