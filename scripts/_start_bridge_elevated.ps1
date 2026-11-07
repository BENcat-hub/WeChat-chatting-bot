<#
.SYNOPSIS
    以管理员（High 完整性）身份启动 akasha-bridge。

.DESCRIPTION
    为什么必须提权：本机微信是 **High 完整性**进程（UAC 的 ConsentPromptBehaviorAdmin=0
    → 管理员账户启动的进程默认就是 High）。而 DSH / 普通终端拉起的 bridge 是 Medium，
    UIPI 会禁止 Medium 进程向 High 窗口注入键鼠（SetCursorPos 返回 0、ShowWindow 报
    ACCESS_DENIED），表现为 bridge 里成片的「send 超时（45s）」或
    「cursor cannot move ... start the bridge elevated」。

    实测（2026-11-05）：Medium 进程 SetCursorPos -> 0；同一段代码提权后 -> 1，光标真的动了，
    ShowWindow(微信, SW_RESTORE) 也成功。

    本脚本：检测当前是否 High；不是就用 Start-Process -Verb RunAs 自提权（本机 UAC 设置为
    「不提示直接提升」，不会弹框），然后隐藏窗口拉起 bridge 并等 8766 起来。

.PARAMETER Restart
    先按 8766 端口属主杀掉旧 bridge 再启动。
#>
[CmdletBinding()]
param(
    [switch]$Restart,
    [int]$WaitSeconds = 40
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bridgeDir = Join-Path $root 'akasha-bridge'
$py = Join-Path $bridgeDir '.venv\Scripts\python.exe'
$logs = Join-Path $root 'logs'

function Test-High {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Path $py)) { throw "找不到 $py" }
if (-not (Test-Path (Join-Path $bridgeDir 'config.json'))) { throw "缺少 akasha-bridge\config.json" }

if (-not (Test-High)) {
    Write-Host '  → 当前不是 High 完整性，自提权后重启 bridge' -ForegroundColor Yellow
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-WaitSeconds', $WaitSeconds)
    if ($Restart) { $argList += '-Restart' }
    Start-Process powershell -ArgumentList $argList -Verb RunAs -WindowStyle Hidden | Out-Null
    Write-Host '  → 已交给提权进程处理（本机 UAC 不弹框）' -ForegroundColor DarkGray
    exit 0
}

if ($Restart) {
    $owners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($o in $owners) {
        if ($o) { Stop-Process -Id $o -Force -ErrorAction SilentlyContinue; Write-Host ("  ✗ 已停止旧 bridge PID {0}" -f $o) }
    }
    Start-Sleep -Seconds 3
}

$outLog = Join-Path $logs 'bridge.out.log'
$errLog = Join-Path $logs 'bridge.err.log'
$p = Start-Process -FilePath $py -ArgumentList @('-X', 'utf8', 'main.py') `
    -WorkingDirectory $bridgeDir -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Write-Host ("  → bridge 已启动 PID={0}（High 完整性）日志={1}" -f $p.Id, $outLog) -ForegroundColor DarkGray

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host '  OK 8766 已监听（bridge 已提权，可操作微信窗口）' -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host '  ✗ 8766 未监听，看 logs\bridge.err.log 与 akasha-bridge\bridge.log' -ForegroundColor Yellow
exit 1
