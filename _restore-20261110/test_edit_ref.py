# -*- coding: utf-8 -*-
"""用纳西妲人设图做参考图，验证 8790 的 /v1/images/edits -> 火山 image_urls/base64 链路。"""
import base64, io, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "astrbot", "data", "plugin_data",
                   "astrbot_plugin_private_companion", "persona", "纳西妲_人设.jpg")
b64 = base64.b64encode(open(IMG, "rb").read()).decode("ascii")
prompt = ("同一个人物的全身穿搭照：深蓝长发、发梢渐变到浅蓝，鲸鱼鳍状耳饰（蓝色小鲸鱼造型），"
          "蓝眼睛。今天穿白色宽松卫衣 + 深灰百褶短裙 + 白色运动鞋，站在宿舍走廊窗边，"
          "柔和的下午自然光，干净的日系二次元插画风，保持人设图的脸型与发色，完整全身入镜。")
body = {
    "model": "jimeng-4.6",
    "prompt": prompt,
    "n": 1,
    "size": "2304x1728",
    "response_format": "url",
    "image": "data:image/jpeg;base64," + b64,
}
req = urllib.request.Request(
    "http://127.0.0.1:8790/v1/images/edits",
    data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "X-Jimeng-Backend": "volc",
        "X-Jimeng-Model": "jimeng-4.6",
        "X-Jimeng-Deadline-S": "300",
    },
    method="POST",
)
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=330) as resp:
        data = json.loads(resp.read().decode("utf-8"))
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", "replace")[:900])
    sys.exit(1)
print("elapsed %.1fs" % (time.time() - t0))
print(json.dumps(data, ensure_ascii=False)[:600])
url = (data.get("data") or [{}])[0].get("url") or ""
if url.startswith("http://127.0.0.1:8790/"):
    with urllib.request.urlopen(url, timeout=120) as r:
        blob = r.read()
    out = os.path.join(ROOT, "logs", "jimeng-out", "ref-test-" + time.strftime("%H%M%S") + ".png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(blob)
    print("saved", out, len(blob))
