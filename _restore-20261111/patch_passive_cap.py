# -*- coding: utf-8 -*-
"""被动回复也受字数上限约束：把过长的气泡切开，落地成多条。

为什么：测试证明「提示词里写死上限」的服从率只有 ~30%（强制 6 字档实测中位仍 8），
只靠语言约束到不了真人的分布。所以把上限做成结构强制：

1. `_reply_length_budget_hint()` 抽档时把上限记到 `self._last_budget_cap`，
   提示词仍照旧告诉模型（让模型先自己压短，减少后续切分）；
2. `_clean_segmented_reply_chunks()` 末尾按这个上限把过长的 Plain 文本切开，
   一个气泡拆成多个 chunk（最多 `segmented_proactive_max_segments` 条），
   这样微信里看到的就是几条短消息，而不是一段长话。

只切分、不改写；优先在逗号/顿号/分号/空格处断；没有分隔符的超长整块原样保留。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")

# 1) 抽档时记录上限
CAP_NEEDLE = '        forced = os.environ.get("HUMANIZE_FORCE_CAP", "").strip()'
CAP_INSERT = (
    CAP_NEEDLE + "\n"
    "        self._last_budget_cap = 0"
)

SETCAP_NEEDLE = '        return (\n            f"【硬性上限】这一条回复不得超过 {cap} 个字（{note}）。"'
SETCAP_INSERT = (
    '        self._last_budget_cap = int(cap)\n'
    '        return (\n            f"【硬性上限】这一条回复不得超过 {cap} 个字（{note}）。"'
)

# 2) chunk 清理器末尾加切分
CLEAN_NEEDLE = '        return cleaned_chunks\n\n    def _llm_controlled_segmenting_allowed'
CLEAN_INSERT = '''        cleaned_chunks = self._humanize_split_chunks(cleaned_chunks, event)
        return cleaned_chunks

    def _humanize_split_chunks(
        self,
        chunks: list[list[Any]],
        event: AstrMessageEvent | None = None,
    ) -> list[list[Any]]:
        """按本轮字数上限把过长的气泡拆成几条（只切分，不改写）。"""
        if not bool(runtime_persona_setting(self, "humanize_short_bubbles", True)):
            return chunks
        cap = int(getattr(self, "_last_budget_cap", 0) or 0)
        if cap < 4:
            return chunks
        try:
            max_segments = int(
                runtime_persona_setting(self, "segmented_proactive_max_segments", 4) or 4
            )
        except Exception:
            max_segments = 4
        max_segments = max(1, min(6, max_segments))

        def _split(text: str) -> list[str]:
            raw = re.split(r"([，,、；;：:\\s。！？!?…~～]+)", text)
            pieces: list[str] = []
            buf = ""
            for part in raw:
                if not part:
                    continue
                if re.fullmatch(r"[，,、；;：:\\s。！？!?…~～]+", part):
                    if buf:
                        pieces.append(buf)
                        buf = ""
                    continue
                buf += part
            if buf:
                pieces.append(buf)
            if not pieces:
                return [text]
            packed: list[str] = []
            cur = ""
            for piece in pieces:
                candidate = piece if not cur else (cur + " " + piece)
                if len(candidate) <= cap:
                    cur = candidate
                else:
                    if cur:
                        packed.append(cur)
                    cur = piece
                while len(cur) > cap:
                    # 宁可整块留长，也不半路砍断词句
                    packed.append(cur)
                    cur = ""
                    break
            if cur:
                packed.append(cur)
            return packed or [text]

        out: list[list[Any]] = []
        for chunk in chunks:
            plains = [comp for comp in chunk if isinstance(comp, Plain)]
            others = [comp for comp in chunk if not isinstance(comp, Plain)]
            if len(plains) != 1:
                out.append(chunk)
                continue
            text = str(getattr(plains[0], "text", "") or "").strip()
            if len(text) <= cap:
                out.append(chunk)
                continue
            pieces = _split(text)
            if len(pieces) <= 1:
                out.append(chunk)
                continue
            out.append([Plain(pieces[0])] + others)
            for piece in pieces[1:]:
                out.append([Plain(piece)])
        if len(out) > max_segments:
            merged: list[list[Any]] = out[: max_segments - 1]
            tail_texts = []
            for chunk in out[max_segments - 1:]:
                chunk_text = " ".join(
                    str(getattr(comp, "text", "") or "").strip()
                    for comp in chunk
                    if isinstance(comp, Plain)
                ).strip()
                if chunk_text:
                    tail_texts.append(chunk_text)
            if tail_texts:
                merged.append([Plain(" ".join(tail_texts))])
            out = merged

        try:
            setattr(event, "_private_companion_humanize_split", True)
        except Exception:
            pass
        return out

    def _llm_controlled_segmenting_allowed'''


def main() -> int:
    src = io.open(TARGET, encoding="utf-8", newline="").read()
    if "_humanize_split_chunks" in src:
        print("已经打过补丁，跳过")
        return 0
    for needle, label in ((CAP_NEEDLE, "forced"), (SETCAP_NEEDLE, "return cap"), (CLEAN_NEEDLE, "clean return")):
        if needle not in src:
            print(f"!! 找不到锚点 {label}")
            return 2
    src = src.replace(CAP_NEEDLE, CAP_INSERT, 1)
    src = src.replace(SETCAP_NEEDLE, SETCAP_INSERT, 1)
    src = src.replace(CLEAN_NEEDLE, CLEAN_INSERT, 1)
    io.open(TARGET, "w", encoding="utf-8", newline="").write(src)
    print(f"patched {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
