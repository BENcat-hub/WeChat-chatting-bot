# -*- coding: utf-8 -*-
"""把纳西妲人设图接成生图参考图（image_companion + private_companion 两处）。"""
import io, json, os, shutil, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "astrbot", "data")
PERSONA = os.path.join(DATA, "plugin_data", "astrbot_plugin_private_companion",
                       "persona", "纳西妲_人设.jpg")
assert os.path.isfile(PERSONA), PERSONA
stamp = time.strftime("%Y%m%d-%H%M%S")


def load(path):
    return json.load(io.open(path, encoding="utf-8-sig"))


def save(path, obj):
    bak = path + ".bak-personaref-" + stamp
    shutil.copy2(path, bak)
    io.open(path, "w", encoding="utf-8-sig", newline="").write(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    print("saved", os.path.basename(path), "backup", os.path.basename(bak))


# 1) image_companion（「我会画给你看」）：开参考图 + 指人设 + 风格改二次元
p1 = os.path.join(DATA, "config", "astrbot_plugin_image_companion_config.json")
c1 = load(p1)
img = c1.setdefault("image", {})
img["enable_photo_reference_image"] = True
img["photo_persona_reference_image_path"] = PERSONA
img["photo_generation_style"] = "二次元"
save(p1, c1)

# 2) private_companion：root 与 photo_action_config 都指同一张人设图
p2 = os.path.join(DATA, "config", "astrbot_plugin_private_companion_config.json")
c2 = load(p2)
c2["enable_photo_reference_image"] = True
c2["photo_persona_reference_image_path"] = PERSONA
pac = c2.get("photo_action_config")
if isinstance(pac, dict):
    pac["enable_photo_reference_image"] = True
    pac["photo_persona_reference_image_path"] = PERSONA
save(p2, c2)
print("persona =", PERSONA)
