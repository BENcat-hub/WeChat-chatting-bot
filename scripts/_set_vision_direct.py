# 把「私聊图片视觉」从「单独模型转文字（caption）」切成「主对话模型直接看图（direct）」
# 依据：private_image.py:1433 只挑 _provider_supports_image() 为真的 provider；
#       _private_image_delivery_mode()：has_visual_provider=True -> caption，否则主模型支持图片 -> direct。
# 所以把 PLUGIN_VISION_PROVIDER_ID 清空，让主模型（deepseek-flash，实测能读图）直接吃图片。
import argparse
import json
import os
import shutil
import time

W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(W, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json")

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
args = ap.parse_args()

bom = open(CFG, "rb").read()[:3] == b"\xef\xbb\xbf"
cfg = json.load(open(CFG, encoding="utf-8-sig"))
print("配置:", CFG)
print("BOM:", bom)
print("model_fallback_overrides:", json.dumps(cfg.get("model_fallback_overrides"), ensure_ascii=False))

hits = []


def walk(node, path):
    if isinstance(node, dict):
        for k, v in list(node.items()):
            p = path + "/" + str(k)
            if k == "PLUGIN_VISION_PROVIDER_ID":
                hits.append((p, v))
            walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            walk(v, path + "[%d]" % i)


walk(cfg, "$")
print("PLUGIN_VISION_PROVIDER_ID 出现 %d 处:" % len(hits))
for p, v in hits:
    print("   %s = %r" % (p, v))

if not args.apply:
    print("\n(dry-run，未写入；--apply 才生效)")
    raise SystemExit(0)

bak = os.path.join(W, "logs", "backup-vision-direct-" + time.strftime("%Y%m%d-%H%M%S"))
os.makedirs(bak, exist_ok=True)
shutil.copy2(CFG, os.path.join(bak, os.path.basename(CFG)))
print("备份:", bak)

changed = 0


def walk_set(node):
    global changed
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k == "PLUGIN_VISION_PROVIDER_ID" and v != "":
                node[k] = ""
                changed += 1
            else:
                walk_set(v)
    elif isinstance(node, list):
        for v in node:
            walk_set(v)


walk_set(cfg)
with open(CFG, "w", encoding=("utf-8-sig" if bom else "utf-8"), newline="\n") as fh:
    json.dump(cfg, fh, ensure_ascii=False, indent=4)
print("改写 %d 处；PLUGIN_VISION_PROVIDER_ID 现为 \"\"（主模型直读图片）" % changed)
