# -*- coding: utf-8 -*-
"""一次性诊断：在 _photo_persona_reference_image_path 入口打日志（PC_REF_DEBUG=1 才输出）。"""
import io
import os
import shutil
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_image_companion", "image_runtime.py")

with io.open(P, encoding="utf-8") as f:
    src = f.read()

if "[refdbg]" in src:
    print("already patched")
    raise SystemExit(0)

anchor = "    def _photo_persona_reference_image_path(self) -> str:\n"
assert anchor in src, "anchor not found"
inject = anchor + (
    "        if os.environ.get(\"PC_REF_DEBUG\"):\n"
    "            _cat = getattr(self, \"photo_reference_catalog\", \"<NOATTR>\")\n"
    "            logger.info(\n"
    "                \"[refdbg] enable=%r catalog_type=%s catalog_len=%r kinds=%r persona_attr=%r from_service=%r\",\n"
    "                bool(getattr(self, \"enable_photo_reference_image\", None)),\n"
    "                type(_cat).__name__,\n"
    "                (len(_cat) if hasattr(_cat, \"__len__\") else None),\n"
    "                [getattr(i, \"kind\", i) for i in (_cat or ())],\n"
    "                str(getattr(self, \"photo_persona_reference_image_path\", \"\"))[:120],\n"
    "                bool(getattr(self, \"_photo_reference_catalog_from_image_service\", None)),\n"
    "            )\n"
)
src = src.replace(anchor, inject, 1)
bak = P + ".bak-refdbg-" + time.strftime("%Y%m%d-%H%M%S")
shutil.copy2(P, bak)
with io.open(P, "w", encoding="utf-8", newline="\n") as f:
    f.write(src)
print("patched", os.path.basename(bak))
