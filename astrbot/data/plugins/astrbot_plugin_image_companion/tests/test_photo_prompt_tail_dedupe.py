# -*- coding: utf-8 -*-
"""尾部要求去重：同一句身份/比例约束不得在最终提示词里出现两遍。

背景（2026-10-16 03:2x）：生图提示词是**两层独立拼装**——`image_runtime.
_apply_photo_generation_prompt_format()` 先带自己的预算拼一次（比例句、身份句、姿势、水印、负面词），
`photo_prompt_context._assemble()` 再拼一次并追加 `_critical_requirements_tail()`。两层互不知道对方
写了什么，于是短请求（离线复现用的是「画一只在窗台上睡觉的橘猫」）里比例句与身份句各写了两遍，
而且一只猫的画面还被追加了 `Pose override: … standing in profile by the window holding the curtain.`。

这组用例锁住三条不变式：
1. 没有头部信息时，尾部必须补齐全部关键要求（宁可重复也不能丢）；
2. 头部已经写明的要求，尾部不再重复；
3. 无论是否去重，`_assemble()` 的输出都必须 ≤ 800 字符且保留 `\\n\\nAvoid ` 段落切分契约。
"""

from __future__ import annotations

import unittest

from astrbot_plugin_image_companion.photo_prompt_context import (
    _PROMPT_CHAR_BUDGET,
    PhotoPromptSection,
    _assemble,
    _critical_requirements_tail,
)

_REQUIRED_MARKERS = (
    "about 7 heads tall",
    "light-blue gradient tips",
    "whale-fin ears",
    "five clear fingers",
    "No watermark",
    "Avoid ",
)

# 模拟 image_runtime 在短请求里保留在头部的那些句子（全部小写，和 `_assemble` 传入的格式一致）。
_HEAD_WITH_EVERY_REQUIREMENT = (
    "create a single coherent image showing petite slender teen girl, small head, about 7 heads "
    "tall, long legs, a cat sleeping on the windowsill keep the reference face, hair colour and "
    "whale-fin ears, her whale tail grows from her lower back above the hips pose override: "
    "framing - close upper-body shot with a blurred background; pose - standing in profile by the "
    "window holding the curtain. hands with five clear fingers, not oversized. no watermark, logo, "
    "handle or caption."
)


class TailDedupeTests(unittest.TestCase):
    def test_tail_without_head_keeps_every_requirement(self) -> None:
        tail = _critical_requirements_tail("chibi")

        for marker in _REQUIRED_MARKERS:
            self.assertIn(marker, tail)

    def test_tail_skips_requirements_already_written_in_head(self) -> None:
        tail = _critical_requirements_tail("chibi", _HEAD_WITH_EVERY_REQUIREMENT)

        for marker in ("about 7 heads tall", "whale-fin ears", "Pose override:", "five clear fingers"):
            self.assertNotIn(marker, tail)
        # 头部没写的仍然要补（这里是发色锚），否定句也必须保留。
        self.assertIn("light-blue gradient tips", tail)
        self.assertIn("Avoid ", tail)

    def test_tail_always_keeps_watermark_sentence(self) -> None:
        """水印句**故意不去重**。

        2026-10-16 03:3x 的离线拼装审计里 `selfie-短` 出现过「头部的 `No watermark…` 被预算裁掉、
        尾部的同一句又被 `carried()` 跳过」的窗口，最终提示词里一个字都不含 watermark；同期生产图
        的伪水印扫描是 12.5%（`scripts/eval_vl_defects.py`）。这 40 个字符是买保险。
        """
        tail = _critical_requirements_tail("chibi", _HEAD_WITH_EVERY_REQUIREMENT)

        self.assertIn("No watermark", tail)

    def test_tail_rewrites_pose_when_head_pose_is_truncated(self) -> None:
        """头部的 `Pose override:` 被裁成半截时必须补一条完整的。

        头部来自 `image_runtime` 的拼装，`head_budget` 经常正好切在 `Pose override:` 里面，
        只按 "pose override" 去重会让最终提示词留下一条没有机位/姿势内容的姿势要求。
        """
        truncated_head = _HEAD_WITH_EVERY_REQUIREMENT.split("pose override:")[0] + "pose override:"

        tail = _critical_requirements_tail("chibi", truncated_head)

        self.assertIn("Pose override: framing -", tail)
        self.assertIn("; pose -", tail)

    def test_assemble_keeps_single_copy_and_avoid_paragraph(self) -> None:
        sections = [
            PhotoPromptSection(
                "user_request",
                "user_request",
                "画一只在窗台上睡觉的橘猫, 阳光洒进来",
            ),
        ]

        prompt = _assemble(sections, "natural_language")

        self.assertLessEqual(len(prompt), 800)
        self.assertLessEqual(len(prompt), _PROMPT_CHAR_BUDGET + 4)
        self.assertIn("\n\nAvoid ", prompt)
        self.assertLessEqual(prompt.count("about 7 heads tall"), 1)
        self.assertLessEqual(prompt.count("whale-fin ears"), 1)
        self.assertIn("five clear fingers", prompt)

    def test_assemble_dedupes_when_head_carries_persona_anchors(self) -> None:
        # 复现真实回归场景：头部（来自 image_runtime 的拼装）已经含比例句与身份句，
        # 去重前这里会各出现两遍（`about 7 heads tall` 见头部 + 尾部）。
        sections = [
            PhotoPromptSection("user_request", "user_request", "画一只在窗台上睡觉的橘猫, 阳光洒进来"),
            PhotoPromptSection(
                "scene_preset",
                "fixed_prompt",
                "Petite slender teen girl, small head, about 7 heads tall, long legs, "
                "Keep the reference face, hair colour and whale-fin ears, her whale tail.",
            ),
        ]

        prompt = _assemble(sections, "natural_language")

        self.assertLessEqual(len(prompt), 800)
        self.assertIn("\n\nAvoid ", prompt)
        self.assertEqual(prompt.count("about 7 heads tall"), 1)
        self.assertEqual(prompt.count("whale-fin ears"), 1)

    def test_assemble_never_loses_identity_when_head_is_clipped(self) -> None:
        # 头部很长 → 会被裁掉尾巴，此时尾部必须把身份/比例要求补回来。
        sections = [
            PhotoPromptSection(
                "user_request",
                "user_request",
                "scene detail " * 60 + "画一只在窗台上睡觉的橘猫",
            ),
        ]

        prompt = _assemble(sections, "natural_language")

        self.assertLessEqual(len(prompt), 800)
        self.assertIn("\n\nAvoid ", prompt)
        self.assertIn("whale-fin ears", prompt)
        self.assertIn("about 7 heads tall", prompt)
        # 宁可重复也不能丢：允许出现两遍，但绝不允许 >2。
        self.assertLessEqual(prompt.count("about 7 heads tall"), 2)


if __name__ == "__main__":
    unittest.main()
