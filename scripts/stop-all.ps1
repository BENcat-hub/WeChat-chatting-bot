# 停止链路：优先按 logs\pids.json 记录的 PID 关闭，再按端口兜底清理
# 用法：  pwsh -File scripts\stop-all.ps1 [-IncludeWeFlow] [-IncludeWeChat]
# 注意：  默认不动微信、不动 WeFlow 窗口（避免误杀你正在用的聊天窗口）

param(
    [switch]$IncludeWeFlow,
    [switch]$IncludeWeChat
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$pidsFile = Join-Path $root "logs\pids.json"

function Stop-Pid([int]$pid_, [string]$name) {
    $p = Get-Process -Id $pid_ -ErrorAction SilentlyContinue
    if ($p) { Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue; Write-Host ("  ✗ 已停止 {0} (PID {1})" -f $name, $pid_) }
    else { Write-Host ("  - {0} (PID {1}) 已不在运行" -f $name, $pid_) }
}
function Stop-ByPort([int]$port, [string]$name) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    $owners = @($conns | Select-Object -ExpandProperty OwningProcess -Unique)
    if (-not $owners) { Write-Host ("  - {0} 端口 {1} 无监听" -f $name, $port); return }
    foreach ($o in $owners) {
        $p = Get-Process -Id $o -ErrorAction SilentlyContinue
        if ($p) { Stop-Process -Id $o -Force -ErrorAction SilentlyContinue; Write-Host ("  ✗ 已停止 {0}（端口 {1}, {2}, PID {3}）" -f $name, $port, $p.ProcessName, $o) }
    }
}

Write-Host "`n=== 停止 AI 链路 ===" -ForegroundColor Cyan
if (Test-Path $pidsFile) {
    try {
        $pids = Get-Content $pidsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $pids.PSObject.Properties) { Stop-Pid ([int]$p.Value.pid) $p.Name }
    } catch { Write-Host "  pids.json 解析失败，改用端口兜底" -ForegroundColor Yellow }
}

Write-Host "`n=== 端口兜底清理 ===" -ForegroundColor Cyan
Stop-ByPort 8766 "bridge 面板"
Stop-ByPort 9877 "WeeMessenger"
Stop-ByPort 11229 "AstrBot 反向 WS"
Stop-ByPort 3333 "AI点歌后端"
if ($IncludeWeFlow) { Stop-ByPort 5031 "WeFlow API" } else { Write-Host "  - WeFlow(5031) 保留（需要时加 -IncludeWeFlow）" }
if ($IncludeWeChat) {
    Get-Process -Name "Weixin" -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.Id -Force; Write-Host ("  ✗ 已停止微信 4.x PID {0}" -f $_.Id) }
} else { Write-Host "  - 微信进程保留（需要时加 -IncludeWeChat）" }

Remove-Item $pidsFile -ErrorAction SilentlyContinue
Write-Host "`n完成。重新启动：pwsh -File scripts\start-all.ps1`n" -ForegroundColor Green
