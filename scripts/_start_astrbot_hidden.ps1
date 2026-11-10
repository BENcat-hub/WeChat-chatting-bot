<#
  以「无控制台」方式启动 AstrBot（2026-11-04）。

  问题：`astrbot.exe` 是 console 脚本（entry_points: astrbot = astrbot.cli.__main__:cli），
  由 start-all.ps1 / 看门狗拉起时哪怕加了 -WindowStyle Hidden，Windows 仍会为它建一个
  控制台（conhost），表现为「跑 AstrBot 时闪 / 冒出一个黑 cmd 窗口」。
  另外 AstrBot 里各插件再 spawn `svc.exe` / `ffmpeg` / `powershell` 时若没带
  CREATE_NO_WINDOW，也会各自闪窗（那几个点已在代码里补了 creationflags）。

  做法：用 GUI 子系统的 `pythonw.exe` 跑同一入口 —— 进程根本没有控制台，自然不会有窗口：
      pythonw.exe -X utf8 -c "import sys; sys.argv=['astrbot','run']; from astrbot.cli.__main__ import cli; cli()"
  日志仍照旧重定向到 logs\astrbot.out.log / astrbot.err.log。

  用法：
    powershell -NoProfile -File scripts\_start_astrbot_hidden.ps1
    powershell -NoProfile -File scripts\_start_astrbot_hidden.ps1 -Restart
#>
param(
    [switch]$Restart,
    [int]$WaitSeconds = 75
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$astrbotRoot = Join-Path $root 'astrbot'
$py = Join-Path $astrbotRoot '.venv\Scripts\pythonw.exe'
$logs = Join-Path $root 'logs'
if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs -Force | Out-Null }

function Get-PortOwner([int]$port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}

if (-not (Test-Path $py)) {
    Write-Host "✗ 找不到 pythonw：$py" -ForegroundColor Red
    exit 1
}

$owners = @(Get-PortOwner 11229) + @(Get-PortOwner 6185) | Sort-Object -Unique
if ($owners.Count -gt 0) {
    if (-not $Restart) {
        Write-Host ("  已在运行（端口属主 PID={0}），未加 -Restart 不改动" -f ($owners -join ',')) -ForegroundColor DarkGray
        exit 0
    }
    foreach ($pid128 in $owners) {
        Write-Host "  停掉旧实例 PID=$pid128" -ForegroundColor DarkGray
        Stop-Process -Id $pid128 -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 3
}

# 密钥必须在 Start-Process 之前进进程环境，否则子进程继承不到
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$env:ASTRBOT_RELOAD = '1'
if (-not $env:DEEPSEEK_API_KEY) {
    $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
}

$entry = Join-Path $PSScriptRoot '_astrbot_entry.py'
$outLog = Join-Path $logs 'astrbot.out.log'
$errLog = Join-Path $logs 'astrbot.err.log'

# 启动前轮转：RedirectStandardOutput 是覆盖写，但异常退出残留的大文件会越堆越多。
# 只保留上一份 .1，超过 8MB 才轮转（小文件直接覆盖，不占地也不丢近期现场）。
foreach ($f in @($outLog, $errLog)) {
    if (Test-Path $f) {
        $len = (Get-Item $f).Length
        if ($len -gt 8MB) {
            $keep = $f + '.1'
            if (Test-Path $keep) { Remove-Item $keep -Force -ErrorAction SilentlyContinue }
            try { Move-Item $f $keep -Force -ErrorAction Stop } catch { }
        }
    }
}

$p = Start-Process -FilePath $py -ArgumentList @('-X', 'utf8', $entry, 'run') `
    -WorkingDirectory $astrbotRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Write-Host ("  → astrbot 已启动 PID={0}（pythonw，无控制台）日志={1}" -f $p.Id, $outLog) -ForegroundColor DarkGray

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    if (Get-PortOwner 11229) {
        Write-Host "  OK 11229 已监听（面板 6185）" -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host "  ✗ ${WaitSeconds}s 内 11229 未监听，看 $errLog" -ForegroundColor Yellow
exit 2
