"""List every request/passive prompt-injection snapshot with local time + session + vision marker."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

STORE = Path(r"<REPO_ROOT>\astrbot\data\plugin_data\astrbot_plugin_private_companion\companions.json")
TZ = timezone(timedelta(hours=8))
MARKERS = ("图片内容", "本轮图片", "视觉摘要", "图片视觉", "你能看到图")

data = json.loads(STORE.read_text(encoding="utf-8"))
root = data.get("recent_prompt_injections") or {}
for kind in ("request", "passive", "proactive", "tts"):
    items = root.get(kind) or []
    print(f"\n===== kind={kind} entries={len(items)}")
    for idx, record in enumerate(items):
        if not isinstance(record, dict):
            continue
        ts = record.get("ts")
        when = datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M:%S") if isinstance(ts, (int, float)) else "?"
        content = record.get("content") if isinstance(record.get("content"), str) else ""
        hit = [m for m in MARKERS if m in content]
        print(f"  [{idx}] {when} session={record.get('session')} title={str(record.get('title'))[:24]!r} mode={record.get('mode')!r} chars={record.get('chars')} markers={hit}")
        if kind in ("request", "passive") and hit:
            pos = content.find(hit[0])
            print("        witness:", content[max(0, pos - 200):pos + 260].replace("\n", " / "))
