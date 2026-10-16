"""出站「按联系人成组」单测。

要保证的两件事：
  1. 同一个人的消息连发完才换下一个人（多个人同时来消息时不交错）；
  2. 同一个人本次连发的第 2 条起走 assume_current 快路径，省掉一次侧边栏 OCR。

运行：cwd=akasha-bridge，`python -m unittest discover -s tests -p test_wxui_grouping.py`
"""

import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import wxui_sender as mod  # noqa: E402
from wxui_sender import WxuiSender  # noqa: E402

BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STACK_DIR = os.path.dirname(BRIDGE_DIR)
WXUI_PY = os.path.join(STACK_DIR, "scripts", "wxui.py")


class _FakeWxuiSender(WxuiSender):
    """记录每次 _run 的调用，不真的驱动微信窗口。"""

    def __init__(self, delay: float = 0.02, group_gather_seconds: float = 0.05) -> None:
        self.calls = []
        self.delay = delay
        self._calls_lock = threading.Lock()
        self._queue_file = os.path.join(tempfile.mkdtemp(prefix="wxui-test-"), "pending.jsonl")
        super().__init__(
            script=__file__,
            python_exe=sys.executable,
            retry_queue_path=self._queue_file,
            queue_interval=3600.0,          # 重发线程本轮不会真的跑起来
            dry_run=False,
            group_gather_seconds=group_gather_seconds,
            min_interval_contact=0.0,
            min_interval_global=0.0,
        )

    def _run(self, subcmd, spec, target, quiet=False, assume_current=False):
        if self.delay:
            time.sleep(self.delay)
        with self._calls_lock:
            self.calls.append({"subcmd": subcmd, "target": target, "text": spec.get("text"),
                               "assume_current": assume_current})
        return True, {"verified": True, "localId": len(self.calls)}


class SchedulerOrderTest(unittest.TestCase):
    def _fire(self, sender, contact, text):
        result = {}
        thread = threading.Thread(
            target=lambda: result.update(zip(("ok", "data"),
                                             sender._scheduled_run(
                                                 "send", {"target": contact, "text": text}, contact))),
            daemon=True)
        thread.start()
        return thread, result

    def test_burst_to_same_contact_is_grouped(self):
        sender = _FakeWxuiSender(delay=0.02, group_gather_seconds=0.5)
        try:
            threads = []
            for i in range(3):
                t, _ = self._fire(sender, "用户B", "m%d" % i)
                threads.append(t)
                time.sleep(0.01)
            for t in threads:
                t.join(10)

            self.assertEqual(3, len(sender.calls))
            self.assertEqual(["用户B"] * 3, [c["target"] for c in sender.calls])
            self.assertEqual(["m0", "m1", "m2"], [c["text"] for c in sender.calls])
            # 第 2 条起省一次侧边栏 OCR（快路径先看聊天头部）
            self.assertEqual([False, True, True],
                             [c["assume_current"] for c in sender.calls])
        finally:
            sender.stop()

    def test_contact_turn_is_finished_before_switching(self):
        """A1、B1、A2 同时到：A 的两条必须连着发完，B 排在后面。"""
        sender = _FakeWxuiSender(delay=0.06, group_gather_seconds=0.2)
        try:
            t1, _ = self._fire(sender, "A", "a1")
            time.sleep(0.01)
            t2, _ = self._fire(sender, "B", "b1")
            time.sleep(0.01)
            t3, _ = self._fire(sender, "A", "a2")
            for t in (t1, t2, t3):
                t.join(10)

            self.assertEqual([("A", "a1"), ("A", "a2"), ("B", "b1")],
                             [(c["target"], c["text"]) for c in sender.calls])
            self.assertEqual([False, True, False],
                             [c["assume_current"] for c in sender.calls])
        finally:
            sender.stop()

    def test_turn_resets_after_switching_contact(self):
        sender = _FakeWxuiSender(delay=0.02, group_gather_seconds=0.05)
        try:
            t1, _ = self._fire(sender, "A", "a1")
            t1.join(10)
            time.sleep(0.1)                     # 换人：A 的轮次结束
            t2, _ = self._fire(sender, "B", "b1")
            t2.join(10)
            t3, _ = self._fire(sender, "A", "a2")
            t3.join(10)

            self.assertEqual([("A", "a1"), ("B", "b1"), ("A", "a2")],
                             [(c["target"], c["text"]) for c in sender.calls])
            # 每次换人后的第一条都不能假设窗口还停在他那儿
            self.assertEqual([False, False, False],
                             [c["assume_current"] for c in sender.calls])
        finally:
            sender.stop()

    def test_result_is_returned_to_the_caller(self):
        sender = _FakeWxuiSender(delay=0.0, group_gather_seconds=0.0)
        try:
            ok, data = sender._scheduled_run("send", {"target": "A", "text": "hi"}, "A")
            self.assertTrue(ok)
            self.assertTrue(data.get("verified"))
        finally:
            sender.stop()


class SpecInjectionTest(unittest.TestCase):
    """assume_current 必须只以 spec 字段的形式下传（argv 会乱码）。"""

    def _shim(self):
        obj = WxuiSender.__new__(WxuiSender)   # 不跑 __init__（不起线程）
        obj._ui_lock = threading.Lock()
        obj.python_exe = sys.executable
        obj.script = __file__
        obj.wxui_timeout = 10.0
        obj.warm_worker = False                # 本组用例测的是逐条起进程那条路径
        return obj

    def _run_capturing_spec(self, assume_current):
        captured = {}
        real_run = mod.subprocess.run

        def fake_run(cmd, **kwargs):
            with open(cmd[cmd.index("--spec") + 1], encoding="utf-8") as fh:
                captured["spec"] = json.load(fh)
            return types.SimpleNamespace(stdout='{"verified": true, "localId": 7}',
                                         stderr="", returncode=0)

        mod.subprocess.run = fake_run
        try:
            ok, data = WxuiSender._run(self._shim(), "send",
                                       {"target": "A", "text": "hi"}, "A",
                                       assume_current=assume_current)
        finally:
            mod.subprocess.run = real_run
        return ok, data, captured.get("spec") or {}

    def test_flag_is_injected(self):
        ok, _, spec = self._run_capturing_spec(True)
        self.assertTrue(ok)
        self.assertTrue(spec.get("assume_current"))
        self.assertTrue(spec.get("commit"))

    def test_flag_absent_keeps_old_payload(self):
        _, _, spec = self._run_capturing_spec(False)
        self.assertNotIn("assume_current", spec)


class WxuiFastPathSourceTest(unittest.TestCase):
    def setUp(self):
        with open(WXUI_PY, encoding="utf-8") as fh:
            self.src = fh.read()

    def test_flag_is_exposed_on_both_send_commands(self):
        self.assertEqual(2, self.src.count('"--assume-current"'))

    def test_spec_field_is_honoured(self):
        self.assertIn('spec.get("assume_current", assume_current)', self.src)

    def test_fast_path_skips_sidebar_scan_but_verifies_header(self):
        self.assertIn("sidebar OCR skipped", self.src)
        self.assertIn("chat_header_matches(client, cfg, rect, target)", self.src)

    def test_both_send_paths_pass_it_to_open_chat(self):
        self.assertEqual(4, self.src.count(
            "open_chat(client, cfg, target, plan, do_ocr=do_ocr, assume_current=assume_current)"))

    def test_focus_check_is_shared(self):
        self.assertIn("def _chat_ready_to_type(cfg, plan, target):", self.src)


class ConfigWiringTest(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(BRIDGE_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_gather_window_is_configurable(self):
        self.assertIn("WXUI_GROUP_GATHER_SECONDS", self._read("config.py"))
        self.assertIn("wxui_group_gather_seconds", self._read("state.py"))
        self.assertIn("state.wxui_group_gather_seconds", self._read("main.py"))
        self.assertIn("group_gather_seconds=config.WXUI_GROUP_GATHER_SECONDS",
                      self._read("senders.py"))


class WarmWorkerWiringTest(unittest.TestCase):
    """2026-10-16 分段发送提速：常驻 worker 必须能从配置一路到发送后端，且能一键关掉。"""

    def _read(self, name):
        with open(os.path.join(BRIDGE_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_config_and_state_expose_the_switch(self):
        self.assertIn("WXUI_WARM_WORKER", self._read("config.py"))
        self.assertIn("wxui_warm_worker = True", self._read("state.py"))
        self.assertIn("state.wxui_warm_worker = config.WXUI_WARM_WORKER", self._read("main.py"))
        self.assertIn("warm_worker=config.WXUI_WARM_WORKER", self._read("senders.py"))
    def test_sender_defaults_to_warm_and_owns_teardown(self):
        src = self._read("wxui_sender.py")
        self.assertIn("warm_worker: bool = True", src)
        self.assertIn("self.warm_worker = bool(warm_worker)", src)
        self.assertIn("def _worker_call(self, subcmd: str, spec: dict, target: str):", src)
        self.assertIn("def _run_oneshot(self, subcmd: str, spec: dict, target: str):", src)
        # worker 不可用时必须回退到老路径，而不是把消息丢掉
        self.assertIn("data = self._worker_call(subcmd, spec, target)", src)
        self.assertIn("data, fatal = self._run_oneshot(subcmd, spec, target)", src)
        # stop() 里要回收 worker，否则 bridge 重启会留下孤儿进程
        self.assertIn('self._worker_kill("bridge 停止")', src)

    def test_worker_subcommand_exists_on_the_script(self):
        with open(os.path.join(STACK_DIR, "scripts", "wxui.py"), encoding="utf-8") as fh:
            src = fh.read()
        for needle in ('sub.add_parser("worker"', "def worker_loop(cfg):",
                       "def worker_dispatch(cfg, client, subcmd, spec):",
                       "client=None",
                       '_WARM = {"target": "", "at": 0.0, "header_at": 0.0, "rect": None,',
                       '"clean": "", "clean_at": 0.0}',
                       "warm session: no OCR"):
            # assertTrue 而不是 assertIn：失败时只会打印短消息，不会把整个文件倒进输出
            self.assertTrue(needle in src, "wxui.py 里缺少：%s" % needle)

    def test_warm_path_still_gates_on_verified_delivery(self):
        with open(os.path.join(STACK_DIR, "scripts", "wxui.py"), encoding="utf-8") as fh:
            src = fh.read()
        for needle in ("def warm_session_trusted(cfg, target, plan):",
                       "def input_box_trusted_clean(cfg, target):",
                       "def _warm_forget_clean():",
                       "_warm_forget_clean()   #",
                       "_warm_mark(target, plan.get(\"window_rect\"), header_verified=True, clean=True)",
                       'if attempt > 1 and not wait_for_session(client, cfg, plan):'):
            self.assertTrue(needle in src, "wxui.py 里缺少：%s" % needle)
        # 两条发送路径都必须先算 trusted、再作废 clean（顺序反了热路径就永远不生效）
        self.assertEqual(2, src.count("trusted = warm_session_trusted(cfg, target, plan)"))
        self.assertEqual(2, src.count("fresh_input = input_box_trusted_clean(cfg, target)"))
        self.assertEqual(2, src.count("_warm_mark(target, plan.get(\"window_rect\"), "
                                      "header_verified=True, clean=True)"))
        # 快速清空只在「上一条已确认送达 + 本条第 1 次尝试」时走
        self.assertEqual(2, src.count("focused_clean=fresh_input and attempt == 1"))

    def test_clear_input_fast_path_is_guarded(self):
        with open(os.path.join(STACK_DIR, "scripts", "wxui.py"), encoding="utf-8") as fh:
            src = fh.read()
        # 快速路径必须以 focused_clean 为条件，且默认开启；cfg 关掉时立刻退回完整清理
        self.assertIn("def clear_input(client, cfg, rect, focused_clean=False):", src)
        self.assertIn('if focused_clean and cfg.get("input_fast_clear", True):', src)
        self.assertIn('client.call("Click", {"loc": [x, y], "clicks": 1})', src)

    def test_warm_reuse_is_bounded(self):
        with open(os.path.join(STACK_DIR, "scripts", "wxui-config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertGreater(cfg.get("warm_reuse_s", 0), 0)
        self.assertGreater(cfg.get("warm_header_recheck_s", 0), 0)
        self.assertLessEqual(cfg["warm_header_recheck_s"], cfg["warm_reuse_s"])
        self.assertGreater(cfg.get("verify_poll_s", 0), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
