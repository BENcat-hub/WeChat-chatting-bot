"""Ask a free VL model for a short factual description of each image.

Used to check that variation knobs (outfit rotation, pose, scene) still work after a
prompt-budget change: if six generations all come back with the same outfit, the
wardrobe clause is being trimmed away.

  python scripts/eval_vl_describe.py --tag outfit logs/jimeng-out/eval/*.png
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VL_MODEL = "nvidia/nemotron-3.5-super-vl:free"
LOG = ROOT / "logs" / "image-eval-describe.jsonl"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

RUBRIC = (
    "Describe this image in one compact JSON object and nothing else: "
    '{"subject":"who/what","outfit":"the exact clothing worn, top and bottom and shoes","hair":"hair colour and style",'
    '"pose":"body pose and camera framing","scene":"place, time of day, light","extra":"notable objects"}'
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--tag", default="describe")
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
            import re

            text = free_llm.call(VL_MODEL, RUBRIC, [str(path)], temperature=0.0, max_tokens=1800, timeout=200)
            match = re.search(r"\{.*\}", text or "", re.S)
            data = json.loads(match.group(0)) if match else {"raw": (text or "")[-200:]}
        except SystemExit as exc:
            data = {"error": str(exc)[:150]}
        except Exception as exc:  # noqa: BLE001
            data = {"error": f"{type(exc).__name__}: {exc}"[:150]}
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tag": args.tag, "file": path.name, **data}
        rows.append(row)
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{path.name}: outfit={data.get('outfit')} | pose={data.get('pose')} | scene={data.get('scene')} | hair={data.get('hair')}")
    outfits = [str(r.get("outfit") or "") for r in rows]
    unique = len({o.lower()[:40] for o in outfits if o})
    print(f"\n=== {unique} distinct outfits across {len(outfits)} images ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
