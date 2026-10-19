# WeFlow 离线防回拨 anchor 体检 / 修复
#
# 背景：WeFlow 启动时原生 DLL 的 wcdb_runtime_policy_version() 必须返回魔数 1464206081。
# 若返回 -105（= 离线防回拨状态损坏或无法安全保存），app.asar 里会打
#   [bootstrap] native runtime policy mismatch value=-105
# 然后 5031 HTTP API 不启动（bridge 报「WeFlow 自检返回 HTTP 500 / 无法连接 WeFlow」）。
# 实测原因不是「时间到期」，而是非正常关机把 %LOCALAPPDATA%\WeFlow\Runtime\anchor-v7-*.bin 写成了全 0，
# 与完好的 State\native-anchor-v7-*.bin 配不上，DLL 就拒绝初始化。
#
# 用法：
#   pwsh -File weflow-anchor-check.ps1            # 只体检，不改动
#   pwsh -File weflow-anchor-check.ps1 -Fix       # 停 WeFlow -> 备份 -> 删掉损坏的 anchor -> 重启 WeFlow
#
# 默认只读。 -Fix 会停/起 WeFlow，并只移动「确认为全 0」的文件（先备份）。

[CmdletBinding()]
param(
    [switch]$Fix,
    [int]$WaitSeconds = 30
)

$ErrorActionPreference = "Continue"

$StateRoot = Join-Path $env:LOCALAPPDATA "WeFlow"
$LogFile = Join-Path $env:APPDATA "weflow\logs\wcdb.log"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackupRoot = Join-Path $RepoRoot "logs"

function Get-StateFiles {
    $names = @("anchor-v7-*.bin", "native-anchor-v7-*.bin", "device-root-v1.bin")
    $found = @()
    foreach ($n in $names) {
        $found += Get-ChildItem -Path $StateRoot -Recurse -File -Force -Filter $n -ErrorAction SilentlyContinue
    }
    return $found
}

function Test-AllZero {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) { return $true }
    $nonzero = 0
    foreach ($b in $bytes) { if ($b -ne 0) { $nonzero++ } }
    return ($nonzero -eq 0)
}

Write-Host "=== WeFlow anchor 体检 ===" -ForegroundColor Cyan
Write-Host "状态目录: $StateRoot"

$files = Get-StateFiles
if (-not $files) {
    Write-Host "未找到任何状态文件（首次运行 WeFlow 时会自动生成）" -ForegroundColor Yellow
}

$corrupt = @()
$healthy = @()
foreach ($f in $files) {
    $zero = Test-AllZero -Path $f.FullName
    $tag = if ($zero) { "损坏(全 0)" } else { "正常" }
    $color = if ($zero) { "Red" } else { "Green" }
    Write-Host ("  [{0}] {1}  {2} 字节  {3}" -f $tag, $f.FullName, $f.Length, $f.LastWriteTime) -ForegroundColor $color
    if ($zero) { $corrupt += $f } else { $healthy += $f }
}

$running = @(Get-Process WeFlow -ErrorAction SilentlyContinue)
Write-Host ("WeFlow 进程数: {0}" -f $running.Count)
$policyOk = $false
if (Test-Path $LogFile) {
    $tail = Get-Content $LogFile -Tail 200
    $lastOk = ($tail | Select-String -Pattern "native protection ok" | Select-Object -Last 1)
    $lastBad = ($tail | Select-String -Pattern "native runtime policy mismatch value=(-?\d+)" | Select-Object -Last 1)
    if ($lastBad) { Write-Host ("最近一次 policy 失败: " + $lastBad.Line) -ForegroundColor Yellow }
    if ($lastOk -and (($null -eq $lastBad) -or ($lastOk.LineNumber -gt $lastBad.LineNumber))) {
        Write-Host "最近一次 policy 检查: native protection ok" -ForegroundColor Green
        $policyOk = $true
    }
}
$api = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5031 -WarningAction SilentlyContinue).TcpTestSucceeded
Write-Host ("5031 API: " + $(if ($api) { "在听" } else { "未监听" })) -ForegroundColor $(if ($api) { "Green" } else { "Yellow" })

if ($corrupt.Count -eq 0 -and $policyOk -and $api) {
    Write-Host "`n结论: WeFlow 保护状态正常，无需修复。" -ForegroundColor Green
    exit 0
}

if ($corrupt.Count -eq 0 -and -not $api) {
    Write-Host "`n结论: anchor 文件本身完好，但 5031 没起来；先确认 WeFlow 是否在运行/是否登录。" -ForegroundColor Yellow
    exit 1
}

Write-Host ("`n发现 {0} 个损坏(全 0)的 anchor 文件。" -f $corrupt.Count) -ForegroundColor Red
if (-not $Fix) {
    Write-Host "加 -Fix 参数执行修复：停 WeFlow -> 备份 -> 移走损坏文件 -> 重启（DLL 会重建 anchor）。" -ForegroundColor Yellow
    exit 1
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = Join-Path $BackupRoot "weflow-state-backup-$stamp"
New-Item -ItemType Directory -Force -Path $backup | Out-Null
Write-Host "`n=== 1) 备份到 $backup ==="
foreach ($f in $files) {
    $dst = Join-Path $backup ($f.Name + "." + (Split-Path -Leaf (Split-Path -Parent $f.FullName)) + ".bak")
    Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
    Write-Host "  备份 $($f.Name)"
}

Write-Host "=== 2) 停掉 WeFlow ==="
if ($running.Count -gt 0) { Stop-Process -Name WeFlow -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 3 }
if (Get-Process WeFlow -ErrorAction SilentlyContinue) {
    Write-Host "  WeFlow 仍在运行，放弃修复（避免半途状态）" -ForegroundColor Red
    exit 1
}
Write-Host "  已停止"

Write-Host "=== 3) 移走损坏文件（只动全 0 的） ==="
foreach ($f in $corrupt) {
    if (Test-AllZero -Path $f.FullName) {
        $dst = Join-Path $backup ("removed-" + $f.Name)
        Move-Item -LiteralPath $f.FullName -Destination $dst -Force
        Write-Host "  已移走 $($f.FullName)"
    } else {
        Write-Host "  $($f.Name) 复查不再是全 0，跳过" -ForegroundColor Yellow
    }
}

Write-Host "=== 4) 重启 WeFlow（经 explorer，走交互式会话） ==="
$before = if (Test-Path $LogFile) { (Get-Item $LogFile).Length } else { 0 }
Start-Process explorer.exe -ArgumentList "`"$env:ProgramFiles\WeFlow\WeFlow.exe`""
for ($i = 0; $i -lt $WaitSeconds; $i += 2) {
    Start-Sleep -Seconds 2
    if ((Test-NetConnection -ComputerName 127.0.0.1 -Port 5031 -WarningAction SilentlyContinue).TcpTestSucceeded) { break }
}

Write-Host "=== 5) 结果 ==="
Write-Host ("WeFlow 进程数: " + @(Get-Process WeFlow -ErrorAction SilentlyContinue).Count)
$api = (Test-NetConnection -ComputerName 127.0.0.1 -Port 5031 -WarningAction SilentlyContinue).TcpTestSucceeded
Write-Host ("5031 API: " + $(if ($api) { "在听" } else { "未监听" })) -ForegroundColor $(if ($api) { "Green" } else { "Red" })
if (Test-Path $LogFile) {
    $fs = [System.IO.File]::Open($LogFile, 'Open', 'Read', 'ReadWrite')
    $fs.Seek($before, 'Begin') | Out-Null
    $sr = New-Object System.IO.StreamReader($fs)
    $new = $sr.ReadToEnd(); $sr.Close(); $fs.Close()
    ($new -split "`n" | Select-String -Pattern "policy mismatch|native protection ok|wcdb_authenticate" | ForEach-Object { $_.Line }) | Select-Object -Last 5
}
Write-Host "重建后的 anchor:"
Get-StateFiles | ForEach-Object { Write-Host ("  {0}  {1} 字节" -f $_.FullName, $_.Length) }

if ($api) { Write-Host "`n修复完成。" -ForegroundColor Green; exit 0 }
Write-Host "`n仍失败：考虑连 State\native-anchor-v7-*.bin 一起移走（让 DLL 全新初始化），或重装 WeFlow。" -ForegroundColor Red
exit 1
