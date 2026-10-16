"""Detect fake social-media watermarks / overlay text in generated images via a free VL model.

Seedream sometimes imitates Weibo-style watermarks ("weibo logo + @username" at the
bottom centre). This script measures how often that happens across a folder so a
mitigation can be judged with numbers instead of a hunch.

  python scripts/eval_vl_watermark.py logs/jimeng-out/eval/prop-*.png
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VL_MODEL = "nvidia/nemotron-3.5-super-vl:free"
LOG = ROOT / "logs" / "image-eval-watermark.jsonl"

try:  # Windows consoles default to GBK and crash on non-ASCII model output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

RUBRIC = (
    "Look only at overlays added on top of the picture: watermarks, logos, usernames, handles, dates, timestamps, "
    "signatures or caption text that look like they were stamped onto a social-media photo (for example a Weibo logo "
    "with an @username at the bottom centre). Normal objects in the scene (a mug, a book cover, a clock on the wall) do not count.\n"
    'Reply with JSON only: {"overlay":true|false,"what":"short description or empty","where":"bottom-center|bottom-left|bottom-right|top|other|none"}'
)


def parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="watermark")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    rows = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_absolute():
            path = (ROOT / raw).resolve()
        if not path.exists():
            print(f"missing {path}")
            continue
        try:
            text = free_llm.call(VL_MODEL, RUBRIC, [str(path)], temperature=0.0, max_tokens=1600, timeout=200)
            data = parse_json(text)
        except SystemExit as exc:
            data = {"error": str(exc)[:120]}
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tag": args.tag,
            "file": path.name,
            "overlay": data.get("overlay"),
            "what": data.get("what"),
            "where": data.get("where"),
        }
        if not data:
            row["raw"] = text[-200:] if isinstance(text, str) else ""
        rows.append(row)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{row['file']}: overlay={row['overlay']} where={row['where']} what={row['what']}")

    flagged = [r for r in rows if r.get("overlay") is True]
    print(f"\n=== summary: {len(flagged)}/{len(rows)} images carry an overlay ===")
    for r in flagged:
        print("  " + r["file"] + " -> " + str(r.get("what"))[:90])
    return 0


if __name__ == "__main__":
    sys.exit(main())
