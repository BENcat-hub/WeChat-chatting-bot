# -*- coding: utf-8 -*-
"""生图改回 1024x1024，并把提示词结构改成「固定身份锚 + 明确负面词」。

用法：python fix_photo_prompt.py [--apply]
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_CFG = os.path.join(ROOT, "astrbot", "data", "config", "astrbot_plugin_image_companion_config.json")
PC_CFG = os.path.join(ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json")
SIZE = "1024x1024"
STAMP = time.strftime("%Y%m%d-%H%M%S")

IDENTITY = (
    "画面里的女孩始终是同一个人：及腰的蓝色渐变长发（发根深蓝、发梢浅蓝）"
    "，耳侧各有一枚扇形鲸鳍状发饰，天蓝色大眼睛，皮肤白皙，身形纤细"
)
STYLE = "日系二次元插画质感，线条干净，柔和自然光，生活照构图，人物五官比例正常"

SELFIE_POSITIVE = (
    f"positive prompt: {IDENTITY}；穿当天的日常私服（卫衣或针织衫配半身裙、短袜、便鞋），"
    f"自然站姿或随手自拍的角度，背景是真实生活场景（宿舍走廊、房间、电梯厅、街边）；{STYLE}"
)
NEGATIVE = (
    "negative prompt: 动物, 宠物, 玩偶, 猫, 狗, 鲸鱼, 尾巴, 多余的动物配件, "
    "多余人物, 背景路人, 文字, 水印, logo, 字幕, 写实照片质感, 3D渲染, "
    "畸形手指, 多余肢体, 面部畸变, 过度磨皮, 露骨裸露"
)

updates = {
    "external_image_api_size": SIZE,
    "photo_generation_prompt_format": "traditional",
    "photo_generation_style": "二次元",
    "photo_generation_negative_prompt_mode": "merge",
    "photo_generation_negative_prompt": NEGATIVE,
    "photo_generation_selfie_negative_prompt": NEGATIVE,
    "photo_generation_text2img_negative_prompt": NEGATIVE,
    "photo_generation_edit_negative_prompt": NEGATIVE,
    "photo_generation_fixed_prompt": SELFIE_POSITIVE,
    "photo_generation_selfie_fixed_prompt": SELFIE_POSITIVE,
    "photo_generation_text2img_fixed_prompt": "",
    "photo_generation_edit_fixed_prompt": (
        "positive prompt: 保留参考图里人物的脸型, 发色渐变, 鲸鳍发饰与整体配色；"
        f"只改动服装/场景/动作；{STYLE}\n{NEGATIVE}"
    ),
}

apply = "--apply" in sys.argv


def patch(path: str, sections: list[dict]) -> None:
    with io.open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    changed = []
    for sect in sections:
        for key, value in updates.items():
            if key.endswith("_size") and key != "external_image_api_size":
                continue
            if key in sect or key in {
                "photo_generation_prompt_format",
                "photo_generation_style",
                "photo_generation_negative_prompt_mode",
                "photo_generation_negative_prompt",
                "photo_generation_selfie_negative_prompt",
                "photo_generation_selfie_fixed_prompt",
                "photo_generation_fixed_prompt",
                "external_image_api_size",
            }:
                if sect.get(key) != value:
                    changed.append(key)
                    if apply:
                        sect[key] = value
        # 端点尺寸
        for key in ("EXTERNAL_IMAGE_API_ENDPOINTS", "external_image_api_endpoints"):
            eps = sect.get(key)
            if isinstance(eps, list):
                for ep in eps:
                    if isinstance(ep, dict) and ep.get("size") != SIZE:
                        changed.append(f"{key}[{ep.get('name')}].size")
                        if apply:
                            ep["size"] = SIZE
    print(f"== {os.path.basename(path)} 待改 {len(changed)} 项: {sorted(set(changed))[:12]}")
    if apply and changed:
        bak = f"{path}.bak-prompt-{STAMP}"
        shutil.copy2(path, bak)
        with io.open(path, "w", encoding="utf-8-sig", newline="\n") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("   written, backup ->", os.path.basename(bak))


with io.open(IMG_CFG, encoding="utf-8-sig") as f:
    img_cfg = json.load(f)
with io.open(PC_CFG, encoding="utf-8-sig") as f:
    pc_cfg = json.load(f)

print("selfie fixed prompt 长度:", len(SELFIE_POSITIVE), " negative:", len(NEGATIVE))
print()

# 1) image_companion: image 段
secs = [img_cfg["image"]] if isinstance(img_cfg.get("image"), dict) else [img_cfg]
patch(IMG_CFG, secs)

# 2) private_companion: 根 + photo_action_config
secs2 = [pc_cfg]
if isinstance(pc_cfg.get("photo_action_config"), dict):
    secs2.append(pc_cfg["photo_action_config"])
patch(PC_CFG, secs2)

if not apply:
    print("\n(dry-run，加 --apply 才写)")
