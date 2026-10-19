# -*- coding: utf-8 -*-
"""纯场景请求不得被注入人设句。

2026-10-17 事故：`photo_text` 把「桌上一杯热牛奶」判成纯场景（`use_persona_reference=False`、
`subject_owner='scene'`，还追加了 people/faces 负面词），因此**没有**参考图；但
`image_runtime._apply_photo_generation_prompt_format()` 和
`photo_prompt_context._critical_requirements_tail()` 两层都无条件追加
「Petite slender teen girl / about 7 heads tall / 蓝发鲸鳍耳 / Keep the reference face /
Pose override」，模型于是凭空画了一个没有身份来源的 Q 版小女孩站在杯子旁边。

修复：两层都接受 `character_present`，为 False 时只保留画风、水印与负面词。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_image_companion.image_runtime import ImageGenerationRuntime
from astrbot_plugin_image_companion.photo_prompt_context import _critical_requirements_tail

_SCENE = (
    "A quiet late-night desk corner at home, a ceramic mug of warm milk with "
    "gentle steam rising, warm lamp light, blurred dark room behind"
)
_CHARACTER_MARKERS = (
    "about 7 heads tall",
    "whale-fin ears",
    "Pose override",
    "Keep the reference face",
    "five clear fingers",
)


class SceneWithoutCharacterTest(unittest.TestCase):
    def test_tail_skips_character_clauses_for_scene(self) -> None:
        tail = _critical_requirements_tail("chibi", character_present=False)

        for marker in _CHARACTER_MARKERS:
            self.assertNotIn(marker, tail)
        # 纯场景不写画风句（2026-10-18：写了会把「拍午饭」顶成平涂插画），
        # 水印与负面词仍然必须保留（水印是唯一的句子级要求，不能丢）。
        self.assertNotIn("2D anime illustration", tail)
        self.assertIn("No watermark", tail)
        self.assertIn("Avoid ", tail)

    def test_tail_uses_photographic_style_for_pure_scene(self) -> None:
        tail = _critical_requirements_tail("chibi", character_present=False, photographic=True)

        self.assertIn("Realistic photo look", tail)
        self.assertNotIn("2D anime illustration", tail)
        for marker in _CHARACTER_MARKERS:
            self.assertNotIn(marker, tail)
        # 人物画面的画风不受影响。
        char_tail = _critical_requirements_tail("chibi", character_present=True, photographic=True)
        self.assertIn("2D anime illustration", char_tail)
        self.assertNotIn("Realistic photo look", char_tail)

    def test_tail_keeps_character_clauses_by_default(self) -> None:
        tail = _critical_requirements_tail("chibi")

        for marker in ("about 7 heads tall", "whale-fin ears", "Pose override", "five clear fingers"):
            self.assertIn(marker, tail)

    def test_person_detection(self) -> None:
        detect = ImageGenerationRuntime._photo_prompt_mentions_person
        self.assertFalse(detect(_SCENE))
        self.assertFalse(detect("a bowl of freshly cooked pork wontons on a wooden table"))
        self.assertFalse(detect("university campus at dusk"))
        self.assertFalse(detect(""))
        self.assertTrue(detect("自拍一张，穿校服"))
        self.assertTrue(detect("a girl sitting by the window"))
        self.assertTrue(detect("她站在窗边回头"))

    def test_person_detection_ignores_negation_clause(self) -> None:
        """2026-10-18 事故：纯场景请求尾部被追加的否定句里带 person/character，不能算「有人」。"""

        detect = ImageGenerationRuntime._photo_prompt_mentions_person
        pure_scene_with_guard = (
            "Japanese anime illustration style, a cozy home dining table scene shot from a "
            "slightly high three-quarter angle, a bowl of rice and a plate of braised pork; "
            "do not insert an unrequested person, character, visible photographer, "
            "back-view figure, face, body, silhouette, or reflection"
        )
        self.assertFalse(detect(pure_scene_with_guard))
        self.assertFalse(detect("a quiet desk at night, no person, no people"))
        # 正向部分真的有人时仍然要识别出来。
        self.assertTrue(detect("the girl sits by the table, avoid crowded composition"))

    def test_scene_with_guard_suffix_has_no_character_clauses(self) -> None:
        runtime = object.__new__(ImageGenerationRuntime)
        prompt_text = (
            "Japanese anime illustration style, a cozy home dining table scene, a bowl of rice "
            "and a plate of braised pork on the wooden table; do not insert an unrequested "
            "person, character, face, body, silhouette"
        )

        character_present = ImageGenerationRuntime._photo_prompt_mentions_person(prompt_text)
        prompt = runtime._apply_photo_generation_prompt_format(
            prompt_text,
            prompt_format="natural_language",
            character_present=character_present,
        )

        self.assertFalse(character_present)
        for marker in _CHARACTER_MARKERS:
            self.assertNotIn(marker, prompt)

    def test_natural_language_prompt_for_scene_has_no_character(self) -> None:
        runtime = object.__new__(ImageGenerationRuntime)

        prompt = runtime._apply_photo_generation_prompt_format(
            _SCENE,
            prompt_format="natural_language",
            character_present=False,
        )

        self.assertTrue(prompt.startswith("Create a single coherent image showing A quiet late-night"))
        for marker in _CHARACTER_MARKERS:
            self.assertNotIn(marker, prompt)
        self.assertIn("No watermark", prompt)
        self.assertLessEqual(len(prompt), 800)

    def test_natural_language_prompt_for_photographic_scene(self) -> None:
        """2026-10-18：纯场景要求写实（午饭饭菜）时不能再出二次元插画。"""

        runtime = object.__new__(ImageGenerationRuntime)
        request = _SCENE + ", realistic food photography, natural light, true-to-life texture"

        prompt = runtime._apply_photo_generation_prompt_format(
            request,
            prompt_format="natural_language",
            character_present=False,
        )

        self.assertTrue(runtime._photo_prompt_wants_photographic_style(request, character_present=False))
        self.assertTrue(prompt.startswith("A realistic photo of "), prompt[:40])
        self.assertIn("Realistic photograph", prompt)
        self.assertNotIn("2D anime", prompt)
        for marker in _CHARACTER_MARKERS:
            self.assertNotIn(marker, prompt)
        self.assertLessEqual(len(prompt), 800)
        # 人物画面永远不走写实分支。
        self.assertFalse(
            runtime._photo_prompt_wants_photographic_style(request, character_present=True)
        )

    def test_natural_language_prompt_for_character_is_unchanged(self) -> None:
        runtime = object.__new__(ImageGenerationRuntime)

        prompt = runtime._apply_photo_generation_prompt_format(
            "casual selfie in the bedroom",
            prompt_format="natural_language",
            character_present=True,
        )

        self.assertIn("about 7 heads tall", prompt)
        self.assertIn("whale-fin ears", prompt)
        self.assertIn("Pose override", prompt)


if __name__ == "__main__":
    unittest.main()
