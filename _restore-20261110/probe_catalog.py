# -*- coding: utf-8 -*-
"""探针：复现 image_companion 的 photo_reference_catalog 加载，弄清人设图为何被丢弃。"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "astrbot", "data", "plugins")
sys.path.insert(0, PLUGINS)

from astrbot_plugin_image_companion import photo_reference_catalog as prc  # noqa: E402
from astrbot_plugin_image_companion import image_runtime as ir  # noqa: E402

cfg_path = os.path.join(ROOT, "astrbot", "data", "config", "astrbot_plugin_image_companion_config.json")
cfg = json.load(io.open(cfg_path, encoding="utf-8-sig"))
img = cfg.get("image", cfg)

raw_catalog = img.get("photo_reference_catalog", "<MISSING>")
legacy_persona = img.get("photo_persona_reference_image_path", "")
presets = (img.get("photo_generation_scene_presets") or {})
preset_names = presets.keys() if isinstance(presets, dict) else ()

print("CATALOG_VERSION =", getattr(ir, "CATALOG_VERSION", "?"))
print("raw_catalog =", repr(raw_catalog))
print("legacy_persona =", repr(legacy_persona))
print("legacy_persona exists =", os.path.isfile(legacy_persona))
print("preset_names =", list(preset_names))
print("strictly_persistable([]) =", prc._canonical_catalog_is_strictly_persistable(raw_catalog, set(preset_names)))

res = prc.load_catalog(
    raw_catalog if raw_catalog != "<MISSING>" else None,
    catalog_version=getattr(ir, "CATALOG_VERSION", 2),
    legacy_persona=legacy_persona,
    legacy_library=img.get("photo_reference_library", []),
    user_cleared=False,
    preset_names=preset_names,
)
print("references =", len(res.references))
for item in res.references:
    print("  ", item.id, item.kind, item.reference_roles, item.source)
print("needs_persist =", res.needs_persist)
print("warnings =", res.warnings)
print("read_only =", getattr(res, "read_only", None))
