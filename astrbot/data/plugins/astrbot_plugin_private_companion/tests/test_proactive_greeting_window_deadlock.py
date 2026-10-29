"""回归：问候计划卡在过期窗口里导致该用户再也收不到主动消息。

事故现场（2026-10-20 用户B / 1000000003）：
`next_proactive_at` = 11:50:35，morning_greeting 窗口 11:08-11:55；
12:14 的 tick 判定“到点但不在窗口内”，sticky 分支调用
`_reschedule_greeting_within_window` 得到 False，却仍然返回“稍后再试”，
`next_proactive_at` 保持过期值不变 —— 之后每个 tick 都重复同一跳过。
"""

from __future__ import annotations

import datetime
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_PARENT = PLUGIN_ROOT.parent
if str(PLUGIN_PARENT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_PARENT))

from astrbot_plugin_private_companion.proactive_engine import (  # noqa: E402
    ProactiveEngineMixin,
    _safe_float,
)

TZ = ZoneInfo("Asia/Shanghai")
WINDOW = (11 * 60 + 8, 11 * 60 + 55)  # 11:08-11:55，与事故当天一致


def _ts(hour: int, minute: int, second: int = 0) -> float:
    return datetime.datetime(2026, 10, 20, hour, minute, second, tzinfo=TZ).timestamp()


class _FakeEngine:
    """只保留本用例需要的调度能力，其余用记录桩替换。"""

    _is_sticky_greeting_reason = ProactiveEngineMixin._is_sticky_greeting_reason
    _normalize_legacy_proactive_text = ProactiveEngineMixin._normalize_legacy_proactive_text
    _reschedule_greeting_within_window = ProactiveEngineMixin._reschedule_greeting_within_window
    _abandon_expired_greeting_plan = ProactiveEngineMixin._abandon_expired_greeting_plan

    def __init__(self, *, schedule_result: bool = True) -> None:
        self.calls: list[str] = []
        self.schedule_result = schedule_result

    def _reason_windows(self, reason, user=None):
        if reason == "morning_greeting":
            return [WINDOW]
        return []

    def _environment_fromtimestamp(self, ts):
        return datetime.datetime.fromtimestamp(ts, tz=TZ)

    def _clear_pending_proactive_plan(self, user):
        self.calls.append("clear")
        user["next_proactive_at"] = 0

    def _schedule_next_proactive(self, user, *, now=None):
        self.calls.append("sched")
        if not self.schedule_result:
            raise RuntimeError("planner down")
        user["next_proactive_at"] = (now or 0) + 3600


class GreetingWindowDeadlockTest(unittest.TestCase):
    def test_tail_of_window_cannot_be_rescheduled(self):
        """窗口只剩 3 分钟时，最早可发送点已越过最晚发送点。"""
        engine = _FakeEngine()
        user = {"user_id": "1000000003", "next_proactive_at": _ts(11, 50, 35)}

        rescheduled = engine._reschedule_greeting_within_window(
            user, "morning_greeting", now=_ts(11, 52)
        )

        self.assertFalse(rescheduled)
        self.assertEqual(user["next_proactive_at"], _ts(11, 50, 35))

    def test_abandon_advances_schedule_instead_of_keeping_stale_timestamp(self):
        engine = _FakeEngine()
        now = _ts(12, 14, 3)
        user = {"user_id": "1000000003", "next_proactive_at": _ts(11, 50, 35)}

        engine._abandon_expired_greeting_plan(user, "morning_greeting", now=now)

        self.assertEqual(engine.calls, ["clear", "sched"])
        self.assertGreater(_safe_float(user.get("next_proactive_at"), 0), now)

    def test_abandon_falls_back_when_planner_raises(self):
        engine = _FakeEngine(schedule_result=False)
        now = _ts(12, 14, 3)
        user = {"user_id": "1000000003", "next_proactive_at": _ts(11, 50, 35)}

        engine._abandon_expired_greeting_plan(user, "morning_greeting", now=now)

        self.assertGreaterEqual(_safe_float(user.get("next_proactive_at"), 0), now + 45 * 60)

    def test_planner_failure_does_not_leave_zero_schedule(self):
        """排程抛错时也不能留下 next_proactive_at=0，否则 tick 永远不到点。"""
        engine = _FakeEngine(schedule_result=False)
        now = _ts(12, 14, 3)
        user = {"user_id": "1000000003", "next_proactive_at": 0.0}

        engine._abandon_expired_greeting_plan(user, "morning_greeting", now=now)

        self.assertGreater(_safe_float(user.get("next_proactive_at"), 0), now)


if __name__ == "__main__":
    unittest.main()
