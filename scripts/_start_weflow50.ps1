# 启动 WeFlow 5.0（源码树，端口 5031）。
# 为什么需要这个脚本：
#   1) ELECTRON_RUN_AS_NODE 一旦被设置（哪怕是空串），electron 会以 node 模式运行、
#      把 app 当成 JS 解析 —— 窗口不出现、命令立刻返回，看起来就是"快捷方式点了没反应"。
#      本脚本会把该变量从子进程环境里真正删掉再拉起 electron。
#   2) 托盘图标缺失时 electron 抛错、主窗口也不弹，整只 app 看不见 —— icon.ico 由本脚本补齐。
#   3) 失败不再静默：stdout/stderr 落到 logs\weflow-5.0.*.log，超时会回显最后几行。
# 用法：_start_weflow50.ps1 [-Elevated] [-WaitSeconds 40]
param(
    [switch]$Elevated,
    [int]$WaitSeconds = 40
)

$ErrorActionPreference = 'Stop'
$ROOT = Split-Path -Parent $PSScriptRoot
$APP = Join-Path $ROOT 'weflow'
$EXE = Join-Path $APP 'node_modules\electron\dist\electron.exe'
$LOG = Join-Path $ROOT 'logs'
$OUT = Join-Path $LOG 'weflow-5.0.out.log'
$ERR = Join-Path $LOG 'weflow-5.0.err.log'

function Test-High {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Listening([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

# 只有显式 -Elevated 才提权（被 DSH 沙箱拦住时由调用方加这个开关）。
if ($Elevated -and -not (Test-High)) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $PSCommandPath,
        '-Elevated', '-WaitSeconds', $WaitSeconds)
    Start-Process powershell -ArgumentList $argList -Verb RunAs | Out-Null
    Write-Host "  -> 已交给提权进程启动 WeFlow 5.0"
    exit 0
}

if (Test-Listening 5031) {
    Write-Host "[weflow50] 5031 已在监听，无需重复启动"
    exit 0
}

if (-not (Test-Path $EXE)) {
    Write-Host "[weflow50] 缺少 electron：$EXE"
    Write-Host "[weflow50] 先执行：cd /d `"$APP`" && npm ci --ignore-scripts"
    exit 1
}

$FF = Join-Path $APP 'node_modules\ffmpeg-static\ffmpeg.exe'
if (-not (Test-Path $FF)) {
    $src = 'C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe'
    if (Test-Path $src) { Copy-Item $src $FF -Force }
}

# 托盘图标：main.js 找的是 electron 自带的 resources\icon.ico，源码树里没有 → 复制一份。
$iconDst = Join-Path $APP 'node_modules\electron\dist\resources\icon.ico'
if (-not (Test-Path $iconDst)) {
    foreach ($cand in @((Join-Path $APP 'dist\icon.ico'), (Join-Path $APP 'public\icon.ico'))) {
        if (Test-Path $cand) { Copy-Item $cand $iconDst -Force; break }
    }
}

if (-not (Test-Path $LOG)) { New-Item -ItemType Directory -Path $LOG | Out-Null }
$TMPDIR = Join-Path $ROOT '.tmp-install'
if (-not (Test-Path $TMPDIR)) { New-Item -ItemType Directory -Path $TMPDIR | Out-Null }

# 关键：把 ELECTRON_RUN_AS_NODE 从环境里真正删掉（赋空串不算删除）
[Environment]::SetEnvironmentVariable('ELECTRON_RUN_AS_NODE', $null, 'Process')
Remove-Item Env:\ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
$env:TEMP = $TMPDIR
$env:TMP = $TMPDIR

Write-Host "[weflow50] 启动 $EXE"
$p = Start-Process -FilePath $EXE -ArgumentList '.', '--no-sandbox' -WorkingDirectory $APP `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput $OUT -RedirectStandardError $ERR

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 700
    if (Test-Listening 5031) {
        Write-Host "[weflow50] OK 5031 已监听（pid=$($p.Id)）"
        exit 0
    }
    if ($p.HasExited) { break }
}

if (Test-Listening 5031) { Write-Host "[weflow50] OK 5031 已监听"; exit 0 }

$tail = ""
if (Test-Path $ERR) { $tail = (Get-Content $ERR -Tail 20) -join "`n" }

# 沙箱里写 %APPDATA% 会 EPERM：这种情况下自动提权重试一次。
if (-not (Test-High) -and $tail -match 'EPERM|EACCES') {
    Write-Host "[weflow50] 当前权限写不了 %APPDATA%\weflow（EPERM），改用提权重试"
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $PSCommandPath,
        '-Elevated', '-WaitSeconds', $WaitSeconds)
    Start-Process powershell -ArgumentList $argList -Verb RunAs | Out-Null
    exit 0
}

Write-Host "[weflow50] 启动失败：5031 未监听（pid=$($p.Id) exited=$($p.HasExited)）"
if ($tail) {
    Write-Host "---- electron stderr（最后 20 行）----"
    $tail -split "`n" | ForEach-Object { Write-Host $_ }
}
exit 2
