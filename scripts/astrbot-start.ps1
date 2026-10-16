<#
.SYNOPSIS
    启动 wechat-ai-stack 的 AstrBot 4.28.2（OneBot v11 反向 WS + DeepSeek）。

.DESCRIPTION
    - 从脚本自身位置推导 AstrBot 根目录（..\astrbot）与 venv CLI（.venv\Scripts\astrbot.exe）。
    - 保证 DEEPSEEK_API_KEY 在进程环境中可用：优先用已有进程环境变量，否则从 User 作用域读取
      （密钥只存在于内存/环境，永不写入任何文件）。
    - 前台运行 `astrbot run`，输出同时 tee 到 logs\astrbot-<时间戳>.log。
    - 不新开任何端口：面板 6185（+ 反向 WS 11229），与 data\cmd_config.json 保持一致。

.PARAMETER LogFile
    指定日志文件路径；默认 logs\astrbot-yyyyMMdd-HHmmss.log。

.PARAMETER NoTee
    只输出到控制台，不写日志文件（调试用）。

.EXAMPLE
    pwsh -File .\scripts\astrbot-start.ps1
    # 后台常驻：Start-Job / job 管理器里运行同一命令即可

.NOTES
    面板密码与初始化信息见 logs\astrbot-notes.txt。
    首次初始化（若 data\cmd_config.json 不存在）：
        $env:ASTRBOT_DASHBOARD_INITIAL_PASSWORD='<自定义密码>'; astrbot init -y
    停止：Ctrl+C（前台）或结束对应 pwsh 进程。
#>
[CmdletBinding()]
param(
    [string]$LogFile,
    [switch]$NoTee
)

$ErrorActionPreference = 'Stop'

# Windows 控制台默认 GBK，AstrBot 启动横幅含 emoji，会让 loguru 的流 handler 抛
# UnicodeEncodeError（"--- Logging error in Loguru Handler ---"）。强制 UTF-8 规避。
$env:PYTHONIOENCODING = 'utf-8'
if ($PSVersionTable.PSVersion.Major -ge 6) { $OutputEncoding = [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 }

# 插件热重载：AstrBot 4.28 只在 ASTRBOT_RELOAD=1 时才启动 watchfiles 监视器
# （astrbot\core\star\star_manager.py:222）。不开的话，改完插件 .py 必须整进程重启，
# 而 AstrBot 不在线的窗口里 bridge 收不到推流目标（现已改为缓存补发）。
$env:ASTRBOT_RELOAD = '1'

$stackRoot   = Split-Path -Parent $PSScriptRoot          # ...\wechat-ai-stack
$astrbotRoot = Join-Path $stackRoot 'astrbot'
$exe         = Join-Path $astrbotRoot '.venv\Scripts\astrbot.exe'
$configPath  = Join-Path $astrbotRoot 'data\cmd_config.json'
$logDir      = Join-Path $stackRoot 'logs'

if (-not (Test-Path $exe)) {
    throw "AstrBot CLI 不存在: $exe（请先创建 venv 并安装 astrbot）"
}
if (-not (Test-Path $configPath)) {
    throw "AstrBot 尚未初始化（缺少 $configPath）。请先运行: astrbot init -y"
}
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
if (-not $LogFile) {
    $LogFile = Join-Path $logDir ('astrbot-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
}

# DeepSeek API key：进程环境优先，其次 User 作用域；只驻留内存
if (-not $env:DEEPSEEK_API_KEY) {
    $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
}
if (-not $env:DEEPSEEK_API_KEY) {
    throw 'DEEPSEEK_API_KEY 未设置（进程环境与 User 作用域均为空），AstrBot 的 DeepSeek 提供方将不可用。'
}

Write-Host "AstrBot root : $astrbotRoot"
Write-Host "AstrBot CLI  : $exe"
Write-Host "Log file     : $LogFile"
Write-Host "Dashboard    : http://127.0.0.1:6185/   (OneBot v11 反向 WS: ws://127.0.0.1:11229/ws)"
Write-Host ""

Set-Location $astrbotRoot
if ($NoTee) {
    & $exe run
} else {
    & $exe run *>&1 | Tee-Object -FilePath $LogFile
}
exit $LASTEXITCODE
