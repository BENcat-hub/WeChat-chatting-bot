# -*- coding: utf-8 -*-
"""把陪伴插件生图端点尺寸改成 4693x3520（4K 竖幅）。

之前 photo_action_config 的端点 size 仍是 1024x1024，导致
「在线图片 API 参考图提交: size=1024x1024」，即使用人设图也只出 1024 方图。
"""
import io
import json
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PC = os.path.join(ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json")
SIZE = "4693x3520"

with io.open(PC, encoding="utf-8-sig") as f:
    cfg = json.load(f)

targets = []
targets.append(cfg)
pa = cfg.get("photo_action_config")
if isinstance(pa, dict):
    targets.append(pa)

for tgt in targets:
    if "external_image_api_size" in tgt:
        print("root/external_image_api_size:", tgt.get("external_image_api_size"), "->", SIZE)
        tgt["external_image_api_size"] = SIZE
    for key in ("EXTERNAL_IMAGE_API_ENDPOINTS", "external_image_api_endpoints"):
        eps = tgt.get(key)
        if isinstance(eps, list):
            for ep in eps:
                if isinstance(ep, dict):
                    print(f"{key} [{ep.get('name')}] size:", ep.get("size"), "->", SIZE)
                    ep["size"] = SIZE

bak = f"{PC}.bak-size4k-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PC, bak)
with io.open(PC, "w", encoding="utf-8-sig", newline="\n") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("written ->", os.path.basename(PC), "backup ->", os.path.basename(bak))
