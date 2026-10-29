"""后置过滤：把「当前计划时段」当成刚发生的事复述的分段要丢弃。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion import main as main_module

_PLUGIN = main_module.PrivateCompanionPlugin

_PLAN = (
    "10:00-10:50｜出门去附近的小超市买抽纸和洗衣液…"
    "回来路上看见两只麻雀在垃圾桶旁抢一块饼。｜情绪：轻快,被风吹醒"
)
_NORMAL = "哈 就一个「厉害」 我这字自己都不敢细看"
_RECITAL = "刚出门买了抽纸 还顺手拿了瓶气泡水 回来路上看两只麻雀抢一块饼 抢得可凶了"


class _Dummy:
    _SCHEDULE_RECITAL_OVERLAP_THRESHOLD = _PLUGIN._SCHEDULE_RECITAL_OVERLAP_THRESHOLD
    _SCHEDULE_RECITAL_MAX_CHARS = _PLUGIN._SCHEDULE_RECITAL_MAX_CHARS
    _SCHEDULE_RECITAL_QUESTION_PATTERN = _PLUGIN._SCHEDULE_RECITAL_QUESTION_PATTERN
    _cjk_bigram_set = staticmethod(_PLUGIN._cjk_bigram_set)
    _drop_schedule_recital_chunks = _PLUGIN._drop_schedule_recital_chunks

    def __init__(self, verified_schedule: str, planned_schedule: str) -> None:
        self._material = (verified_schedule, planned_schedule)

    def _private_passive_schedule_material(self, current_user=None):
        return self._material

    def _plain_text_segments_from_chunks(self, chunks):
        return [str(item) for item in chunks]

    def _segmented_chunk_log_text(self, chunk):
        return str(chunk)


def _event(inbound: str) -> SimpleNamespace:
    return SimpleNamespace(message_str=inbound, unified_msg_origin="default:FriendMessage:1")


class ScheduleRecitalFilterTest(unittest.TestCase):
    def _filter(self, chunks, *, inbound="嗯", planned=_PLAN):
        plugin = _Dummy("", planned)
        return plugin._drop_schedule_recital_chunks(_event(inbound), chunks)

    def test_plan_recital_segment_is_dropped(self) -> None:
        keep, dropped = self._filter([_NORMAL, _RECITAL])
        self.assertEqual(dropped, 1)
        self.assertEqual(keep, [_NORMAL])

    def test_normal_segment_is_kept(self) -> None:
        keep, dropped = self._filter([_NORMAL])
        self.assertEqual(dropped, 0)
        self.assertEqual(keep, [_NORMAL])

    def test_activity_question_disables_filter(self) -> None:
        keep, dropped = self._filter([_NORMAL, _RECITAL], inbound="你刚才干嘛去了")
        self.assertEqual(dropped, 0)
        self.assertEqual(keep, [_NORMAL, _RECITAL])

    def test_all_recital_keeps_one_segment(self) -> None:
        keep, dropped = self._filter([_RECITAL, _RECITAL + " 而且"]) 
        self.assertEqual(len(keep), 1)
        self.assertEqual(dropped, 1)

    def test_without_plan_keeps_everything(self) -> None:
        keep, dropped = self._filter([_NORMAL, _RECITAL], planned="")
        self.assertEqual(dropped, 0)
        self.assertEqual(len(keep), 2)

    def test_overlap_ratios_have_margin(self) -> None:
        plugin = _Dummy("", _PLAN)
        plan_bigrams = plugin._cjk_bigram_set(_PLAN)
        recital_ratio = len(plan_bigrams & plugin._cjk_bigram_set(_RECITAL)) / len(
            plugin._cjk_bigram_set(_RECITAL)
        )
        normal_ratio = len(plan_bigrams & plugin._cjk_bigram_set(_NORMAL)) / len(
            plugin._cjk_bigram_set(_NORMAL)
        )
        self.assertGreaterEqual(recital_ratio, plugin._SCHEDULE_RECITAL_OVERLAP_THRESHOLD + 0.05)
        self.assertLess(normal_ratio, plugin._SCHEDULE_RECITAL_OVERLAP_THRESHOLD - 0.1)


if __name__ == "__main__":
    unittest.main()
