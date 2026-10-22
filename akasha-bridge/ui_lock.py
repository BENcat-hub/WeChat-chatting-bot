"""微信桌面 UI 的全局互斥锁。

语音气泡链路（CABLE 虚拟麦克风 + 点麦克风 / 点发送）和文字/图片/文件发送链路
（点会话、粘剪贴板、回车）操作的是**同一个微信窗口**。两边并发时，一边刚点开 A，
另一边把内容粘进 A，或者语音的「发送」点落到别人身上。2026-10-22 实测：
/svc 翻唱回复与语音气泡并发 → 文字粘错会话、回读验证失败、消息进重发队列并重复投递。
会动鼠标/键盘的路径一律先拿这把锁。
"""
import threading

UI_LOCK = threading.Lock()


def ui_busy(timeout: float = 0.0) -> bool:
    got = UI_LOCK.acquire(timeout=timeout)
    if got:
        UI_LOCK.release()
        return False
    return True
