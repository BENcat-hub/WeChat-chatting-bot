# -*- coding: utf-8 -*-
"""日程候选必须按所有者过滤，否则别人的日程会被写进当前用户的生活时间线。"""
from __future__ import annotations

import inspect
import unittest

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin

A_USER = "1000000003"    # 用户B（规范 id）
A_LEGACY = "1000000004"  # 桃桃的旧数字 id
B_USER = "1000000001"    # 用户A（规范 id）
B_LEGACY = "1000000002"  # 用户A的旧数字 id

_ALIASES = {A_LEGACY: A_USER, A_USER: A_USER, B_LEGACY: B_USER, B_USER: B_USER}


class _Harness(ProactiveMessageMixin):
    """只保留日程候选提示段所需的最小宿主。"""

    def __init__(self, candidates: list[dict]) -> None:
        self._candidates = list(candidates)

    def _agenda_calendar_candidates_store(self) -> list[dict]:
        return [dict(item) for item in self._candidates]

    def _agenda_calendar_timeline(self, **_kwargs) -> dict:
        return {"current_phase": [{"title": "日常节奏"}]}

    def _canonical_private_user_id(self, value: str) -> str:
        return _ALIASES.get(str(value or "").strip(), str(value or "").strip())

    def persona_setting(self, key: str, default: object = None) -> object:
        return default


def _candidate(title: str, owner_key: str = "", owner_value: str = "") -> dict:
    item: dict[str, object] = {"title": title, "lifecycle_state": "candidate"}
    if owner_key:
        item[owner_key] = owner_value
    return item


class ProactiveCalendarOwnerFilterTests(unittest.TestCase):
    def _rendered(self, user_id: str, candidates: list[dict]) -> str:
        section = _Harness(candidates)._format_proactive_calendar_constraint_hint_section(user_id)
        return "" if section is None else str(section.content)

    def test_other_users_candidate_is_excluded(self):
        text = self._rendered(
            A_USER,
            [
                _candidate("复习英语", "target_user_id", A_USER),
                _candidate("去上体育课", "target_user_id", B_USER),
            ],
        )
        self.assertIn("复习英语", text)
        self.assertNotIn("体育", text)

    def test_legacy_owner_id_is_canonicalized(self):
        text = self._rendered(A_USER, [_candidate("去上体育课", "target_user_id", A_LEGACY)])
        self.assertIn("体育", text)

    def test_unlabelled_candidate_is_excluded(self):
        text = self._rendered(A_USER, [_candidate("去上体育课")])
        self.assertNotIn("体育", text)

    def test_missing_user_id_keeps_legacy_behaviour(self):
        text = self._rendered("", [_candidate("去上体育课", "target_user_id", B_USER)])
        self.assertIn("体育", text)

    def test_source_user_id_also_matches(self):
        text = self._rendered(A_USER, [_candidate("复习英语", "source_user_id", A_LEGACY)])
        self.assertIn("复习英语", text)

    def test_bare_hint_passes_user_id_through(self):
        harness = _Harness([_candidate("复习英语", "target_user_id", A_USER)])
        self.assertIn("复习英语", harness._format_proactive_calendar_constraint_hint(A_USER))

    def test_scene_context_filters_by_scene_owner(self):
        from astrbot_plugin_private_companion import scene_context

        source = inspect.getsource(scene_context)
        self.assertIn("scene_owner", source)
        self.assertIn("_proactive_calendar_candidate_owned_by(item, scene_owner)", source)

    def test_judge_and_delivery_prompts_pass_user_id(self):
        from astrbot_plugin_private_companion import proactive_engine

        engine_source = inspect.getsource(proactive_engine)
        self.assertIn('calendar_hint_getter(str(user.get("user_id") or ""))', engine_source)
        message_source = inspect.getsource(ProactiveMessageMixin)
        self.assertNotIn(
            "self._format_proactive_calendar_constraint_hint_section()",
            message_source,
        )
        self.assertNotIn(
            "self._format_proactive_calendar_constraint_hint()",
            message_source,
        )


if __name__ == "__main__":
    unittest.main()
