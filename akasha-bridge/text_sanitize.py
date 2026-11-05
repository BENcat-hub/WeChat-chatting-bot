"""桥层出站文本清洗：把内部协议标记从可见正文里剥掉。

用户 2026-11-05 反馈：微信里收到 `<pc_tts>` 与「TC下划线TTS…」这种标签字面词。
所有发往微信的文本都会经过 sender.send_text()，所以清洗放在这里做成最后一道闸。

顺序很重要：先按完整标签剥（`</pc_tts>`、`<pc_tts ...>`），再清「被拼成字面词」的残留
（PC下划线TTS / tc_tts），最后收尾空白与空括号 —— 反过来说，先剥字面词会把
`</pc_tts>` 切成 `</>`。
"""
from __future__ import annotations

import re

# 1) 完整标签：<pc_tts> / <pc_tts foo="1"> / </pc_tts> / <tts> / </tts>
_INTERNAL_TAG_RE = re.compile(r"</?(?:pc_[a-z0-9_]+|t{2,}s)\b[^<>]*>", re.IGNORECASE)

# 2) 哨兵：[[PCTTS:...]]
_SENTINEL_RE = re.compile(r"\[\[PCTTS:[^\]]*\]\]", re.IGNORECASE)

# 3) 被拼成字面词的残留：PC下划线TTS、TC下划线TTS、pc_tts、tc_tts、[pc_tts]
_LITERAL_RE = re.compile(
    r"(?:[A-Za-z]{1,4}\s*[下_][划_]?线?\s*(?:TTS|tts))"
    r"|(?:_?(?:pc|tc)_tts)"
    r"|(?:\[\s*/?\s*(?:pc_)?t{2,}s\s*\])",
    re.IGNORECASE,
)

_SPACE_RUN_RE = re.compile(r"[ \t]{2,}")
_LEAD_NOISE_RE = re.compile(r"^[ \t,，、。；;:：\-]+")
_EMPTY_BRACKETS_RE = re.compile(r"\[\]|【】|</?>|<[^<>]{0,2}>")
_NEWLINES_RE = re.compile(r"(?:\r?\n){3,}")


def sanitize_outbound_text(text: str) -> str:
    """去掉内部标记，保留真正要给人看的话。"""
    source = str(text or "")
    if not source:
        return ""
    if not (_INTERNAL_TAG_RE.search(source) or _SENTINEL_RE.search(source) or _LITERAL_RE.search(source)):
        return source
    cleaned = _SENTINEL_RE.sub("", source)
    cleaned = _INTERNAL_TAG_RE.sub("", cleaned)
    cleaned = _LITERAL_RE.sub("", cleaned)
    cleaned = _EMPTY_BRACKETS_RE.sub("", cleaned)
    cleaned = _SPACE_RUN_RE.sub(" ", cleaned)
    cleaned = _LEAD_NOISE_RE.sub("", cleaned)
    cleaned = _NEWLINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()
