# -*- coding: utf-8 -*-
"""主动消息：去掉无意义的自问/反问收尾，并让分段后的每段同样干净。

实测样本（2026-11-10 微信实收）：
    下午这几小时过得有点慢 手边也没急着要做的事 就先这么坐着?
    下午过去一大半了 冰箱里那袋速冻饺子 我还没想好晚上怎么弄?
两句都没有「你」、也没有任何真在发问的对象，只是把陈述句末尾换成了问号，
看起来像自问自答。做法两层：
1. 提示词层：`_proactive_expression_shape_prompt_section` 增加禁令；
2. 落地层：`_strip_meaningless_question_tail()` 逐句判断，只有「真的在问对方」才留问号。

用法：python patch_no_rhetorical_question.py [--apply]
"""
import io
import shutil
import sys
import time

P = "astrbot/data/plugins/astrbot_plugin_private_companion/proactive_message.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")

PROMPT_ANCHOR = '''            "除非语义确实需要，不要每句都用完整句号收尾；不要为了显得自然而堆叠解释、背景和客套。",
'''
PROMPT_NEW = '''            "除非语义确实需要，不要每句都用完整句号收尾；不要为了显得自然而堆叠解释、背景和客套。",
            "不要用反问句或自问自答收尾（例如「就先这么坐着？」「我还没想好晚上怎么弄？」这类只有自己在嘀咕的问句）："
            "陈述句直接把话说完就行。只有真的在问对方、而且问题具体（带「你」并落到对方身上）时才用问号。",
'''

METHOD = '''    _MEANINGLESS_QUESTION_TAIL_RE = re.compile(r"[\\s]*(?:吗|呢|吧)?\\s*[？?]\\s*$")

    def _is_real_question_to_user(self, sentence: str) -> bool:
        """判断一句带问号的话是不是真的在问对方。

        判据（任一命中即算真问句）：
        - 出现第二人称（你/您/你们/妳）且带疑问词（吗/呢/什么/怎么/哪/谁/几/多少/要不要/是不是/想不想/有没有/好不好）；
        - 或以「要不要/是不是/想不想/有没有/好不好/方便吗/在吗/睡了吗」这类面向对方的问句开头。
        没有第二人称的自问（「就先这么坐着？」）一律不算。
        """
        text = str(sentence or "").strip()
        if not text:
            return False
        if not re.search(r"你|您|妳", text):
            return False
        if re.search(
            r"吗|呢|什么|怎么|咋|哪|谁|几点|多少|要不要|是不是|想不想|有没有|好不好|行不行|对不对|可以吗|方便吗",
            text,
        ):
            return True
        return bool(
            re.match(r"^(?:要不要|是不是|想不想|有没有|好不好|行不行|在吗|睡了|醒了|忙吗|方便)", text)
        )

    def _strip_meaningless_question_tail(self, text: str) -> str:
        """把「没有对象的自问/反问」的句末问号改成句号；真问句保持原样。"""
        source = str(text or "").strip()
        if not source or not re.search(r"[？?]", source):
            return source
        units = re.split(r"(?<=[。！？!?])", source)
        out: list[str] = []
        changed = 0
        for unit in units:
            piece = unit
            if re.search(r"[？?]\\s*$", piece) and not self._is_real_question_to_user(piece):
                piece = self._MEANINGLESS_QUESTION_TAIL_RE.sub("。", piece)
                if piece == unit:
                    piece = re.sub(r"[？?]\\s*$", "。", piece)
                changed += 1
            out.append(piece)
        result = "".join(out).strip()
        if changed:
            logger.info(
                "主动消息去掉自问/反问收尾 %d 处: %s",
                changed,
                _single_line(result, 80),
            )
        return result

'''

CALL_ANCHOR = '''        finalized = self._normalize_proactive_sentence_flow(reviewed)
'''
CALL_NEW = '''        reviewed = self._strip_meaningless_question_tail(reviewed)
        finalized = self._normalize_proactive_sentence_flow(reviewed)
'''

src = io.open(P, encoding="utf-8").read()
checks = {
    "prompt_anchor": src.count(PROMPT_ANCHOR),
    "call_anchor": src.count(CALL_ANCHOR),
    "method_present": src.count("def _strip_meaningless_question_tail"),
}
print("checks:", checks)
if checks["method_present"]:
    print("already patched")
    raise SystemExit(0)
assert checks["prompt_anchor"] == 1, checks["prompt_anchor"]
assert checks["call_anchor"] == 1, checks["call_anchor"]

new = src.replace(PROMPT_ANCHOR, PROMPT_NEW)
new = new.replace(CALL_ANCHOR, CALL_NEW)

# 方法插到 _strip_morning_meal_questions 定义之前
marker = "    @staticmethod\n    def _strip_morning_meal_questions("
assert new.count(marker) == 1, new.count(marker)
new = new.replace(marker, METHOD + marker)

if "--apply" not in sys.argv:
    print("(dry-run，加 --apply 才写)")
    raise SystemExit(0)

shutil.copy2(P, f"{P}.bak-noquestion-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(new)
print("written, backup ->", f"proactive_message.py.bak-noquestion-{STAMP}")
