# 重启 akasha-bridge（停掉 8766 监听进程 -> 重新拉起 main.py，日志 logs\bridge-<stamp>.log）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pids = @()
$pids += (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess)
$pids += (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'akasha-bridge.*main\.py' } | Select-Object -ExpandProperty ProcessId)
foreach ($p in ($pids | Sort-Object -Unique)) {
    try { Stop-Process -Id $p -Force -ErrorAction Stop; "stopped pid $p" } catch { "skip pid $p" }
}
Start-Sleep -Seconds 3
"8766 free: $(-not [bool](Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue))"

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = "$root\logs\bridge-$stamp.log"
$env:PYTHONIOENCODING = 'utf-8'
$bridgeDir = Join-Path $root 'akasha-bridge'
$bridgePy = Join-Path $bridgeDir '.venv\Scripts\python.exe'
$proc = Start-Process -FilePath $bridgePy -ArgumentList '-X', 'utf8', 'main.py' -WorkingDirectory $bridgeDir `
    -RedirectStandardOutput $log -RedirectStandardError "$root\logs\bridge-$stamp.err.log" -WindowStyle Hidden -PassThru
"bridge pid $($proc.Id)"

# 等端口 + 等 AstrBot 连接
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 750
    if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { $ok = $true; break }
}
"8766 listening: $ok"
if ($ok) { Start-Sleep -Seconds 3 }
Get-Content $log -Tail 12 -ErrorAction SilentlyContinue
"log $log"
