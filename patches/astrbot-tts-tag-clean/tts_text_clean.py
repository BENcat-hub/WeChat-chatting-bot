"""Strip internal voice tags before handing text to a TTS provider.

Patched in (not upstream): <pc_tts>...</pc_tts>, <tts>, [[PCTTS:...]] must never be
spoken aloud. Upgrade of AstrBot overwrites this file - re-apply after upgrade.
"""
import re

_TAG = re.compile(r"</?(?:pc_[a-z0-9_]+|t{2,}s)\b[^<>]*>", re.IGNORECASE)
_SENT = re.compile(r"\[\[\s*PCTTS:[^\]\]]*\]\]", re.IGNORECASE)
_LITERAL = re.compile(r"(?:[A-Za-z]{1,4}\s*[\u4e0b_][\u5212_]?\u7ebf?\s*(?:TTS|tts))|(?:_?(?:pc|tc)_tts)", re.IGNORECASE)


def clean_tts_text(text: str) -> str:
    if not text:
        return text
    out = _TAG.sub("", text)
    out = _SENT.sub("", out)
    out = _LITERAL.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return out or text
