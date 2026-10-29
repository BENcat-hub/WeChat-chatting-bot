import json, urllib.parse, urllib.request, sys
sys.path.insert(0, ".")
import config
base = config.WE_FLOW_BASE_URL.rstrip("/"); tok = config.ACCESS_TOKEN
def get(path, **params):
    params["access_token"] = tok
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
s = get("/api/v1/sessions")
sessions = s.get("sessions") if isinstance(s, dict) else s
found = 0
for item in sessions:
    un = item.get("username")
    if not un: continue
    for lim in (200,):
        try:
            d = get("/api/v1/messages", talker=un, media="true", voice="true", limit=lim)
        except Exception as e:
            print("ERR", un, e); continue
        msgs = d.get("messages") if isinstance(d, dict) else d
        voices = [m for m in (msgs or []) if m.get("localType") == 34]
        if voices:
            print("SESSION", item.get("displayName"), un, "voices", len(voices))
            m = voices[-1]
            print("  sample:", json.dumps({k: m.get(k) for k in ("localId","content","mediaType","mediaFileName","mediaUrl","isSend","createTime")}, ensure_ascii=False)[:500])
            found += 1
            break
print("sessions-with-voice", found)
