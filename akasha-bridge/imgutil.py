"""图片格式兜底：把微信显示不了的图片按**内容**转成它能显示的。

为什么单独一个模块：两条发送路径都要用（`ob_protocol` 的 image 段、`wxui_sender`
的剪贴板贴图、以及 web_panel 的 /send_image），放在其中任一个里都会造成循环 import。
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile

log = logging.getLogger("imgutil")


def normalize_image_for_wechat(path: str) -> str:
    """WebP → GIF（多帧，保留动画）/ PNG（单帧）；其它格式原样返回。

    2026-10-22 实测：插件把 WebP 内容的图存成 `.gif` 文件名发过来（与「用户C」
    对话里的 `tmpb9_4pwp2.gif`，550.1K，真实字节是 WebP），微信按后缀当 GIF 解析
    失败，聊天里只剩一个文件卡片、对方点开也解不了。
    """
    if not path or not os.path.exists(path):
        return path
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return path
    try:
        with Image.open(path) as im:
            if str(im.format or "").upper() != "WEBP":
                return path
            frames = []
            durations = []
            try:
                total = int(getattr(im, "n_frames", 1) or 1)
            except Exception:  # noqa: BLE001
                total = 1
            for index in range(total):
                im.seek(index)
                frames.append(im.convert("RGBA"))
                durations.append(int(im.info.get("duration", 80) or 80))
            if not frames:
                return path
            suffix = ".gif" if total > 1 else ".png"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.close()
            if total > 1:
                frames[0].save(tmp.name, save_all=True, append_images=frames[1:],
                               duration=durations, loop=0, disposal=2)
            else:
                frames[0].convert("RGB").save(tmp.name)
            log.info("WebP->%s: %s -> %s frames=%s", suffix.lstrip("."),
                     os.path.basename(path), os.path.basename(tmp.name), total)
            return tmp.name
    except Exception as exc:  # noqa: BLE001
        log.warning("图片转码失败（按原样发送）: %s", exc)
        return path


def cleanup_temp(path: str) -> None:
    if path and os.path.basename(path).startswith("tmp") and os.path.basename(path) != path:
        try:
            os.unlink(path)
        except Exception:  # noqa: BLE001
            pass
