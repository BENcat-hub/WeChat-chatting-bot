# 重启 AstrBot（停掉旧进程树 → 用 WMI 以独立进程树重新拉起 astrbot-start.ps1）。
# 2026-11-01 修：本机 Get-NetTCPConnection 经常不可用（NetTCPIP 模块没加载），按端口找 pid 的
# 老写法会静默失效；停进程改成 taskkill /T 按 astrbot.exe 进程树来。逻辑只保留在
# _restart_astrbot_only.ps1 一处，这里只做委托（保留文件名，兼容旧文档里的调用）。
$ErrorActionPreference = 'Stop'
$inner = Join-Path $PSScriptRoot '_restart_astrbot_only.ps1'
if (-not (Test-Path $inner)) { throw "缺少 $inner" }
& $inner
