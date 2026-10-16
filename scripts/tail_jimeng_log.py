"""看即梦本地生图服务的请求日志尾巴（`logs/jimeng-server-requests.jsonl`）。

跑法：
    python scripts/tail_jimeng_log.py            # 最后 15 条
    python scripts/tail_jimeng_log.py -n 40
    python scripts/tail_jimeng_log.py --errors   # 只看 error/cooldown_block
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

REPO = pathlib.Path(__file__).resolve().parent.parent
LOG = REPO / "logs" / "jimeng-server-requests.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--lines", type=int, default=15)
    parser.add_argument("--errors", action="store_true", help="只看 error / cooldown_block / access_denied")
    args = parser.parse_args()

    if not LOG.is_file():
        print(f"找不到 {LOG}")
        return 2
    raw_lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[dict] = []
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if args.errors:
        rows = [
            row
            for row in rows
            if str(row.get("stage")) in {"error", "cooldown_block", "access_denied"}
            or "access denied" in json.dumps(row, ensure_ascii=False).lower()
        ]
    print(f"// {LOG} 共 {len(raw_lines)} 行，展示最后 {min(args.lines, len(rows))} 条")
    for row in rows[-args.lines :]:
        detail = " ".join(str(row.get("detail") or "").split())
        print(
            f"{row.get('ts')} {str(row.get('stage')):14s} {str(row.get('path') or ''):20s} "
            f"model={row.get('model')} prompt_len={row.get('prompt_len')} "
            f"req_key={row.get('req_key')} seconds_left={row.get('seconds_left')} detail={detail[:150]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
