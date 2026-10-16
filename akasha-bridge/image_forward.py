"""图片转发策略：决定 bridge 收到图片/表情后发给 AstrBot 的内容形态。

背景：早期实现是把图片交给描述模型（ollama llava / OpenAI 兼容）先转成文字
（``[图片: 描述]``）再转发，主对话模型永远看不到原图；描述一旦有偏差，人物记忆
就会"记错事"。AstrBot 侧现在已经支持主模型直读图片（插件私聊图片管线切到
direct 模式），所以默认改为转发原图，描述退化为可选项。

模式：
- ``direct`` ：只转发原图段 ``[CQ:image,file=file:///...]``（默认）
- ``caption``：只转发描述文字（旧行为，保留兼容）
- ``both``   ：原图段 + 描述文字

说明：这里用 ``file:///`` 而不是 ``base64://``——图片本身就在本机 AstrBot 的
附件目录里，AstrBot 能直接读文件；base64 会把每一帧撑到几 MB，历史上正是它
导致过 WebSocket 1009 断开（见 ob_client.py 的 max_size=None）。
"""

import os
import re
from pathlib import Path

IMAGE_FORWARD_MODES = ("direct", "caption", "both")
DEFAULT_IMAGE_FORWARD_MODE = "direct"

# 只匹配我们自己生成的 CQ 图片码，避免误伤普通文本里的方括号
_CQ_IMAGE_RE = re.compile(r"\[CQ:image,file=([^\[\]]+)\]")


def normalize_forward_mode(value) -> str:
    """把配置/面板传来的值归一化成合法模式，非法或空值回退 direct。"""
    mode = str(value or "").strip().lower()
    return mode if mode in IMAGE_FORWARD_MODES else DEFAULT_IMAGE_FORWARD_MODE


def file_uri(path: str) -> str:
    """本地路径 → ``file:///C:/...`` URI（AstrBot 可直接读取）。"""
    return Path(os.path.abspath(str(path))).as_uri()


def build_image_marker(path: str) -> str:
    """生成 OneBot CQ 图片码；由 :func:`text_to_segments` 拆成真正的图片段。"""
    return f"[CQ:image,file={file_uri(path)}]"


def build_image_payload(image_path: str | None, caption: str | None,
                        mode: str = DEFAULT_IMAGE_FORWARD_MODE,
                        caption_placeholder: str = "（图片内容无法描述）") -> str:
    """按模式拼装要注入缓冲区的文本（可能含 CQ 图片码）。

    ``image_path`` 为空时无法转发原图，任何模式都退化为描述文字形态，
    这样上游"取图失败"时的旧行为保持一致。
    """
    normalized = normalize_forward_mode(mode)
    marker = build_image_marker(image_path) if image_path else ""
    text = str(caption or "").strip() or caption_placeholder

    if normalized == "direct":
        return marker or f"[图片: {text}]"
    if normalized == "caption":
        return f"[图片: {text}]"
    # both：原图在前，描述作为附带说明
    return f"{marker}[图片: {text}]" if marker else f"[图片: {text}]"


def text_to_segments(text: str) -> list:
    """把含 CQ 图片码的文本切成 OneBot 消息段数组（text/image 交替）。

    process_sender 用它把缓冲区里的文本形态统一转成消息段：没有 CQ 码时
    结果与旧实现完全一致（单个 text 段）。
    """
    raw = text or ""
    segments: list = []
    pos = 0
    for match in _CQ_IMAGE_RE.finditer(raw):
        if match.start() > pos:
            segments.append({"type": "text", "data": {"text": raw[pos:match.start()]}})
        segments.append({"type": "image", "data": {"file": match.group(1)}})
        pos = match.end()
    if pos < len(raw):
        segments.append({"type": "text", "data": {"text": raw[pos:]}})
    if not segments:
        segments.append({"type": "text", "data": {"text": raw}})
    return segments
