# -*- coding: utf-8 -*-
"""出站聊天排版：句号换空格、逗号换空格，代码片段不处理。"""
import unittest

from ..helpers import _apply_wechat_chat_style


class WechatChatStyleTest(unittest.TestCase):
    def test_period_becomes_space(self):
        self.assertEqual(_apply_wechat_chat_style("我吃完了。你呢"), "我吃完了 你呢")

    def test_trailing_period_removed(self):
        self.assertEqual(_apply_wechat_chat_style("行吧。"), "行吧")

    def test_comma_becomes_space(self):
        self.assertEqual(_apply_wechat_chat_style("行吧，那我先去了"), "行吧 那我先去了")

    def test_consecutive_commas_collapse(self):
        self.assertEqual(_apply_wechat_chat_style("哈哈，，真的假的"), "哈哈 真的假的")

    def test_thousands_separator_kept(self):
        self.assertEqual(_apply_wechat_chat_style("3,000 块"), "3,000 块")

    def test_question_and_tilde_kept(self):
        self.assertEqual(_apply_wechat_chat_style("真的吗？好耶~"), "真的吗？好耶~")

    def test_code_span_untouched(self):
        text = "看 `a。b` 这段"
        self.assertEqual(_apply_wechat_chat_style(text), text)

    def test_empty_and_blanks(self):
        self.assertEqual(_apply_wechat_chat_style(""), "")
        self.assertEqual(_apply_wechat_chat_style(None), "")
        self.assertEqual(_apply_wechat_chat_style("你好。  "), "你好")


if __name__ == "__main__":
    unittest.main()
