"""回归：同一联系人的分段发送不能被「后到的消息」越过（2026-10-20 桃桃三段事故）。

事故形态：第 1 段被 windows-mcp CONTROL_PREEMPTED 拒、进重发队列，第 2/3 段照常直发
成功 → 用户先看到后两段，第 1 段三分钟后才到（看起来像"吞了一段"）。

修复后应满足：
1. 第 1 段失败时，同一联系人已经排在调度器里的后续分段一起转入重发队列（不直发）；
2. 重发队列按入队顺序逐条送达；
3. 重发队列里同一联系人前一条还失败时，后一条本轮不许越过它；
4. 队列清空后恢复直发。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

import wxui_sender


class FakeSender(wxui_sender.WxuiSender):
    """把真正的 UI 动作换成一个可编排的假动作，其余逻辑（调度/队列）保持原样。"""

    def __init__(self, queue_path: str) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_texts: set[str] = set()
        self.fail_after_queue: set[str] = set()
        super().__init__(
            script=__file__,
            python_exe=os.sys.executable,
            retry_queue_path=queue_path,
            queue_interval=3600.0,   # 不让后台线程在测试期间插进来
            group_gather_seconds=0.0,
            warm_worker=False,
            dry_run=False,
        )

    def _run(self, subcmd, spec, target, quiet=False, assume_current=False):
        text = str(spec.get("text") or "")
        self.calls.append((subcmd, target, text))
        if subcmd == "verify":
            return True, {"verified": False}
        if text in self.fail_texts:
            return False, {"retryable": True,
                           "error": "desktop control preempted by windows-mcp "
                                    "(CONTROL_PREEMPTED: state=user wait=10.0s frozen latch)"}
        return True, {"verified": True, "localId": 1}

    def _worker_kill(self, reason=""):
        return None

    def _settle(self):
        return None

    def send(self, target: str, text: str) -> bool:
        return self._post({"type": "send_message", "target": target, "message": text})

    def queued_texts(self) -> list[str]:
        return [str((it.get("spec") or {}).get("text") or "") for it in self._load_queue()]


class SegmentOrderingTest(unittest.TestCase):
    def setUp(self) -> None:
        fd, self.queue_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        self.sender = FakeSender(self.queue_path)

    def tearDown(self) -> None:
        self.sender.stop()

    def test_failed_first_segment_holds_later_segments(self) -> None:
        target = "用户B"
        self.sender.fail_texts = {"第一段"}
        results = []
        results.append(self.sender.send(target, "第一段"))
        results.append(self.sender.send(target, "第二段"))
        results.append(self.sender.send(target, "第三段"))

        self.assertEqual(results, [False, False, False])
        # 只有第一段真的尝试过发送，后续分段直接排队（顺序保住了）
        sends = [text for subcmd, _, text in self.sender.calls if subcmd == "send"]
        self.assertEqual(sends, ["第一段"])
        self.assertEqual(self.sender.queued_texts(), ["第一段", "第二段", "第三段"])

    def test_queue_replays_in_order_and_unblocks(self) -> None:
        target = "用户B"
        self.sender.fail_texts = {"第一段"}
        for text in ("第一段", "第二段", "第三段"):
            self.sender.send(target, text)

        self.sender.fail_texts = set()   # 桌面控制恢复
        self.sender.calls.clear()
        stat = self.sender.drain_queue()

        resent = [text for subcmd, _, text in self.sender.calls if subcmd == "send"]
        self.assertEqual(resent, ["第一段", "第二段", "第三段"])
        self.assertEqual(stat["resent"], 3)
        self.assertEqual(self.sender.queued_texts(), [])
        # 队列清空后恢复正常直发
        self.assertFalse(self.sender._is_blocked(target))
        self.assertTrue(self.sender.send(target, "第四段"))
        self.assertEqual(self.sender.calls[-1], ("send", target, "第四段"))

    def test_later_item_never_overtakes_failed_one_in_same_round(self) -> None:
        target = "用户B"
        self.sender.fail_texts = {"第一段"}
        for text in ("第一段", "第二段"):
            self.sender.send(target, text)
        self.assertEqual(self.sender.queued_texts(), ["第一段", "第二段"])

        # 本轮第一段仍然失败：第二段必须留在队列里，不被越过
        self.sender.calls.clear()
        stat = self.sender.drain_queue()

        sends = [text for subcmd, _, text in self.sender.calls if subcmd == "send"]
        self.assertEqual(sends, ["第一段"])
        self.assertEqual(self.sender.queued_texts(), ["第一段", "第二段"])
        self.assertEqual(stat["failed"], 2)
        self.assertTrue(self.sender._is_blocked(target))


class QueueShapeTest(unittest.TestCase):
    def test_queue_item_keeps_insertion_order_on_disk(self) -> None:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        sender = FakeSender(path)
        try:
            sender.fail_texts = {"第一段"}
            for text in ("第一段", "第二段"):
                sender.send("张三", text)
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual([row["spec"]["text"] for row in rows], ["第一段", "第二段"])
            self.assertEqual([row["target"] for row in rows], ["张三", "张三"])
        finally:
            sender.stop()
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
