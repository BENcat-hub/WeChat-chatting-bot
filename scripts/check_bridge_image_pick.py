#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bridge 取图选择逻辑的离线自检（不连服务、不发消息）。

覆盖 `WeFlowBridge._pick_media_message` / `_query_messages_with_media` 的选择规则：
- 优先"别人发来的"带 mediaUrl 的消息（isSend 为假），而不是机器人自己发的图；
- 全都没有 mediaUrl 时返回 None（触发调用方重试/降级）。
"""
import sys

sys.path.insert(0, r"<REPO_ROOT>\akasha-bridge")

from bridge_core import WeFlowBridge  # noqa: E402

CASES = [
    (
        "只有对方发的图有 mediaUrl",
        [
            {"localId": 152, "isSend": 0, "mediaType": "image", "mediaUrl": "http://x/a.png"},
            {"localId": 151, "isSend": 1, "content": "文字", "mediaType": None, "mediaUrl": None},
        ],
        "http://x/a.png",
    ),
    (
        "对方的图没导出来、自己的图有（旧代码会误取自己的）",
        [
            {"localId": 160, "isSend": 0, "mediaType": "image", "mediaUrl": None},
            {"localId": 159, "isSend": 1, "mediaType": "image", "mediaUrl": "http://x/old.png"},
        ],
        "http://x/old.png",
    ),
    ("都没有", [{"localId": 161, "isSend": 0, "mediaType": "image", "mediaUrl": None}], None),
    ("空列表", [], None),
    (
        "表情包也算媒体",
        [{"localId": 162, "isSend": 0, "mediaType": "sticker", "mediaUrl": "http://x/s.gif"}],
        "http://x/s.gif",
    ),
]

failed = 0
for name, messages, expected in CASES:
    got = WeFlowBridge._pick_media_message(messages)
    actual = (got or {}).get("mediaUrl")
    ok = actual == expected
    failed += 0 if ok else 1
    print(f"[{'OK ' if ok else 'FAIL'}] {name}: got={actual!r} expected={expected!r}")

print(f"\n{'全部通过' if not failed else f'{failed} 例失败'}（共 {len(CASES)} 例）")
sys.exit(1 if failed else 0)
