# 启动 AI点歌（matsuko_cover）本地后端：Gradio 中间层 + so-vits-svc + Demucs
# 用法：pwsh -File scripts\start-cover-backend.ps1 [-Port 3333]
param(
    [int]$Port = 3333,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $repo 'rvc-backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$logDir = Join-Path $repo 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }
$log = Join-Path $logDir 'cover-backend.log'

if (-not (Test-Path $python)) { throw "找不到后端 venv: $python" }

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    # 2026-10-22：只判「端口在听」不够。点歌后端曾经在 23:40 启动后悄悄卡死，
    # 端口仍被占着但 Gradio 不再响应，于是 start-all 打印「已在监听」跳过，
    # 结果 /svc 报 `[WinError 10061] 由于目标计算机积极拒绝`。
    # 所以这里做一次真实 HTTP 探活：健康就跳过，不健康就把占位进程清掉重起。
    $healthy = $false
    try {
        $probe = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -TimeoutSec 4 -UseBasicParsing
        if ($probe.StatusCode -ge 200 -and $probe.StatusCode -lt 500) { $healthy = $true }
    } catch { $healthy = $false }
    if ($healthy) {
        Write-Output "端口 $Port 已被 pid $($existing.OwningProcess) 占用且探活正常，跳过启动"
        exit 0
    }
    Write-Output "端口 $Port 被 pid $($existing.OwningProcess) 占用但探活失败（卡死？），先清理再重启"
    foreach ($owner in ($existing | Select-Object -ExpandProperty OwningProcess -Unique)) {
        try { Stop-Process -Id $owner -Force -ErrorAction Stop; Write-Output "  已停止 pid $owner" }
        catch { Write-Output "  停止 pid $owner 失败: $($_.Exception.Message)" }
    }
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { break }
    }
}

$env:BACKEND_MODELS = Join-Path $backend 'models'
$args = @(
    (Join-Path $backend 'server.py'),
    '--port', $Port,
    '--models', $env:BACKEND_MODELS
)

if ($Foreground) {
    & $python -X utf8 @args
    exit $LASTEXITCODE
}

# 重定向时 python 默认块缓冲，看不到实时进度
$env:PYTHONUNBUFFERED = '1'
$proc = Start-Process -FilePath $python `
    -ArgumentList (@('-X', 'utf8') + $args) `
    -WorkingDirectory $backend `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError ($log -replace '\.log$', '.err.log') `
    -PassThru
Write-Output "cover backend 已启动 pid=$($proc.Id) 端口=$Port 日志=$log"
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($listen) { Write-Output "端口 $Port 已就绪"; exit 0 }
}
Write-Output "60 秒内未监听 $Port，请查看 $log"
exit 1
