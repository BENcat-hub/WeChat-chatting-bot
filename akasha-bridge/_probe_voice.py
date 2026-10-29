import json, urllib.parse, urllib.request, sys
sys.path.insert(0, ".")
import config
base = config.WE_FLOW_BASE_URL.rstrip("/"); tok = config.ACCESS_TOKEN
def get(path, **params):
    params["access_token"] = tok
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
for who in ["wxid_CONTACT_C", "wxid_CONTACT_B", "wxid_CONTACT_D", "wxid_CONTACT_A"]:
    try:
        d = get("/api/v1/messages", talker=who, media="true", limit=60)
    except Exception as e:
        print(who, "ERR", e); continue
    msgs = d.get("messages") if isinstance(d, dict) else d
    kinds = {}
    for m in msgs or []:
        kinds[m.get("localType")] = kinds.get(m.get("localType"), 0) + 1
    print(who, "n=", len(msgs or []), "localTypes", kinds)
    for m in (msgs or []):
        if m.get("localType") == 34:
            print("   VOICE:", json.dumps({k: m.get(k) for k in ("localId","localType","content","mediaType","mediaUrl","duration","isSend","createTime")}, ensure_ascii=False)[:400])
            break
