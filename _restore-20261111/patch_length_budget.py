# -*- coding: utf-8 -*-
"""给被动回复加「本轮字数预算」：每轮随机抽一档，从结构上制造长短变化。

为什么需要：光靠「请简洁」这类抽象指令，模型稳定输出 10~15 字的整句
（实测中位 10），而真人私聊中位只有 5 字、60% 的消息 ≤6 字。把「这一条
只准多短」变成每轮一个具体数字，模型更容易照做，长短也会自然交错。

实现：`main.py::_format_reply_style_prompt_section` 在拼好的风格段末尾追加一行
预算提示；档位与概率写死在 `_REPLY_BUDGET_TIERS`，可用设置
`humanize_length_budget`（默认 True）关掉。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")

ANCHOR = "    def _format_reply_style_prompt_section(self) -> PromptSection:"

HELPER = '''    _REPLY_BUDGET_TIERS: tuple[tuple[float, str], ...] = (
        (0.50, "≤6 字：只说一两个词也行，像\\"嗯\\"\\"那早点睡\\"\\"这么累\\""),
        (0.30, "≤12 字：一句短话，说完就停"),
        (0.20, "≤25 字：最多两句，中间用空格断，不要写成一段"),
    )

    def _reply_length_budget_hint(self) -> str:
        """本轮字数预算（随机抽档，制造真人那种长短交错）。"""
        if not bool(runtime_persona_setting(self, "humanize_length_budget", True)):
            return ""
        roll = random.random()
        picked = self._REPLY_BUDGET_TIERS[-1][1]
        acc = 0.0
        for weight, text in self._REPLY_BUDGET_TIERS:
            acc += weight
            if roll <= acc:
                picked = text
                break
        return (
            "【本轮字数预算】" + picked +
            "。预算优先：意思说到就停，不要为了铺垫加解释；"
            "对方在问正经事、需要步骤或较长说明时才可超出。"
        )

'''

NEEDLE = "        text = str(runtime_persona_setting(self, 'reply_style_prompt', \"\") or \"\").strip()"


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    if "_reply_length_budget_hint" in src:
        print("已经打过补丁，跳过")
        return 0
    if ANCHOR not in src:
        print("!! 找不到 _format_reply_style_prompt_section 定义")
        return 2
    if NEEDLE not in src:
        print("!! 找不到 reply_style_prompt 取值行")
        return 2

    src = src.replace(ANCHOR, HELPER + ANCHOR, 1)

    # 在取到 text 之后注入预算；只改这一处（该函数内首次出现）
    inject = (
        NEEDLE + "\n"
        "        _budget_hint = self._reply_length_budget_hint()\n"
        "        if _budget_hint:\n"
        "            text = (text + \"\\n\" + _budget_hint).strip() if text else _budget_hint"
    )
    idx = src.find(NEEDLE)
    src = src[:idx] + inject + src[idx + len(NEEDLE):]

    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print(f"patched {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
