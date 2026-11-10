import base64, hashlib, hmac, json, time, urllib.request, io
cfg = json.load(io.open("astrbot/data/cmd_config.json", encoding="utf-8-sig"))
secret = cfg["dashboard"]["jwt_secret"]
b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
h = b64(json.dumps({"alg":"HS256","typ":"JWT"},separators=(",",":")).encode())
p = b64(json.dumps({"username":"astrbot","exp":int(time.time())+600},separators=(",",":")).encode())
t = (h+b"."+p+b"."+b64(hmac.new(secret.encode(), h+b"."+p, hashlib.sha256).digest())).decode()
req = urllib.request.Request("http://127.0.0.1:6185/api/plug/astrbot_plugin_private_companion/page/overview")
req.add_header("Authorization","Bearer "+t)
d = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
s = d.get("data",{}).get("settings",{})
for k in ("external_image_api_size","photo_generation_prompt_format","photo_generation_style",
          "photo_generation_negative_prompt_mode","photo_generation_selfie_fixed_prompt"):
    print(k, "=", repr(s.get(k))[:120])
