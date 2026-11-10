# -*- coding: utf-8 -*-
"""主动消息用自己的一套字数预算，别和被动回复共用 2/6/12/25 字档。

原因：被动接话要短（真人中位 5 字），但主动开口是「分享一小段」
（真人偶尔也会写 15~30 字），用 2 字档会变成没头没尾的碎片。
两套档位分开，切条上限（_last_budget_cap）也跟着各自的档位走。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")
PROACTIVE = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "proactive_message.py")

HOOK_ANCHOR = "            _budget = self._reply_length_budget_hint()"

NEW_METHOD = '''    _PROACTIVE_BUDGET_TIERS: tuple[tuple[float, int, str], ...] = (
        (0.25, 12, "≤12 字：一句短话，像顺口丢过来的一句"),
        (0.55, 22, "≤22 字：一句到两句，说完就停"),
        (0.20, 35, "≤35 字：最多两句，中间用空格断"),
    )

    def _proactive_length_budget_hint(self) -> str:
        """主动消息的字数预算：比被动接话宽松一档，避免碎片化。"""
        if not bool(runtime_persona_setting(self, "humanize_length_budget", True)):
            self._last_budget_cap = 0
            return ""
        roll = random.random()
        picked_cap, picked_text = self._PROACTIVE_BUDGET_TIERS[-1][1], self._PROACTIVE_BUDGET_TIERS[-1][2]
        acc = 0.0
        for weight, cap, text in self._PROACTIVE_BUDGET_TIERS:
            acc += weight
            if roll <= acc:
                picked_cap, picked_text = cap, text
                break
        self._last_budget_cap = picked_cap
        return "【本轮字数预算】" + picked_text + "。主动开口不必凑字，意思到了就停。"

'''


def main() -> int:
    src = io.open(MAIN, encoding="utf-8", newline="").read()
    if "_proactive_length_budget_hint" in src:
        print("主动预算已存在，跳过 main.py")
    else:
        if HOOK_ANCHOR not in src:
            print("!! 找不到主动路径上的 _reply_length_budget_hint 调用")
            return 2
        src = src.replace(HOOK_ANCHOR, "            _budget = self._proactive_length_budget_hint()", 1)
        method_anchor = "    def _format_proactive_voice_prompt_sections(self) -> list[PromptSection]:"
        if method_anchor not in src:
            print("!! 找不到 _format_proactive_voice_prompt_sections")
            return 2
        src = src.replace(method_anchor, NEW_METHOD + method_anchor, 1)
        io.open(MAIN, "w", encoding="utf-8", newline="").write(src)
        print("patched main.py")

    psrc = io.open(PROACTIVE, encoding="utf-8", newline="").read()
    old = "多数控制在 20~45 字。"
    if old in psrc:
        psrc = psrc.replace(old, "多数控制在 12~30 字（主动开口，不必凑满一整段）。", 1)
        io.open(PROACTIVE, "w", encoding="utf-8", newline="").write(psrc)
        print("patched proactive_message.py")
    else:
        print("proactive_message.py 已改过或找不到 20~45 字 那句")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
