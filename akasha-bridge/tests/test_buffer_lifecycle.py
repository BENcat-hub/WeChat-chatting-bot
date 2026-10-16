"""缓冲生命周期单测：过期消息 TTL 丢弃 + 推送期间新到的消息必须补计时器。

背景（2026-10-16 事故）：图片比文本晚几秒到、落进一个"已经推送过/正在推送"的
缓冲条目里，而该分支不挂计时器，于是这张图一直卡在队列里；等到十几分钟后
用户再发消息触发 flush 时，AstrBot 收到的是十几分钟前的旧图（"AI 读到的其实是
人设图"）。这里把两条不变式钉住：
  1. `process_sender` 丢弃超过 `WeFlowBridge.BUFFER_MESSAGE_TTL_SECONDS` 的消息；
  2. 推送结束后若条目里还有新消息，自动补一次 flush。

运行：cwd=akasha-bridge，`python -m unittest discover -s tests -t tests`
"""

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge_core


def _bare_bridge():
    """不跑 __init__（避免读配置/起线程）的最小实例。"""
    bridge = object.__new__(bridge_core.WeFlowBridge)
    bridge.pending_buffers = {}
    bridge.buffer_lock = threading.RLock()
    bridge._pending_mention_images = {}
    bridge._sent_recently = {}
    bridge._last_push = 0.0
    return bridge


def _entry(messages, stamps, **extra):
    entry = {
        "messages": list(messages),
        "message_ts": list(stamps),
        "rawids": [""] * len(messages),
        "timer": None,
        "timer_version": 0,
        "processing": False,
        "contact": "用户C",
        "is_group": False,
        "source_name": "用户C",
        "session_id_data": "wxid_CONTACT_A",
    }
    entry.update(extra)
    return entry


class ArmFlushLockedTest(unittest.TestCase):
    def test_arms_timer_when_idle(self):
        bridge = _bare_bridge()
        entry = _entry(["a"], [time.time()])
        bridge.pending_buffers["k"] = entry
        bridge._arm_flush_locked("k", entry, 3600.0)
        self.assertIsNotNone(entry["timer"])
        self.assertEqual(1, entry["timer_version"])
        entry["timer"].cancel()

    def test_never_arms_while_processing(self):
        bridge = _bare_bridge()
        entry = _entry([], [], processing=True)
        bridge._arm_flush_locked("k", entry, 0.05)
        self.assertIsNone(entry["timer"])

    def test_keeps_live_timer(self):
        bridge = _bare_bridge()
        entry = _entry(["a"], [time.time()])
        live = threading.Timer(3600, lambda: None)
        live.daemon = True
        live.start()
        entry["timer"] = live
        entry["timer_version"] = 7
        bridge._arm_flush_locked("k", entry, 0.05)
        self.assertIs(live, entry["timer"])
        self.assertEqual(7, entry["timer_version"])
        live.cancel()


class ProcessSenderTtlTest(unittest.TestCase):
    def setUp(self):
        self.bridge = _bare_bridge()
        self.pushed = []
        self._orig = (bridge_core.push_event, bridge_core.make_message_event)
        bridge_core.push_event = lambda event: (self.pushed.append(event), 1)[1]
        bridge_core.make_message_event = lambda *a, **k: {"kind": a[0], "segments": a[2]}
        self._orig_state = (
            getattr(bridge_core.state, "_wxid_to_int", None),
            getattr(bridge_core.state, "remember_ob_contact", None),
        )
        bridge_core.state._wxid_to_int = lambda wxid: 1000000006
        bridge_core.state.remember_ob_contact = lambda *a, **k: None

    def tearDown(self):
        bridge_core.push_event, bridge_core.make_message_event = self._orig
        if self._orig_state[0] is not None:
            bridge_core.state._wxid_to_int = self._orig_state[0]
        if self._orig_state[1] is not None:
            bridge_core.state.remember_ob_contact = self._orig_state[1]

    def _texts(self):
        out = []
        for event in self.pushed:
            for seg in event["segments"]:
                if seg["type"] == "text":
                    out.append(seg["data"]["text"])
        return out

    def test_drops_stale_messages(self):
        now = time.time()
        self.bridge.pending_buffers["k"] = _entry(
            ["[图片: （图片内容无法描述）]", "我夸你是真心的", "图发给你"],
            [now - 12 * 60, now - 5, now - 4],
        )
        self.bridge.process_sender("k")

        self.assertEqual(["我夸你是真心的\n图发给你"], self._texts())
        entry = self.bridge.pending_buffers["k"]
        self.assertEqual([], entry["messages"])
        self.assertEqual([], entry["message_ts"])
        self.assertFalse(entry["processing"])

    def test_all_stale_pushes_nothing(self):
        old = time.time() - 10 * 60
        self.bridge.pending_buffers["k"] = _entry(["旧图", "旧占位"], [old, old])
        self.bridge.process_sender("k")

        self.assertEqual([], self.pushed)
        entry = self.bridge.pending_buffers["k"]
        self.assertEqual([], entry["messages"])
        self.assertFalse(entry["processing"])

    def test_fresh_messages_survive(self):
        now = time.time()
        self.bridge.pending_buffers["k"] = _entry(["a", "b"], [now - 1, now])
        self.bridge.process_sender("k")
        self.assertEqual(["a\nb"], self._texts())

    def test_message_arriving_during_push_gets_new_timer(self):
        now = time.time()
        entry = _entry(["先到的文本"], [now])
        self.bridge.pending_buffers["k"] = entry

        def push_during(event):
            # 模拟推送期间图片注入缓冲：processing 仍是 True，走不到 add_to_buffer 的挂表分支
            entry["messages"].append("[CQ:image,file=file:///cat.jpg]")
            entry["message_ts"].append(time.time())
            self.pushed.append(event)
            return 1

        bridge_core.push_event = push_during
        try:
            self.bridge.process_sender("k")
        finally:
            bridge_core.push_event = self._orig[0]

        self.assertFalse(entry["processing"])
        self.assertIsNotNone(entry["timer"])
        self.assertGreaterEqual(entry["timer_version"], 1)
        self.assertEqual(1, len(entry["messages"]))
        entry["timer"].cancel()


if __name__ == "__main__":
    unittest.main()
