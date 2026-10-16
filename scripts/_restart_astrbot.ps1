# 重启 AstrBot（停掉旧进程树 -> 用 scripts\astrbot-start.ps1 重新拉起，脚本内已带 ASTRBOT_RELOAD=1）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pids = @()
$pids += (Get-NetTCPConnection -LocalPort 11229 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
$pids += (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrbot-start\.ps1|astrbot\.exe run' } | Select-Object -ExpandProperty ProcessId)
foreach ($p in ($pids | Sort-Object -Unique)) {
    try { Stop-Process -Id $p -Force -ErrorAction Stop; "stopped pid $p" } catch { "skip pid $p" }
}
Start-Sleep -Seconds 4
"11229 free: $(-not [bool](Get-NetTCPConnection -LocalPort 11229 -State Listen -ErrorAction SilentlyContinue))"

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = "$root\logs\astrbot-$stamp.log"
$proc = Start-Process pwsh -ArgumentList '-NoProfile', '-File', "$root\scripts\astrbot-start.ps1", '-LogFile', $log -WindowStyle Hidden -PassThru
"astrbot runner pid $($proc.Id)"
"log $log"
Start-Sleep -Seconds 22
Get-NetTCPConnection -LocalPort 11229, 6185, 8766, 5031 -State Listen -ErrorAction SilentlyContinue |
    Sort-Object LocalPort | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize | Out-String
$log
