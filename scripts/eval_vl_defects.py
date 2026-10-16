"""Tally visible defects across a batch of generated images with a free VL model.

Unlike eval_vl_score.py (which gives 1-10 scores per arm), this script asks for a
defect tag list per image and aggregates them into a ranked table, so we can see
"which structural defect shows up most often" instead of just an average score.

Example:
  python scripts/eval_vl_defects.py --tag prod --limit 24            # 最新的 24 张生产图
  python scripts/eval_vl_defects.py --tag prod --files a.png b.png
  # 按改动时间把两批图分开统计缺陷率（文件名里带 YYYYmmdd_HHMMSS）：
  python scripts/eval_vl_defects.py --tag before --older-than 20261016_015800 --limit 12
  python scripts/eval_vl_defects.py --tag after  --newer-than 20261016_015800 --limit 12
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import free_llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
VL_MODEL = "nvidia/nemotron-3.5-super-vl:free"
PHOTO_DIR = (
    ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_private_companion" / "generated_photos"
)
EVAL_DIR = ROOT / "logs" / "jimeng-out" / "eval"
OUT = EVAL_DIR / "vl-defects.jsonl"

TAGS = (
    "hands",            # 手指数量/融合/过长/缺指
    "tail_attachment",  # 鲸尾与腰腿衔接不自然、像人鱼尾
    "head_ratio",       # 头身比偏大/偏 Q
    "watermark",        # 画面或道具上的文字/水印/商标
    "extra_limbs",      # 多手多腿
    "clothing",         # 服装结构错误（缺失、错位、不合身）
    "face",             # 脸部崩坏或身份漂移
    "background",       # 背景结构错误（透视、物体融合）
)

PROMPT = (
    "You are auditing one generated anime illustration for defects. "
    "Look at the image and report the defects that are actually visible. "
    "Choose tags only from this vocabulary: " + ", ".join(TAGS) + ". "
    "Do not invent defects; an empty list is a valid answer for a clean image. "
    "For each defect add a very short English note (max 12 words). "
    'Reply with JSON only: {"defects":[{"tag":"hands","note":"..."}],"clean":false}'
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def parse_json(text: str) -> dict:
    import re

    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {}


_STAMP_RE = None


def _stamp(path: Path) -> str:
    """Return the `YYYYmmdd_HHMMSS` stamp embedded in the generated file name.

    生成文件名形如 `private_companion_troubleshooting_20261016_015754_c18e8819.png`，
    用文件名里的时间而不是 mtime，因为 mtime 会被复制/同步改掉。
    """

    global _STAMP_RE
    if _STAMP_RE is None:
        import re

        _STAMP_RE = re.compile(r"_(\d{8})_(\d{6})_")
    match = _STAMP_RE.search(path.name)
    if match:
        return f"{match.group(1)}_{match.group(2)}"
    import datetime

    return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y%m%d_%H%M%S")


def _in_window(stamp: str, *, newer_than: str, older_than: str) -> bool:
    if newer_than and stamp < newer_than:
        return False
    if older_than and stamp > older_than:
        return False
    return True


def collect_files(args: argparse.Namespace) -> list[Path]:
    if args.files:
        return [Path(f) for f in args.files]
    files = sorted(PHOTO_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if args.newer_than or args.older_than:
        # 时间窗过滤：用来把「某次改动前 / 后」的图分成两批做缺陷率对比。
        files = [
            p
            for p in files
            if _in_window(_stamp(p), newer_than=args.newer_than, older_than=args.older_than)
        ]
    return files[: args.limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="defects")
    ap.add_argument("--limit", type=int, default=24)
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument(
        "--newer-than",
        default="",
        help="只统计文件名时间戳 >= 该值的图，格式 YYYYmmdd_HHMMSS（如 20261016_015800）",
    )
    ap.add_argument(
        "--older-than",
        default="",
        help="只统计文件名时间戳 <= 该值的图，格式 YYYYmmdd_HHMMSS",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印筛选出来的图片与时间戳，不调用 VL 模型（用来验证时间窗）",
    )
    args = ap.parse_args()

    if args.dry_run:
        for path in collect_files(args):
            print(f"{_stamp(path)}  {path.name}")
        return 0

    files = [f for f in collect_files(args) if f.exists()]
    if not files:
        raise SystemExit("没有可分析的图片")

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    tally: Counter[str] = Counter()
    rows: list[dict] = []
    for path in files:
        text = free_llm.call(
            VL_MODEL, PROMPT, [str(path)], temperature=0.2, max_tokens=2500
        )
        payload = parse_json(text)
        defects = payload.get("defects") or []
        tags: list[str] = []
        for item in defects:
            tag = str(item.get("tag") if isinstance(item, dict) else item).strip().lower()
            if tag:
                tags.append(tag)
                tally[tag] += 1
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tag": args.tag,
            "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "defects": tags,
            "detail": defects,
            # 以实际标签列表为准：VL 偶尔给出 "defects": [] 但 "clean": false，
            # 聚合统计（第 183 行）本来就用 defects，这里对齐以免逐行记录自相矛盾。
            "clean": not tags,
            "model_clean": bool(payload.get("clean")),
        }
        rows.append(row)
        with OUT.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[{len(rows)}/{len(files)}] {path.name}: {', '.join(tags) or 'clean'}", flush=True)
        time.sleep(args.sleep)

    total = len(rows)
    print(f"\n=== {args.tag}: {total} 张，缺陷出现次数（按频率）===")
    for tag, count in tally.most_common():
        print(f"{tag:18s} {count:3d}  ({count / total * 100:5.1f}% 的图)")
    clean = sum(1 for r in rows if not r["defects"])
    print(f"完全无缺陷：{clean}/{total}")
    report = EVAL_DIR / f"defects-{args.tag}.json"
    report.write_text(json.dumps({"total": total, "tally": dict(tally), "rows": rows},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print("明细：", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
