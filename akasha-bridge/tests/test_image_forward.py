"""图片转发策略单测：direct（默认转发原图）/ caption / both。

运行：cwd=akasha-bridge，`python -m unittest discover -s tests -p test_image_forward.py`
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from image_forward import (DEFAULT_IMAGE_FORWARD_MODE, build_image_marker,
                           build_image_payload, file_uri, normalize_forward_mode,
                           text_to_segments)

BRIDGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NormalizeModeTest(unittest.TestCase):
    def test_known_modes_pass_through(self):
        for mode in ("direct", "caption", "both"):
            self.assertEqual(mode, normalize_forward_mode(mode))

    def test_case_and_space_tolerant(self):
        self.assertEqual("caption", normalize_forward_mode(" CAPTION "))

    def test_invalid_falls_back_to_direct(self):
        for value in (None, "", "nope", 123, "direct2"):
            self.assertEqual(DEFAULT_IMAGE_FORWARD_MODE, normalize_forward_mode(value))


class FileUriTest(unittest.TestCase):
    def test_uri_escapes_spaces(self):
        path = os.path.join(tempfile.gettempdir(), "wechat_images", "a b.jpg")
        uri = file_uri(path)
        self.assertTrue(uri.startswith("file:///"))
        self.assertIn("a%20b.jpg", uri)


class BuildPayloadTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), "wechat_images", "wechat_1.jpg")

    def test_direct_forwards_original_only(self):
        payload = build_image_payload(self.path, "描述不该出现", "direct")
        self.assertIn("[CQ:image,file=file:///", payload)
        self.assertNotIn("描述不该出现", payload)

    def test_caption_keeps_old_shape(self):
        self.assertEqual("[图片: 一只猫]", build_image_payload(self.path, "一只猫", "caption"))

    def test_both_contains_marker_and_caption(self):
        payload = build_image_payload(self.path, "一只猫", "both")
        self.assertIn("[CQ:image,file=file:///", payload)
        self.assertIn("[图片: 一只猫]", payload)

    def test_missing_path_degrades_to_text(self):
        self.assertEqual("[图片: 一只猫]", build_image_payload(None, "一只猫", "direct"))
        self.assertEqual("[图片: （图片内容无法描述）]", build_image_payload(None, None, "direct"))

    def test_marker_uses_file_uri(self):
        self.assertEqual(f"[CQ:image,file={file_uri(self.path)}]", build_image_marker(self.path))


class TextToSegmentsTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), "wechat_images", "x.jpg")
        self.marker = build_image_marker(self.path)

    def test_plain_text_single_segment(self):
        self.assertEqual([{"type": "text", "data": {"text": "你好"}}], text_to_segments("你好"))

    def test_empty_text_still_returns_text_segment(self):
        self.assertEqual([{"type": "text", "data": {"text": ""}}], text_to_segments(""))

    def test_marker_in_middle_splits_text_image_text(self):
        segments = text_to_segments(f"看图 {self.marker} 好看吗")
        self.assertEqual(["text", "image", "text"], [s["type"] for s in segments])
        self.assertEqual("看图 ", segments[0]["data"]["text"])
        self.assertEqual(file_uri(self.path), segments[1]["data"]["file"])
        self.assertEqual(" 好看吗", segments[2]["data"]["text"])

    def test_marker_only(self):
        segments = text_to_segments(self.marker)
        self.assertEqual(["image"], [s["type"] for s in segments])

    def test_two_markers_keep_order(self):
        segments = text_to_segments(f"a{self.marker}b{self.marker}c")
        self.assertEqual(["text", "image", "text", "image", "text"], [s["type"] for s in segments])
        self.assertEqual(["a", "b", "c"],
                         [s["data"]["text"] for s in segments if s["type"] == "text"])


class BridgeCoreWiringTest(unittest.TestCase):
    def _source(self, name):
        with open(os.path.join(BRIDGE_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_process_sender_converts_markers_to_segments(self):
        src = self._source("bridge_core.py")
        self.assertIn("text_to_segments(combined)", src)
        self.assertIn("text_to_segments(formatted)", src)

    def test_image_paths_use_payload_builder(self):
        src = self._source("bridge_core.py")
        self.assertIn("_image_payload(", src)
        self.assertNotIn("[图片: {caption or '无法描述'}]", src)

    def test_config_and_panel_expose_mode(self):
        self.assertIn("IMAGE_FORWARD_MODE", self._source("config.py"))
        self.assertIn("image_forward_mode", self._source("state.py"))
        self.assertIn("image_forward_mode", self._source("web_panel.py"))


class DirectModeSkipsCaptionModelTest(unittest.TestCase):
    def test_direct_mode_never_calls_caption_model(self):
        import bridge_core

        called = []
        original = bridge_core.caption_image_via_ollama
        bridge_core.caption_image_via_ollama = lambda path: called.append(path) or "不该被调用"
        try:
            payload = bridge_core._image_payload("C:/tmp/wechat_images/x.jpg", "direct")
        finally:
            bridge_core.caption_image_via_ollama = original

        self.assertEqual([], called)
        self.assertIn("[CQ:image,file=file:///", payload)

    def test_caption_mode_calls_caption_model(self):
        import bridge_core

        called = []
        original = bridge_core.caption_image_via_ollama
        bridge_core.caption_image_via_ollama = lambda path: called.append(path) or "一只猫"
        try:
            payload = bridge_core._image_payload("C:/tmp/wechat_images/x.jpg", "caption")
        finally:
            bridge_core.caption_image_via_ollama = original

        self.assertEqual(1, len(called))
        self.assertEqual("[图片: 一只猫]", payload)


if __name__ == "__main__":
    unittest.main()
