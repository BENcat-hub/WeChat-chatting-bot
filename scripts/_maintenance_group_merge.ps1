# 维护窗口：停 AstrBot -> 应用群档案合并 -> 重新拉起 AstrBot（加载新配置/代码）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1) 停 AstrBot（端口持有者 + 命令行匹配的 runner/子进程）
$pids = @()
$pids += (Get-NetTCPConnection -LocalPort 11229 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
$pids += (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'astrbot-start\.ps1|astrbot\.exe run' } | Select-Object -ExpandProperty ProcessId)
foreach ($p in ($pids | Sort-Object -Unique)) {
    try { Stop-Process -Id $p -Force -ErrorAction Stop; "stopped pid $p" } catch { "skip pid $p ($($_.Exception.Message))" }
}
Start-Sleep -Seconds 4
$stillUp = Get-NetTCPConnection -LocalPort 11229 -State Listen -ErrorAction SilentlyContinue
"11229 still listening: $([bool]$stillUp)"

# 2) 应用群档案合并（脚本自带备份 + 报告）
$env:PYTHONIOENCODING = 'utf-8'
& "$root\akasha-bridge\.venv\Scripts\python.exe" -X utf8 "$root\scripts\merge_companion_groups.py" --apply 2>&1 |
    Select-Object -Last 10

# 3) 重新拉起 AstrBot（脚本内已带 ASTRBOT_RELOAD=1）
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = "$root\logs\astrbot-$stamp.log"
$proc = Start-Process pwsh -ArgumentList '-NoProfile', '-File', "$root\scripts\astrbot-start.ps1", '-LogFile', $log -WindowStyle Hidden -PassThru
"astrbot runner pid $($proc.Id) log $log"
Start-Sleep -Seconds 22
Get-NetTCPConnection -LocalPort 11229, 6185, 8766, 5031 -State Listen -ErrorAction SilentlyContinue |
    Sort-Object LocalPort | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize | Out-String
