# -*- coding: utf-8 -*-
"""磁盘卫生：清调试产物、收敛备份、checkpoint WAL。

默认 dry-run（只打印将删什么），`--apply` 才真删。删除统一走 dsh-tools/safe-delete.py
之外的就地 unlink（这些都在工作区内、且是我们自己的调试产物）。

规则（可用 --keep 覆盖）：
  logs/wmcp/          每次截图/OCR 的中间产物   keep 40 个文件 + 7 天
  logs/jimeng-out/    出图缓存                  keep 20 个文件 + 7 天
  logs/msgprobe/      探针截图                  keep 40 个文件 + 3 天
  logs/voice/         语音中间 wav              keep 20 个文件 + 7 天
  logs/*.log.1 等     轮转残留                  保留最新 1 份
  plugin_data/astrbot_plugin_private_companion/companions.json.bak-*   保留最新 3 份
  plugin_data/astrbot_plugin_private_companion/*.bak-*（其它）          保留最新 2 份
  astrbot/data/data_v4.db：PRAGMA wal_checkpoint(TRUNCATE)（尽力而为）
"""
from __future__ import annotations

import argparse
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS = os.path.join(ROOT, "logs")
PDATA = os.path.join(ROOT, "astrbot", "data", "plugin_data", "astrbot_plugin_private_companion")
DB = os.path.join(ROOT, "astrbot", "data", "data_v4.db")

RULES = [
    # (相对路径, 至少保留几个最新文件, 超过多少天可删, 是否只按数量裁剪)
    # jimeng-out 是代理层的出图缓存（4K PNG 单张 10~19MB，可重生成），
    # 用户真正要看的成图在 plugin_data/.../generated_photos，因此这里按数量硬裁剪。
    ("logs/wmcp", 40, 7, False),
    ("logs/jimeng-out", 6, 7, True),
    ("logs/msgprobe", 40, 3, True),
    ("logs/voice", 10, 7, True),
    # 插件自己的成图相册：留最新 60 张，更老的只在超过 30 天时清理
    ("astrbot/data/plugin_data/astrbot_plugin_private_companion/generated_photos", 60, 30, False),
]

BAK_RULES = [
    (PDATA, "companions.json.bak-", 3),
    (PDATA, ".bak-", 2),
]


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def prune_dir(rel: str, keep: int, days: float, apply: bool, count_only: bool = False) -> tuple[int, int]:
    path = os.path.join(ROOT, rel)
    if not os.path.isdir(path):
        return 0, 0
    files: list[tuple[float, int, str]] = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        try:
            st = os.stat(full)
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, full))
    files.sort(key=lambda x: x[0], reverse=True)
    cutoff = time.time() - days * 86400
    deleted = freed = 0
    for idx, (mtime, size, full) in enumerate(files):
        if idx < keep:
            continue
        stale = mtime < cutoff
        if not (stale or count_only):
            continue
        deleted += 1
        freed += size
        if apply:
            try:
                os.unlink(full)
            except OSError as exc:
                print(f"  ! {os.path.basename(full)}: {exc}")
    print(f"{rel}: {len(files)} files -> delete {deleted} ({human(freed)})")
    return deleted, freed


def prune_baks(folder: str, marker: str, keep: int, apply: bool) -> tuple[int, int]:
    if not os.path.isdir(folder):
        return 0, 0
    names = [n for n in os.listdir(folder) if marker in n and os.path.isfile(os.path.join(folder, n))]
    items = []
    for n in names:
        full = os.path.join(folder, n)
        try:
            st = os.stat(full)
        except OSError:
            continue
        items.append((st.st_mtime, st.st_size, full))
    items.sort(key=lambda x: x[0], reverse=True)
    deleted = freed = 0
    for _mtime, size, full in items[keep:]:
        deleted += 1
        freed += size
        if apply:
            try:
                os.unlink(full)
            except OSError as exc:
                print(f"  ! {os.path.basename(full)}: {exc}")
    label = os.path.basename(folder) + "/" + marker
    print(f"{label}: {len(items)} files -> delete {deleted} ({human(freed)})")
    return deleted, freed


def checkpoint(apply: bool) -> None:
    if not os.path.exists(DB):
        return
    wal = DB + "-wal"
    before = os.path.getsize(wal) if os.path.exists(wal) else 0
    print(f"data_v4.db-wal: {human(before)}")
    if not apply:
        return
    try:
        import sqlite3

        con = sqlite3.connect(DB, timeout=10)
        try:
            row = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        finally:
            con.close()
        after = os.path.getsize(wal) if os.path.exists(wal) else 0
        print(f"  checkpoint -> {row} ; wal {human(before)} -> {human(after)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  checkpoint skipped: {exc}")


def logs_size() -> int:
    total = 0
    for base, _dirs, names in os.walk(LOGS):
        for name in names:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的删；默认只预览")
    ap.add_argument("--wal-only", action="store_true", help="只做 WAL checkpoint")
    ap.add_argument(
        "--auto",
        action="store_true",
        help="供 start-all 调用：logs 超过阈值(默认 120MB)才裁剪，永远顺手 checkpoint；输出一行摘要",
    )
    ap.add_argument("--threshold-mb", type=float, default=float(os.environ.get("PRUNE_LOGS_MB", "120")))
    args = ap.parse_args()

    if args.auto:
        size_mb = logs_size() / 1048576.0
        if size_mb > args.threshold_mb:
            print(f"[prune] logs {size_mb:.0f}MB > {args.threshold_mb:.0f}MB，执行裁剪")
            args.apply = True
        else:
            print(f"[prune] logs {size_mb:.0f}MB，未超阈值，跳过")
            args.apply = False
            args.wal_only = False
            checkpoint(True)
            return 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"== prune_artifacts [{mode}] root={ROOT}")
    total = 0
    if not args.wal_only:
        for rel, keep, days, count_only in RULES:
            total += prune_dir(rel, keep, days, args.apply, count_only)[1]
        for folder, marker, keep in BAK_RULES:
            total += prune_baks(folder, marker, keep, args.apply)[1]
        print(f"== 合计可回收/已回收: {human(total)}")
    checkpoint(args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
