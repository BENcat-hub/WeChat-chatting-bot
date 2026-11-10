# -*- coding: utf-8 -*-
"""把人设图写进 image_companion 的规范参考图目录（photo_reference_catalog）。

背景：image_runtime.__init__ 里 load_catalog(legacy_persona=self.photo_persona_reference_image_path)，
但 mixin __init__ 执行时该属性尚未从 owner 复制过来（getattr 返回 ""），
所以「旧配置迁移」拿不到人设路径 → catalog 变成空元组 → reference=False。
修法：直接写一条通过 validate_and_serialize 的规范条目进配置。

用法：python fix_persona_catalog.py [--apply]
"""
from __future__ import annotations

import dataclasses
import io
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "astrbot", "data", "plugins")
sys.path.insert(0, PLUGINS)

from astrbot_plugin_image_companion import photo_reference_catalog as prc  # noqa: E402

CFG = os.path.join(
    ROOT, "astrbot", "data", "config", "astrbot_plugin_image_companion_config.json"
)
PC_CFG = os.path.join(
    ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json"
)
PERSONA = os.path.join(
    ROOT,
    "astrbot",
    "data",
    "plugin_data",
    "astrbot_plugin_private_companion",
    "persona",
    "纳西妲_人设.jpg",
)

apply = "--apply" in sys.argv

with io.open(CFG, encoding="utf-8-sig") as f:
    cfg = json.load(f)
img = cfg.get("image")
if not isinstance(img, dict):
    img = cfg

preset_names = set((img.get("photo_generation_scene_presets") or {}).keys())

# 用官方迁移逻辑构造参考对象，保证字段齐全
refs = prc._migrate_legacy_catalog(PERSONA, [], preset_names, [])
if not refs:
    print("!! 迁移失败：人设图路径无法识别", PERSONA)
    raise SystemExit(2)
ref = refs[0]
print("migrated:", ref.id, ref.kind, ref.reference_roles, ref.source)

entry = dataclasses.asdict(ref)
# matching_only 要求场景/时段命中；人设图应当在任何自拍/人像场景都能兜底
entry["selection_eligibility"] = "fallback_allowed"
payload = [entry]
serialized = prc.validate_and_serialize(payload, preset_names=preset_names)
if len(serialized) != len(payload):
    print("!! validate_and_serialize 丢条目", len(payload), len(serialized))
    raise SystemExit(2)
print("canonical entry:")
print(json.dumps(serialized, ensure_ascii=False, indent=2))

if not apply:
    print("\n(dry-run，加 --apply 才写)")
    raise SystemExit(0)

backup = f"{CFG}.bak-personacat-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(CFG, backup)
img["photo_reference_catalog"] = serialized
img["photo_persona_reference_image_path"] = PERSONA
img["enable_photo_reference_image"] = True
with io.open(CFG, "w", encoding="utf-8-sig", newline="\n") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("written ->", CFG, "backup ->", os.path.basename(backup))

# private_companion 自己的 photo_reference_catalog 也要有：image_runtime 在
# service 未提供该键时回落 owner（private_companion）的属性，
# 而 owner 的 catalog 若仍是 [] 会再次丢掉人设图。
with io.open(PC_CFG, encoding="utf-8-sig") as f:
    pc = json.load(f)
pc_backup = f"{PC_CFG}.bak-personacat-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PC_CFG, pc_backup)
targets = [pc]
pa = pc.get("photo_action_config")
if isinstance(pa, dict):
    targets.append(pa)
for tgt in targets:
    tgt["photo_reference_catalog"] = serialized
    tgt["photo_persona_reference_image_path"] = PERSONA
    tgt["enable_photo_reference_image"] = True
with io.open(PC_CFG, "w", encoding="utf-8-sig", newline="\n") as f:
    json.dump(pc, f, ensure_ascii=False, indent=2)
    f.write("\n")
print("written ->", PC_CFG, "backup ->", os.path.basename(pc_backup))
