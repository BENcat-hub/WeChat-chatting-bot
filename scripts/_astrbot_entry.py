# -*- coding: utf-8 -*-
"""AstrBot 的「无控制台」入口（配合 pythonw.exe 使用）。

`astrbot.exe` 是 console 脚本，由脚本拉起时 Windows 一定会给它建一个控制台窗口
（就是"跑 AstrBot 时冒出来的黑 cmd 窗口"）。改用 GUI 子系统的 pythonw 加载本文件，
进程没有控制台，就不会有窗口：

    pythonw.exe -X utf8 scripts\\_astrbot_entry.py run

等价于 `astrbot run`（entry_points: astrbot = astrbot.cli.__main__:cli）。
参数原样透传：不带参数时默认补上 `run`。
"""
from __future__ import annotations

import sys


def main() -> None:
    argv = list(sys.argv[1:])
    if not argv:
        argv = ["run"]
    sys.argv = ["astrbot", *argv]
    from astrbot.cli.__main__ import cli

    cli()


if __name__ == "__main__":
    main()
