"""
配置模块：加载 config.json，提供全局配置常量。
"""

import json
import os
import logging
import threading

# ============ 配置 ============

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


config = load_config()

WE_FLOW_BASE_URL = config["weflow_base_url"]
ACCESS_TOKEN = config["access_token"]
ASTRBOT_ATTACHMENTS = config.get("astrbot_attachments", "")
BOT_NICKNAMES = config["bot_nicknames"]
BOT_WXID = config.get("bot_wxid", "")
# 发送方式已固定为 UIA 纯键盘模拟
BUFFER_SECONDS = config.get("buffer_seconds", 5)
WEB_PORT = config.get("web_port", 8766)
GROUP_REPLY_MODE = config.get("group_reply_mode", "mention")  # "mention" / "all"

# AstrBot OneBot 连接配置（bridge 作为 WebSocket 客户端连 AstrBot 的 aiocqhttp 服务端）
ASTRBOT_OB_URL = config.get("astrbot_ob_url", "ws://127.0.0.1:19777")

# 图片描述配置（支持 ollama 或 openai 兼容 API）
IMAGE_CAPTION_PROVIDER = config.get("image_caption_provider", "ollama")  # "ollama" / "openai"
IMAGE_CAPTION_MODEL = config.get("image_caption_model", "llava:7b")
IMAGE_CAPTION_API_KEY = config.get("image_caption_api_key", "")
IMAGE_CAPTION_API_BASE = config.get("image_caption_api_base", "")
IMAGE_CAPTION_PROMPT = config.get("image_caption_prompt", "请用中文简短描述这张图片的内容")

# 图片转发策略："direct"=只转发原图（主模型直读，推荐）/ "caption"=只转发描述文字 / "both"=原图+描述
IMAGE_FORWARD_MODE = config.get("image_forward_mode", "direct")

# Ollama 图片描述配置（provider=ollama 时使用）
OLLAMA_BASE_URL = config.get("ollama_base_url", "http://127.0.0.1:61000")
OLLAMA_TIMEOUT = config.get("ollama_timeout", 60)

# ============ 发送后端与出站护栏 ============
# "weemessenger" = 文本走 WeeMessenger 指令通道（推荐）；"uia" = 全部由 bridge 自己操作微信
SENDER_BACKEND = config.get("sender_backend", "weemessenger")

# WeeMessenger 的 HTTP 指令服务（其 config.yaml 里 http.enabled 必须为 true）
WEEMESSENGER_HTTP_BASE = config.get("weemessenger_http_base", "http://127.0.0.1:9877")
WEEMESSENGER_TIMEOUT = config.get("weemessenger_timeout", 30)

# wxui 文本发送后端：由 Windows-MCP + Windows OCR 驱动微信窗口，并用 WeFlow 回读验证
# （WeeMessenger/UIA 的 Ctrl+F→粘贴→回车 在微信 4.x 会命中"搜索网络结果"，所以默认改用 wxui）
WXUI_SCRIPT = config.get("wxui_script", "")
WXUI_PYTHON = config.get("wxui_python", "")
WXUI_TIMEOUT = config.get("wxui_timeout", 90)
# 重发队列：wxui 内部重试仍失败（RDP 断连、微信被最小化、锁屏等长时故障）时，
# 消息落到这个文件，后台线程每隔几秒重发一次；每次重发前先做只读回读校验，避免重复发送
WXUI_RETRY_QUEUE = config.get("wxui_retry_queue", "")
WXUI_QUEUE_INTERVAL = config.get("wxui_queue_interval", 20)
WXUI_QUEUE_MAX_ATTEMPTS = config.get("wxui_queue_max_attempts", 5)
# 出站按联系人成组：同时有多个会话要回复时，先把一个人的消息发完，再换下一个人；
# 同一联系人本次连发的第 2 条起走「只校验聊天头部」的快路径，省掉一次侧边栏全量 OCR。
# 这个值是换人前的聚集窗口（秒）：上一段发完后最多等这么久，等到同一个人的后续分段
# 就继续发他，等不到才换人。
WXUI_GROUP_GATHER_SECONDS = config.get("wxui_group_gather_seconds", 1.2)
# 常驻 worker（2026-10-16 分段发送提速）：wxui.py 只启动一次，一个进程里复用同一个
# Windows-MCP 客户端（省掉每条消息约 3 秒的 windows-mcp 启动），并记住「微信窗口现在停在
# 谁的会话上」，同一联系人连发时不再重复验证窗口位置和名字。worker 不可用时自动回退到
# 逐条起进程的老路径，行为与过去一致。
WXUI_WARM_WORKER = config.get("wxui_warm_worker", True)
# 图片通道："uia" = bridge 剪贴板贴图（png/jpg/gif，画质无损）；"sticker" = 走 WeeMessenger 表情包通道（仅 GIF）
WEEMESSENGER_IMAGE_MODE = config.get("weemessenger_image_mode", "uia")
# 图片走 UIA 前等待的秒数，用于错开 WeeMessenger 正在进行的 UI 动作
UI_HANDOFF_WAIT = config.get("ui_handoff_wait", 3.0)

# 出站白名单：非空时只允许给名单内的微信昵称/群名发消息（强烈建议填写）
SEND_WHITELIST = config.get("send_whitelist", [])
# 出站黑名单：优先级高于白名单
SEND_BLACKLIST = config.get("send_blacklist", [])
# 限频：同一联系人两次发送的最小间隔 / 全局两次发送的最小间隔（秒）
SEND_MIN_INTERVAL_CONTACT = config.get("send_min_interval_contact", 2.0)
SEND_MIN_INTERVAL_GLOBAL = config.get("send_min_interval_global", 1.0)
# 空跑：true 时只写日志不真的发微信（首次联调、或想观察 AI 回复时用）
SEND_DRY_RUN = config.get("send_dry_run", True)
SEND_MAX_TEXT_LENGTH = config.get("send_max_text_length", 2000)

# ============ 日志 ============

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("bridge.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("ob11-bridge")
