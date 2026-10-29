# -*- coding: utf-8 -*-
"""只读验证：名字 → talker(wxid) 能不能解析出来。不发送任何消息。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wxui  # noqa: E402

cfg = wxui.config()
sessions = wxui.api(cfg, "/api/v1/sessions", limit=300).get("sessions", [])
print("RESULT sessions=%d" % len(sessions))
for name in ("用户F别名", "用户F", "用户G别名", "用户G", "用户H别名", "用户H"):
    print("RESULT %-6s -> %s" % (name, wxui.talker_for(cfg, name, sessions)))
