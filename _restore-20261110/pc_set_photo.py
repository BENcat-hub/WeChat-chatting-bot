# -*- coding: utf-8 -*-
"""通过面板 API 写入生图提示词/尺寸设置（避免 AstrBot 启动时回写覆盖）。"""
import base64, hashlib, hmac, json, time, urllib.request, io

CFG = "astrbot/data/cmd_config.json"
cfg = json.load(io.open(CFG, encoding="utf-8-sig"))
secret = cfg["dashboard"]["jwt_secret"]

def b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=")
def token(exp=3600):
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = b64(json.dumps({"username": "astrbot", "exp": int(time.time()) + exp}, separators=(",", ":")).encode())
    msg = header + b"." + payload
    sig = b64(hmac.new(secret.encode(), msg, hashlib.sha256).digest())
    return (msg + b"." + sig).decode()

SETTINGS = {
    "external_image_api_size": "1024x1024",
    "photo_generation_prompt_format": "traditional",
    "photo_generation_style": "二次元",
    "photo_generation_negative_prompt_mode": "merge",
    "photo_generation_negative_prompt": "negative prompt: 动物, 宠物, 玩偶, 猫, 狗, 鲸鱼, 尾巴, 多余的动物配件, 多余人物, 背景路人, 文字, 水印, logo, 字幕, 写实照片质感, 3D渲染, 畸形手指, 多余肢体, 面部畸变, 过度磨皮, 露骨裸露",
    "photo_generation_selfie_negative_prompt": "negative prompt: 动物, 宠物, 玩偶, 猫, 狗, 鲸鱼, 尾巴, 多余的动物配件, 多余人物, 背景路人, 文字, 水印, logo, 字幕, 写实照片质感, 3D渲染, 畸形手指, 多余肢体, 面部畸变, 过度磨皮, 露骨裸露",
    "photo_generation_text2img_negative_prompt": "negative prompt: 动物, 宠物, 玩偶, 猫, 狗, 鲸鱼, 尾巴, 多余人物, 背景路人, 文字, 水印, logo, 字幕, 写实照片质感, 3D渲染, 畸形手指, 多余肢体",
    "photo_generation_edit_negative_prompt": "negative prompt: 动物, 宠物, 玩偶, 尾巴, 多余人物, 文字, 水印, logo, 畸形手指, 多余肢体",
    "photo_generation_fixed_prompt": "positive prompt: 画面里的女孩始终是同一个人：及腰的蓝色渐变长发（发根深蓝、发梢浅蓝），耳侧各有一枚扇形鲸鳍状发饰，天蓝色大眼睛，皮肤白皙，身形纤细；穿当天的日常私服，自然站姿或随手自拍的角度，背景是真实生活场景；日系二次元插画质感，线条干净，柔和自然光，人物五官比例正常",
    "photo_generation_selfie_fixed_prompt": "positive prompt: 画面里的女孩始终是同一个人：及腰的蓝色渐变长发（发根深蓝、发梢浅蓝），耳侧各有一枚扇形鲸鳍状发饰，天蓝色大眼睛，皮肤白皙，身形纤细；穿当天的日常私服，自然站姿或随手自拍的角度，背景是真实生活场景；日系二次元插画质感，线条干净，柔和自然光，人物五官比例正常",
    "photo_generation_edit_fixed_prompt": "positive prompt: 保留参考图里人物的脸型、发色渐变、鲸鳍发饰与整体配色；只改动服装、场景与动作；日系二次元插画质感，线条干净，柔和自然光",
}

def call(path, body):
    data = json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request("http://127.0.0.1:6185" + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]

print(call("/api/plug/astrbot_plugin_private_companion/page/settings/update", {"settings": SETTINGS}))
