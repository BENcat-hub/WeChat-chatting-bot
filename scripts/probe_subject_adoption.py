# -*- coding: utf-8 -*-
"""按时间线合并“用户入站 / 机器人出站”，用于人工排查主语串味。

数据源：
  * AstrBot 日志  -> 入站用户消息（`[default(aiocqhttp)] 名字/uid: 文本`）与
                     出站回复（`已按分段计划发送 LLM 回复: ... first=... full=...`）
  * bridge.log    -> 最终外发文本（`[OB11] 文字已发送至 名字: 文本`）

用法：
    python probe_subject_adoption.py --since 16:20 --until 17:00
"""
from __future__ import annotations

import argparse
import glob
import os
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


def hhmm(ts: str) -> str:
    return ts


def collect(since: str, until: str):
    rows: list[tuple[str, str, str]] = []  # (time, kind, text)

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
                rows.append((ts, "IN   ", f"{mi.group(1)}: {mi.group(3).strip()}"))
                continue
            ms = SEND_RE.search(line)
            if ms:
                rows.append((ts, "OUT  ", f"[seg={ms.group(1)}] {ms.group(4).strip()}"))

    bridge_log = ROOT / "akasha-bridge" / "bridge.log"
    if bridge_log.exists():
        for raw in open(bridge_log, encoding="utf-8", errors="replace"):
            line = ANSI.sub("", raw.rstrip("\n"))
            m = re.match(r"(\d\d:\d\d:\d\d)", line)
            mb = BRIDGE_RE.search(line)
            if m and mb and since <= m.group(1) <= until:
                rows.append((m.group(1), "SENT ", f"{mb.group(1).strip()}: {mb.group(2).strip()}"))

    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="00:00")
    ap.add_argument("--until", default="23:59")
    ap.add_argument("--width", type=int, default=200)
    args = ap.parse_args()

    rows = collect(args.since, args.until)
    print(f"=== {args.since} ~ {args.until}  共 {len(rows)} 行 ===")
    for ts, kind, text in rows:
        text = text.replace("\n", " ")
        if len(text) > args.width:
            text = text[: args.width] + "…"
        print(f"{ts} {kind} {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
