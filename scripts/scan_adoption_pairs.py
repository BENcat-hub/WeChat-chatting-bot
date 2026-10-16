# -*- coding: utf-8 -*-
"""扫描“用户说过的话被机器人用第一人称复述”的候选（主语串味取证）。

思路：把每个用户入站消息与随后 N 秒内的机器人出站消息配对，找出两边
共有的 ≥4 字连续片段；只报告机器人侧片段落在第一人称语境（含“我”）的配对。

用法：
    python scan_adoption_pairs.py --since 00:00 --until 23:59 --window 300 --minlen 4
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

ROOT = Path(
    r"<REPO_ROOT>"
)
ANSI = re.compile(r"\x1b\[[0-9;]*m")
IN_RE = re.compile(r"\[default\([^\]]+\)\]\s*([^/\n]+?)/(\d+):\s*(.*)$")
SEND_RE = re.compile(r"已按分段计划发送 LLM 回复:\s*segments=(\d+)\s*llm_segments=(\d+)\s*first=(.*?)\s*full=(.*)$")
BRIDGE_RE = re.compile(r"文字已发送至\s*([^:]+):\s*(.*)$")


def ts_to_sec(ts: str) -> int:
    h, m, s = (int(x) for x in ts.split(":"))
    return h * 3600 + m * 60 + s


def rows(since: str, until: str):
    out: list[tuple[int, str, str, str]] = []
    cur = "?"
    for path in sorted(glob.glob(str(ROOT / "logs" / "astrbot-2026*.log"))):
        for raw in open(path, encoding="utf-8", errors="replace"):
            line = ANSI.sub("", raw.rstrip("\n"))
            m = re.match(r"\[(\d\d:\d\d:\d\d)\.\d+\]", line)
            if not m:
                continue
            ts = m.group(1)
            if not (since <= ts <= until):
                continue
            mi = IN_RE.search(line)
            if mi and "event_bus" in line:
                cur = mi.group(1).strip()
                out.append((ts_to_sec(ts), "IN", cur, mi.group(3).strip()))
                continue
            ms = SEND_RE.search(line)
            if ms:
                out.append((ts_to_sec(ts), "OUT", cur, ms.group(4).strip()))
    bl = ROOT / "akasha-bridge" / "bridge.log"
    if bl.exists():
        for raw in open(bl, encoding="utf-8", errors="replace"):
            line = ANSI.sub("", raw.rstrip("\n"))
            m = re.match(r"(\d\d:\d\d:\d\d)", line)
            mb = BRIDGE_RE.search(line)
            if m and mb and since <= m.group(1) <= until:
                out.append((ts_to_sec(m.group(1)), "SENT", mb.group(1).strip(), mb.group(2).strip()))
    out.sort(key=lambda r: (r[0], r[1]))
    return out


def common_fragments(a: str, b: str, minlen: int) -> list[str]:
    frags: set[str] = set()
    for i in range(len(a) - minlen + 1):
        for j in range(len(b) - minlen + 1):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k >= minlen:
                frag = a[i : i + k]
                if not frag.isdigit():
                    frags.add(frag.strip())
    return sorted((f for f in frags if f), key=len, reverse=True)[:3]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="00:00")
    ap.add_argument("--until", default="23:59")
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--minlen", type=int, default=4)
    ap.add_argument("--require-wo", action="store_true", default=True)
    ap.add_argument("--no-require-wo", action="store_true", default=False)
    args = ap.parse_args()

    data = rows(args.since, args.until)
    hits = 0
    for idx, (t, kind, who, text) in enumerate(data):
        if kind != "IN":
            continue
        for t2, kind2, who2, text2 in data[idx + 1 :]:
            if t2 - t > args.window:
                break
            if kind2 != "OUT":
                continue
            frags = common_fragments(text, text2, args.minlen)
            if not args.no_require_wo:
                frags = [f for f in frags if ("我" in f or "我的" in f)]
            if not frags:
                continue
            hits += 1
            print(f"--- {t//3600:02d}:{t%3600//60:02d}:{t%60:02d} IN  [{who}] {text[:110]}")
            print(f"    {t2//3600:02d}:{t2%3600//60:02d}:{t2%60:02d} OUT [{who2}] {text2[:150]}")
            print(f"    共享片段: {frags}")
    print(f"\n候选配对数: {hits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
