# -*- coding: utf-8 -*-
"""探测某个 SQLite 库当前能不能拿到写锁（只读探测，不改数据）。

背景（2026-11-02）：AstrBot 的 memory_companion 进程卡住一个未提交的写事务后，
插件自己的写入全部返回 `sqlite3.OperationalError: unable to open database file`，
表现为「记忆不再更新」，日志里只有 WARNING，很容易被忽略。
判据：BEGIN IMMEDIATE 能立刻拿到写锁 = 健康；拿不到 = 有进程占着写锁，重启 AstrBot 即可释放。

用法：python _probe_dbwrite.py <db 文件路径> [超时秒数]
输出一行：OK <path>  或  LOCKED <path>: <原因>  或  MISSING <path>
退出码：0=OK，1=LOCKED/异常，2=文件不存在
"""
import os
import sqlite3
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: _probe_dbwrite.py <db-path> [timeout]")
        return 2
    path = sys.argv[1]
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    if not os.path.isfile(path):
        print("MISSING %s" % path)
        return 2
    try:
        conn = sqlite3.connect(path, timeout=timeout)
        try:
            conn.execute("PRAGMA busy_timeout=%d" % int(timeout * 1000))
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 探测脚本，任何异常都算不健康
        print("LOCKED %s: %s: %s" % (path, type(exc).__name__, exc))
        return 1
    print("OK %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
