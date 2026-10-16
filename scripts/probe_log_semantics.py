#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日志语义取证：对话流水（谁说了什么）+ 表情包重复选择统计。

用法：
  python probe_log_semantics.py <日志文件> [--session 1000000003] [关键词...]

输出：
  1) 关键词命中行的上下文；
  2) 对话流水（收/发/表情包选择，按时间交错）；
  3) 表情包语义检索词与选中统计 + 连续重复。
"""

from __future__ import annotations

import collections
import os
import re
import sys

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INBOUND_RE = re.compile(r"\[default\(aiocqhttp\)\]\s*(?P<who>[^:]+):\s*(?P<text>.*)$")
OUTBOUND_RE = re.compile(r"\[respond\.stage:\d+\]:\s*Prepare to send\s*-\s*(?P<who>[^:]+):\s*(?P<text>.*)$")
MEME_PICK_RE = re.compile(r"已选择语义表情：\[(?P<ids>.*?)\]")
MEME_QUERY_RE = re.compile(r"情感模型语义检索词（(?P<n>\d+)字）:\s*(?P<q>.*)$")
TS_RE = re.compile(r"^\[(?P<ts>\d{2}:\d{2}:\d{2}\.\d{3})\]")


def read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return [ANSI_RE.sub("", line) for line in fh.read().splitlines()]


def ts_of(line: str) -> str:
    m = TS_RE.match(line)
    return m.group("ts")[:8] if m else "        "


def show_keyword_context(lines: list[str], keyword: str, before: int = 1, after: int = 1) -> None:
    hits = [i for i, line in enumerate(lines) if keyword in line]
    print(f"\n=== 含「{keyword}」的行：{len(hits)} 处 ===")
    shown: set[int] = set()
    for idx in hits:
        for j in range(max(0, idx - before), min(len(lines), idx + after + 1)):
            if j in shown:
                continue
            shown.add(j)
            mark = ">>" if j == idx else "  "
            print(f"  {mark} {j + 1:5d}: {lines[j].strip()[:230]}")
        print("  ---")


def transcript(lines: list[str], session: str | None) -> None:
    print("\n=== 对话流水（收 / 发 / 表情包）===")
    for line in lines:
        if session and session not in line:
            continue
        m = INBOUND_RE.search(line)
        if m:
            print(f"  {ts_of(line)}  收  {m.group('who').strip():<12} {m.group('text').strip()[:150]}")
            continue
        m = OUTBOUND_RE.search(line)
        if m:
            print(f"  {ts_of(line)}  发  {m.group('text').strip()[:150]}")
            continue
        m = MEME_PICK_RE.search(line)
        if m:
            ids = ",".join(x.strip() for x in m.group("ids").split(",") if x.strip())
            print(f"  {ts_of(line)}  🖼  选中表情 {ids}")
            continue
        m = MEME_QUERY_RE.search(line)
        if m:
            print(f"  {ts_of(line)}  🔍  语义检索词({m.group('n')}字) {m.group('q').strip()}")


def meme_stats(lines: list[str]) -> None:
    queries: list[str] = []
    picks: list[list[str]] = []
    for line in lines:
        m = MEME_QUERY_RE.search(line)
        if m:
            queries.append(m.group("q").strip())
        m = MEME_PICK_RE.search(line)
        if m:
            picks.append([x.strip() for x in m.group("ids").split(",") if x.strip()])

    print(f"\n=== 表情包语义检索：{len(queries)} 次查询，{len(picks)} 次选中 ===")
    counter: collections.Counter[str] = collections.Counter()
    for group in picks:
        for meme in group:
            counter[meme] += 1
    for meme, count in counter.most_common(10):
        print(f"  {meme}  选中 {count} 次")

    repeats = 0
    for picks1, picks2 in zip(picks, picks[1:]):
        if picks1 and picks1 == picks2:
            repeats += 1
    print(f"  连续两轮选中同一表情：{repeats} 次")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1]
    if not os.path.isfile(path):
        print(f"文件不存在: {path}")
        return 1
    args = sys.argv[2:]
    session = None
    keywords: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--session" and i + 1 < len(args):
            session = args[i + 1]
            i += 2
            continue
        keywords.append(args[i])
        i += 1

    lines = read_lines(path)
    print(f"读取 {path}：{len(lines)} 行")
    for keyword in keywords:
        show_keyword_context(lines, keyword)
    transcript(lines, session)
    meme_stats(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
