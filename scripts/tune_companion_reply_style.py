# -*- coding: utf-8 -*-
"""把「回复风格约束」同时写进插件的扁平 legacy 键与新版分组键。

背景：astrbot_plugin_private_companion 6.7.0 启动时会执行
migrate_flat_config_into_schema_groups()，把旧扁平键迁进分组配置；
运行时读的是分组键（basic_config.reply_style_prompt），
只改扁平键会在插件重载时被分组值覆盖回去。
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

sys.stdout.reconfigure(encoding="utf-8")
import jwt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "astrbot", "data", "cmd_config.json")
PLUGIN_CFG = os.path.join(ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json")
LOGS = os.path.join(ROOT, "logs")
BASE = "http://127.0.0.1:6185"
PLUGIN = "astrbot_plugin_private_companion"

OLD_STYLE = (
    "每次回复至多三句话；简单的回答尽可能保持在 1~2 句，并尽可能保持口语化与简洁化。"
    "回复长度和语气应该与当前对话节奏相匹配，必须使用简体中文进行回复，话语符合社交媒体交流习惯。"
    "当你需要解决复杂问题或进行详细说明时，可以无视这一要求。"
)
NEW_STYLE = (
    "每次回复至多三句话；简单的回答尽可能保持在 1~2 句，并尽可能保持口语化与简洁化。"
    "回复长度和语气应该与当前对话节奏相匹配，必须使用简体中文进行回复，话语符合社交媒体交流习惯。"
    "不要使用 emoji 或颜文字；不要输出用星号或括号包裹的动作、神态描写（例如 *歪头*、（笑））；"
    "不要用 markdown、编号、列表、加粗；不要使用敬语和客服腔（例如「您好」「请问」「很高兴为您服务」「有什么可以帮你的」）。"
    "当你需要解决复杂问题或进行详细说明时，可以无视这一要求。"
)

cfg = json.load(open(CFG, encoding="utf-8-sig"))
tok = jwt.encode(
    {"username": "astrbot", "exp": int(time.time()) + 1800},
    cfg["dashboard"]["jwt_secret"],
    algorithm="HS256",
)
tok = tok.decode() if isinstance(tok, bytes) else tok


def req(method, path, body=None, timeout=180):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as f:
            return f.status, f.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


HITS = []


def transform(o, path=""):
    """返回替换后的对象；同时把改动记录进 HITS。"""
    if isinstance(o, dict):
        return {k: transform(v, f"{path}.{k}" if path else str(k)) for k, v in o.items()}
    if isinstance(o, list):
        return [transform(v, f"{path}[{i}]") for i, v in enumerate(o)]
    if isinstance(o, str):
        new = o
        if OLD_STYLE in o:
            new = new.replace(OLD_STYLE, NEW_STYLE)
        if "姓名：用户E" in new and "生日：16" in new:
            new = new.replace("生日：16", "年龄：16")
        if new != o:
            HITS.append((path, len(o), len(new)))
        return new
    return o


st, b = req("GET", "/api/v1/plugins/config?" + urllib.parse.urlencode({"plugin_id": PLUGIN}))
d = json.loads(b)
conf = d.get("data", {})
if isinstance(conf, dict) and isinstance(conf.get("config"), dict):
    conf = conf["config"]
print("[GET]", st, "top keys =", len(conf))
snap = os.path.join(LOGS, f"pc_config.before-style-{time.strftime('%Y%m%d-%H%M%S')}.json")
json.dump(conf, open(snap, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("[backup]", os.path.basename(snap))

new_conf = transform(conf)
for p, a, c in HITS:
    print(f"[patch] {p}: {a} -> {c} chars")
if not HITS:
    print("[patch] 没有命中任何待改字段")

st, b = req("PUT", "/api/v1/plugins/config", {"plugin_id": PLUGIN, "config": new_conf})
print("[PUT]", st, b[:200])
time.sleep(10)  # 等插件热重载与落盘

ondisk = json.load(open(PLUGIN_CFG, encoding="utf-8-sig"))
flat = ondisk.get("reply_style_prompt") or ""
group = (ondisk.get("basic_config") or {}).get("reply_style_prompt") or ""
sched = ((ondisk.get("basic_config") or {}).get("schedule_persona_prompt")) or ondisk.get("schedule_persona_prompt") or ""
print("[disk flat ] emoji 规则 =", "不要使用 emoji" in flat, "| len", len(flat))
print("[disk group] emoji 规则 =", "不要使用 emoji" in group, "| len", len(group))
print("[disk sched] =", [l for l in sched.splitlines() if l.startswith(("年龄", "生日", "种族"))])
