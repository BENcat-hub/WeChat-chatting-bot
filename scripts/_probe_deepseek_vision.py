# 只读探针：确认 AstrBot 用的 deepseek-flash 通道到底能不能吃图片输入
import base64
import json
import os
import urllib.error
import urllib.request

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(W, "astrbot", "data", "cmd_config.json")

cfg = json.load(open(CFG, encoding="utf-8-sig"))


def walk(node, path=""):
    out = []
    if isinstance(node, dict):
        if "api_base" in node or "model" in node:
            out.append((path, node))
        for k, v in node.items():
            out += walk(v, path + "/" + str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += walk(v, path + "[%d]" % i)
    return out


found = walk(cfg)
print("== provider 候选节点 %d ==" % len(found))
for path, node in found:
    print("-", path)
    for k in ("id", "type", "model", "modalities", "api_base", "key", "provider_source_id", "timeout"):
        if k in node:
            v = node[k]
            if k == "key" and isinstance(v, str) and len(v) > 12:
                v = v[:6] + "...(%d)" % len(v)
            print("    %s = %s" % (k, v))

# 解析 deepseek 源
sources = []
def collect(node):
    if isinstance(node, dict):
        if node.get("api_base") and node.get("id"):
            sources.append(node)
        for v in node.values():
            collect(v)
    elif isinstance(node, list):
        for v in node:
            collect(v)
collect(cfg)

target = None
for s in sources:
    if "deepseek" in str(s.get("id", "")).lower():
        target = s
        break
if target is None and sources:
    target = sources[0]
if target is None:
    print("!! 找不到带 api_base 的 provider 源")
    raise SystemExit(0)

base = str(target["api_base"]).rstrip("/")
key = target.get("key")
if isinstance(key, list):
    resolved = ""
    for name in key:
        name = str(name).strip().lstrip("$")
        resolved = os.environ.get(name, "")
        if resolved:
            break
    key = resolved
print("\n== 探测目标: %s (base=%s, key=%s) ==" % (target.get("id"), base, "有" if key else "无"))

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()

body = {
    "model": target.get("model") or "deepseek-flash",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这张图是什么颜色？只回答颜色词。"},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ],
    "max_tokens": 32,
}
req = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % key},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read().decode("utf-8", "replace")
    print("HTTP %d" % r.status)
    try:
        j = json.loads(raw)
        msg = (j.get("choices") or [{}])[0].get("message", {})
        print("模型回答:", str(msg.get("content"))[:300])
    except Exception:
        print("原文:", raw[:600])
except urllib.error.HTTPError as e:
    print("HTTPError %d" % e.code)
    print(e.read().decode("utf-8", "replace")[:600])
except Exception as e:
    print("失败:", type(e).__name__, e)
