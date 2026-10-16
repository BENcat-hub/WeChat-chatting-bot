# 决定性探针：给 AstrBot 的两个 deepseek 通道各发一张「已知内容」的图，看它是否真能读图
import base64
import json
import os
import sys
import urllib.error
import urllib.request

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(W, "logs", "_vision_probe.png")

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception as e:  # pragma: no cover
    print("PIL 不可用:", e)
    raise SystemExit(1)

# 画一张白底、巨大黑字「7」和一个红点
im = Image.new("RGB", (320, 320), "white")
d = ImageDraw.Draw(im)
font = None
for cand in (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
):
    if os.path.exists(cand):
        try:
            font = ImageFont.truetype(cand, 260)
            break
        except Exception:
            pass
if font is None:
    font = ImageFont.load_default()
d.text((90, 10), "7", fill="black", font=font)
d.ellipse((20, 250, 70, 300), fill="red")
im.save(IMG)
print("测试图:", IMG)

data_url = "data:image/png;base64," + base64.b64encode(open(IMG, "rb").read()).decode()

CFG = json.load(open(os.path.join(W, "astrbot", "data", "cmd_config.json"), encoding="utf-8-sig"))
sources = {s["id"]: s for s in (CFG.get("provider_sources") or []) if isinstance(s, dict) and s.get("id")}

for pid in ("deepseek", "deepseek-v4-pro"):
    prov = next((p for p in (CFG.get("provider") or []) if p.get("id") == pid), None)
    if not prov:
        print("!! 找不到 provider", pid)
        continue
    src = sources.get(prov.get("provider_source_id")) or {}
    base = str(src.get("api_base") or "").rstrip("/")
    key = src.get("key")
    if isinstance(key, list):
        key = next((os.environ.get(str(k).lstrip("$"), "") for k in key if os.environ.get(str(k).lstrip("$"))), "")
    print("\n===== %s (model=%s, base=%s, key=%s) =====" % (pid, prov.get("model"), base, "有" if key else "无"))

    body = {
        "model": prov.get("model"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图里有哪个数字？还有一个什么颜色的圆点？用一行回答，例如：数字X + Y色圆点"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 300,
        "temperature": 0,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode("utf-8", "replace")
        j = json.loads(raw)
        ch = (j.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        print("finish_reason:", ch.get("finish_reason"))
        print("content     :", repr(str(msg.get("content"))[:400]))
        rc = msg.get("reasoning_content")
        if rc:
            print("reasoning   :", repr(str(rc)[:300]))
        print("usage       :", j.get("usage"))
    except urllib.error.HTTPError as e:
        print("HTTPError", e.code, e.read().decode("utf-8", "replace")[:400])
    except Exception as e:
        print("失败:", type(e).__name__, e)
