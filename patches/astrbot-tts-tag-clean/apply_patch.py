import io, os, re, pathlib, shutil, time
SP = pathlib.Path(r"C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack\astrbot\.venv\Lib\site-packages\astrbot")
helper = SP / "core" / "utils" / "tts_text_clean.py"
helper.write_text('''"""Strip internal voice tags before handing text to a TTS provider.

Patched in (not upstream): <pc_tts>...</pc_tts>, <tts>, [[PCTTS:...]] must never be
spoken aloud. Upgrade of AstrBot overwrites this file - re-apply after upgrade.
"""
import re

_TAG = re.compile(r"</?(?:pc_[a-z0-9_]+|t{2,}s)\\b[^<>]*>", re.IGNORECASE)
_SENT = re.compile(r"\\[\\[\\s*PCTTS:[^\\]\\]]*\\]\\]", re.IGNORECASE)
_LITERAL = re.compile(r"(?:[A-Za-z]{1,4}\\s*[\\u4e0b_][\\u5212_]?\\u7ebf?\\s*(?:TTS|tts))|(?:_?(?:pc|tc)_tts)", re.IGNORECASE)


def clean_tts_text(text: str) -> str:
    if not text:
        return text
    out = _TAG.sub("", text)
    out = _SENT.sub("", out)
    out = _LITERAL.sub("", out)
    out = re.sub(r"[ \\t]{2,}", " ", out).strip()
    return out or text
''', encoding="utf-8")

stamp = time.strftime("%Y%m%d-%H%M%S")
targets = [SP/"core"/"pipeline"/"result_decorate"/"stage.py", SP/"core"/"astr_agent_run_util.py", SP/"core"/"provider"/"sources"/"edge_tts_source.py"]
for p in targets:
    bak = p.with_suffix(p.suffix + f".bak-ttsfix-{stamp}")
    if not bak.exists():
        shutil.copy2(p, bak)
    src = p.read_text(encoding="utf-8")
    if "clean_tts_text" in src:
        print("already", p.name); continue
    # inject import after first 'import' block line
    if p.name == "stage.py":
        src = src.replace("audio_path = await tts_provider.get_audio(comp.text)",
                          "audio_path = await tts_provider.get_audio(clean_tts_text(comp.text))", 1)
    elif p.name == "astr_agent_run_util.py":
        src = src.replace("audio_path = await tts_provider.get_audio(text)",
                          "audio_path = await tts_provider.get_audio(clean_tts_text(text))", 1)
    else:
        src = src.replace('kwargs = {"text": text, "voice": self.voice}',
                          'kwargs = {"text": clean_tts_text(text), "voice": self.voice}', 1)
    # add import right after the module docstring/first import
    lines = src.splitlines(True)
    idx = 0
    for i, ln in enumerate(lines[:60]):
        if ln.startswith("import ") or ln.startswith("from "):
            idx = i
            break
    lines.insert(idx, "from astrbot.core.utils.tts_text_clean import clean_tts_text\n")
    p.write_text("".join(lines), encoding="utf-8")
    print("patched", p.name)