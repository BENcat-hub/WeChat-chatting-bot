"""
微信 ↔ AstrBot 桥接（OneBot v11 版）
=====================================
消息接收：WeFlow SSE 推送
AI 服务：AstrBot 通过 aiocqhttp (OneBot v11) 接入
消息发送：bridge 接收 AstrBot 的 API 调用 → WeFlow API / UIA

架构：
  WeFlow ──SSE──→ bridge.py ──WS 客户端──→ AstrBot (aiocqhttp 服务端)
                   ↑ 连接 ws://127.0.0.1:19777  ↑ 监听端口，等待客户端连入
                   发送 OneBot 事件             返回 API 响应
"""

# 共享状态：所有模块通过 import state 访问这些变量
import hashlib
import json
import os
import threading
from typing import Optional

# ============ 状态控制 ============

running = False
paused = threading.Event()
paused.clear()
run_lock = threading.Lock()
bridge_thread = None

# ============ OneBot WebSocket 客户端管理 ============

_ob_ws = None          # WebSocket 连接实例
_ob_ws_loop = None     # 事件循环
_ob_ws_ready = threading.Event()
_self_id_int = 0       # 启动时从 config 初始化


# ============ 稳定 ID 派生 ============
# 旧实现 abs(hash(wxid)) % 2**31 受 PYTHONHASHSEED 影响：bridge 一重启，
# 同一个人就会拿到新的 user_id，AstrBot 于是把它当成新用户（会话/记忆全部重开）。
# 现改为 sha256 派生：跨进程、跨机器都稳定；user/group/self 各占独立段位，互不碰撞。

_ID_NAMESPACE = {"user": 1_000_000_000, "group": 1_500_000_000, "self": 1_900_000_000}
_ID_SPAN = 100_000_000          # 每段 1 亿个槽位，结果恒 < 2**31


def stable_int(namespace: str, key: str) -> int:
    """把任意字符串映射为稳定正整数（sha256 派生，与进程/PYTHONHASHSEED 无关）。"""
    digest = hashlib.sha256(f"{namespace}:{key}".encode("utf-8")).digest()
    return _ID_NAMESPACE[namespace] + int.from_bytes(digest[:4], "big") % _ID_SPAN


def _wxid_to_int(wxid: str) -> int:
    """私聊联系人或群成员 → OneBot user_id（user 命名空间）。"""
    return stable_int("user", str(wxid))


def _group_id_int(room: str) -> int:
    """群聊 → OneBot group_id（group 命名空间；必须传稳定的 @chatroom id，不能用群昵称）。"""
    return stable_int("group", str(room))


def _self_id_of(wxid: str) -> int:
    """机器人自身 → OneBot self_id（self 命名空间）。"""
    return stable_int("self", str(wxid or "wechat_bot"))


# ============ 桥接实例 / 发送器 ============

bridge_instance = None
bridge_lock = threading.Lock()
sender_instance = None
_ob_id_to_contact: dict[int, str] = {}  # OneBot user_id/group_id → 微信联系名
ob_client_started = False

# id ↔ 联系人名 映射落盘：否则 bridge 重启后收到「旧会话」的回复请求会找不到人
_OB_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ob_id_map.json")
_ob_map_lock = threading.Lock()


def load_ob_id_map() -> int:
    """启动时从磁盘恢复 id → 联系名 映射，返回恢复条数。"""
    try:
        with open(_OB_MAP_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return 0
    except Exception as exc:      # 损坏的映射文件不该拦住启动
        print(f"[state] ob_id_map.json 读取失败: {exc}")
        return 0
    if not isinstance(data, dict):
        return 0
    restored = 0
    with _ob_map_lock:
        for key, value in data.items():
            try:
                _ob_id_to_contact[int(key)] = str(value)
                restored += 1
            except (TypeError, ValueError):
                continue
    return restored


def remember_ob_contact(ob_id: int, contact: str) -> None:
    """记录 id → 联系名并在内容变化时落盘（供 API 回复与重启恢复使用）。"""
    contact = str(contact or "")
    if not contact:
        return
    with _ob_map_lock:
        if _ob_id_to_contact.get(ob_id) == contact:
            return
        _ob_id_to_contact[ob_id] = contact
        snapshot = {str(k): v for k, v in _ob_id_to_contact.items()}
    try:
        tmp = f"{_OB_MAP_PATH}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, _OB_MAP_PATH)
    except Exception as exc:
        print(f"[state] ob_id_map.json 写入失败: {exc}")

# 群聊回复模式（运行时可变，启动时从 config 初始化）
group_reply_mode = "mention"

# 图片描述配置（运行时可变，Web 面板保存后即时生效）
image_caption_provider = "ollama"
image_caption_model = "llava:7b"
image_caption_api_key = ""
image_caption_api_base = ""
image_caption_prompt = "请用中文简短描述这张图片的内容"
# 图片转发策略（"direct" / "caption" / "both"，运行时可变）
image_forward_mode = "direct"
# 出站按联系人成组：换人前的聚集窗口（秒，运行时可变）
wxui_group_gather_seconds = 1.2
# 常驻 worker（分段发送提速）：一个 wxui.py 进程长期活着，复用 Windows-MCP 客户端
wxui_warm_worker = True
ollama_base_url = "http://127.0.0.1:61000"
ollama_timeout = 60
