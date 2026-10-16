# -*- coding: utf-8 -*-
"""离线补发队列：AstrBot 不在线时不能丢事件。"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ob_protocol  # noqa: E402
import state  # noqa: E402


class _FakeWS:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def send(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(payload)


class OfflineQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        ob_protocol._OFFLINE_QUEUE.clear()
        state._ob_ws = None
        state._ob_ws_loop = None

    def tearDown(self) -> None:
        ob_protocol._OFFLINE_QUEUE.clear()
        state._ob_ws = None
        state._ob_ws_loop = None

    def test_event_is_queued_when_no_client(self):
        self.assertFalse(ob_protocol.push_event({"post_type": "message", "user_id": 1}))
        self.assertEqual(len(ob_protocol._OFFLINE_QUEUE), 1)

    def test_flush_sends_in_order_and_clears(self):
        for i in range(3):
            ob_protocol.push_event({"post_type": "message", "seq": i})
        ws = _FakeWS()
        sent = asyncio.run(ob_protocol.flush_offline_events(ws))
        self.assertEqual(sent, 3)
        self.assertEqual([json.loads(p)["seq"] for p in ws.sent], [0, 1, 2])
        self.assertEqual(len(ob_protocol._OFFLINE_QUEUE), 0)

    def test_send_failure_keeps_event_queued(self):
        ob_protocol.push_event({"post_type": "message", "seq": 0})
        sent = asyncio.run(ob_protocol.flush_offline_events(_FakeWS(fail=True)))
        self.assertEqual(sent, 0)
        self.assertEqual(len(ob_protocol._OFFLINE_QUEUE), 1)

    def test_expired_event_is_not_replayed(self):
        ob_protocol.push_event({"seq": "old"})
        ts, item = ob_protocol._OFFLINE_QUEUE[0]
        ob_protocol._OFFLINE_QUEUE[0] = (ts - ob_protocol._OFFLINE_QUEUE_TTL - 1, item)
        ws = _FakeWS()
        sent = asyncio.run(ob_protocol.flush_offline_events(ws))
        self.assertEqual(sent, 0)
        self.assertEqual(ws.sent, [])

    def test_queue_is_bounded_and_drops_oldest(self):
        for i in range(ob_protocol._OFFLINE_QUEUE_MAX + 5):
            ob_protocol.push_event({"seq": i})
        self.assertEqual(len(ob_protocol._OFFLINE_QUEUE), ob_protocol._OFFLINE_QUEUE_MAX)
        self.assertEqual(ob_protocol._OFFLINE_QUEUE[0][1]["seq"], 5)


if __name__ == "__main__":
    unittest.main()
