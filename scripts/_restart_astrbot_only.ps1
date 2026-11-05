<#
.SYNOPSIS
    重启 AstrBot（唯一实现）。

.DESCRIPTION
    2026-11-04 重写：之前这里是一份「按 6185 端口找 pid → taskkill → WMI 拉起
    astrbot-start.ps1」的实现，11-03 误删后整个文件不见了，导致 `_restart_astrbot.ps1`
    一调用就 throw。现在把实现收敛到 `_start_astrbot_hidden.ps1`（pythonw 无控制台启动，
    先按 11229/6185 端口属主杀旧进程，再轮询端口），本文件只做兼容性转发。

.NOTES
    无控制台：astrbot.exe 是 console 脚本，用它会新建一个黑窗口（conhost +1）；
    pythonw + _astrbot_entry.py 实测 conhost 不变（+0）。
#>
[CmdletBinding()]
param(
    [int]$WaitSeconds = 75
)
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot '_start_astrbot_hidden.ps1'
if (-not (Test-Path $launcher)) { throw "缺少 $launcher" }
& $launcher -Restart -WaitSeconds $WaitSeconds
exit $LASTEXITCODE
