# -*- coding: utf-8 -*-
"""把外部生图端点尺寸改成 1024x1024（走面板 API，避免启动回写覆盖）。"""
import base64, hashlib, hmac, json, time, urllib.request, io

cfg = json.load(io.open("astrbot/data/cmd_config.json", encoding="utf-8-sig"))
secret = cfg["dashboard"]["jwt_secret"]
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
p = b64(json.dumps({"username": "astrbot", "exp": int(time.time()) + 600}, separators=(",", ":")).encode())
tok = (h + b"." + p + b"." + b64(hmac.new(secret.encode(), h + b"." + p, hashlib.sha256).digest())).decode()

pc = json.load(io.open("astrbot/data/config/astrbot_plugin_private_companion_config.json", encoding="utf-8-sig"))
eps = [dict(e) for e in (pc.get("external_image_api_endpoints") or []) if isinstance(e, dict)]
for e in eps:
    e["size"] = "1024x1024"
print("endpoints:", [(e.get("name"), e.get("size")) for e in eps])

body = {"settings": {"external_image_api_endpoints": eps, "external_image_api_size": "1024x1024"}}
data = json.dumps(body, ensure_ascii=False).encode()
req = urllib.request.Request("http://127.0.0.1:6185/api/plug/astrbot_plugin_private_companion/page/settings/update",
                             data=data, method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("Authorization", "Bearer " + tok)
with urllib.request.urlopen(req, timeout=60) as r:
    print(r.status, r.read().decode()[:200])
