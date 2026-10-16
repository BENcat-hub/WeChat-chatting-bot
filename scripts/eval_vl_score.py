"""Blind-score generated images with a free VL model and compare arms.

Keeps the expensive judgement out of the main agent's context: the free model does
the scoring, this script only aggregates numbers.

Example:
  python scripts/eval_vl_score.py --arm a=A1.png,A2.png,A3.png --arm b=B1.png,B2.png,B3.png
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VL_MODEL = "nvidia/nemotron-3.5-super-vl:free"
LOG = ROOT / "logs" / "image-eval-vl.jsonl"

try:  # Windows consoles default to GBK and crash on non-ASCII model output
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

RUBRIC = (
    "Score this single generated image from 1 to 10 on each axis:\n"
    "head_proportion: head-to-body ratio naturalness (a chibi/oversized head or shrunken body scores low; a natural 1:6-1:7 figure scores high)\n"
    "hands: hand and finger quality (wrong finger count, melted hands score low)\n"
    "face: face clarity and appeal\n"
    "style: purity of 2D anime illustration style (photorealistic or 3D-looking rendering, or a mixed style, scores low)\n"
    "anatomy: neck, limbs, joints, silhouette correctness\n"
    "finish: overall polish and believability as a chat photo\n"
    "list_defects: short list of concrete visible defects, empty list if none\n"
    'Reply with JSON only, no prose: {"head_proportion":n,"hands":n,"face":n,"style":n,"anatomy":n,"finish":n,"list_defects":["..."]}'
)

AXES = ("head_proportion", "hands", "face", "style", "anatomy", "finish")


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
    ap.add_argument("--arm", action="append", default=[], help="name=path1,path2,...")
    ap.add_argument("--tag", default="vl")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if not args.arm:
        raise SystemExit("--arm name=path1,path2 is required")

    items: list[tuple[str, Path]] = []
    for spec in args.arm:
        name, _, paths = spec.partition("=")
        for raw in paths.split(","):
            raw = raw.strip()
            if raw:
                items.append((name.strip(), (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)))

    random.Random(args.seed).shuffle(items)
    labels = [chr(ord("A") + i) for i in range(len(items))]
    print("blind mapping: " + ", ".join(f"{lab}={p.name}({arm})" for lab, (arm, p) in zip(labels, items)))

    scores: dict[str, list[dict]] = {}
    for lab, (arm, path) in zip(labels, items):
        started = time.time()
        try:
            text = free_llm.call(VL_MODEL, RUBRIC, [str(path)], temperature=0.0, max_tokens=2500, timeout=240)
        except SystemExit as exc:
            print(f"{lab} {arm} {path.name}: ERROR {exc}")
            continue
        data = parse_json(text)
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tag": args.tag, "label": lab, "arm": arm, "file": path.name,
               "elapsed": round(time.time() - started, 1)}
        if not data:
            row["raw"] = (text or "")[-400:]
        for axis in AXES:
            row[axis] = data.get(axis)
        row["list_defects"] = data.get("list_defects")
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{lab} {arm} {path.name}: " + " ".join(f"{a}={row[a]}" for a in AXES) + f" defects={row['list_defects']}")
        scores.setdefault(arm, []).append(row)

    print("\n=== means ===")
    for arm, rows in scores.items():
        means = {}
        for axis in AXES:
            vals = [r[axis] for r in rows if isinstance(r[axis], (int, float))]
            means[axis] = round(sum(vals) / len(vals), 2) if vals else None
        total = [sum(r[a] for a in AXES) for r in rows if all(isinstance(r[a], (int, float)) for a in AXES)]
        print(arm, f"n={len(rows)}", means, "total_mean=" + (str(round(sum(total) / len(total), 2)) if total else "n/a"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
