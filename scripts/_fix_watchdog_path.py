# -*- coding: utf-8 -*-
"""把看门狗的重启脚本指向修好的 `_restart_bridge.ps1`（一次性迁移脚本）。"""
import io
import os

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bridge_watchdog.py")
s = io.open(p, encoding="utf-8").read()
before = s

old_doc = ("重启方式：调 `scripts/_restart_bridge_detached.ps1`（WMI 拉起，父进程是 WmiPrvSE，\n"
           "不挂在当前进程树下，下次不会再被回收）。")
new_doc = ("重启方式：调 `scripts/_restart_bridge.ps1`（脚本内部用 WMI 拉起 `_run_bridge.cmd`，\n"
           "父进程是 WmiPrvSE，不挂在当前进程树下，下次不会再被回收）。\n"
           "不要再套一层 `_restart_bridge_detached.ps1`：两层 powershell/cmd 转手在这台机器上\n"
           "失败过（日志没写、旧桥没停、新桥绑定失败静默退出）。")
s = s.replace(old_doc, new_doc)
s = s.replace('RESTART_PS1 = os.path.join(ROOT, "scripts", "_restart_bridge_detached.ps1")',
              'RESTART_PS1 = os.path.join(HERE, "_restart_bridge.ps1")')

if s == before:
    print("NO-CHANGE")
else:
    io.open(p, "w", encoding="utf-8", newline="\n").write(s)
    print("PATCHED")
