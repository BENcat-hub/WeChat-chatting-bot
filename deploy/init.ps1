<#
.SYNOPSIS
    一键初始化脚本：在解压后的仓库根目录里准备 Python 环境、安装依赖、生成配置、打赢 WeFlow 补丁。

.DESCRIPTION
    假设你已经拿到整个 wechat-ai-stack 目录，并且本机已安装：
      - Windows 10/11
      - 微信 PC 版 4.x（已登录）
      - Python 3.12（`py -3.12` 或 `python` 可用）
      - Node.js 20+ 与 npm（要跑 WeFlow 时必须）
      - FFmpeg 可执行文件（WeFlow 需要，见 -FfmpegPath）

    本脚本只做「准备工作」，不会自动替你改微信配置或注入任何东西。
    所有步骤默认幂等，可重复运行。

.PARAMETER Root
    仓库根目录。默认取本脚本所在目录的上一级。

.PARAMETER FfmpegPath
    已有的 ffmpeg.exe 路径；会给 weflow\node_modules\ffmpeg-static\ffmpeg.exe 放一份副本。
    不传则跳过（WeFlow 仍可跑，只是 HEVC/wxgf 图片解不出来）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\init.ps1
    powershell -ExecutionPolicy Bypass -File deploy\init.ps1 -FfmpegPath "C:\tools\ffmpeg.exe"
#>

[CmdletBinding()]
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$FfmpegPath = "",
    [switch]$SkipAstrbotDeps,
    [switch]$SkipBridgeDeps,
    [switch]$SkipWeFlowDeps,
    [switch]$SkipPatch
)

$ErrorActionPreference = 'Stop'
function Say([string]$m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Ok([string]$m)  { Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn([string]$m){ Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Die([string]$m) { Write-Host "  [x]  $m" -ForegroundColor Red; exit 1 }

Say "环境检查"
if (-not (Test-Path $Root)) { Die "找不到仓库根目录：$Root" }
Write-Host "  仓库根目录：$Root"

# ---- Python ----
$py = $null
foreach ($cand in @('py -3.12', 'py -3', 'python')) {
    $parts = $cand.Split(' ')
    $exe = $parts[0]
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
        $v = & $exe @($parts[1..($parts.Length - 1)] + @('-c', 'import sys;print(sys.version_info[:2])')) 2>$null
        if ($v -match '\((3), (9|10|11|12|13)\)') { $py = $cand; break }
    } catch { }
}
if (-not $py) { Die "没找到 Python 3.9+。请安装 Python 3.12 并确保 `python` 在 PATH 里。" }
Ok "Python：$py"

# ---- Node ----
$node = Get-Command node -ErrorAction SilentlyContinue
$npm  = Get-Command npm  -ErrorAction SilentlyContinue
if ($node) { Ok "Node：$((& node -v) 2>&1)" } else { Warn "没找到 node —— WeFlow 依赖安装会被跳过" }
if ($npm)  { Ok "npm ：$((& npm -v) 2>&1)" }  else { Warn "没找到 npm  —— WeFlow 依赖安装会被跳过" }

# ---- venv 辅助 ----
function New-Venv([string]$dir, [string]$name) {
    if (Test-Path (Join-Path $dir '.venv\Scripts\python.exe')) { Ok "$name 虚拟环境已存在"; return }
    Say "创建 $name 虚拟环境"
    Push-Location $dir
    try { & $py -m venv .venv } finally { Pop-Location }
    Ok "已创建 $dir\.venv"
}

# ---- AstrBot ----
if (-not $SkipAstrbotDeps) {
    $ast = Join-Path $Root 'astrbot'
    if (Test-Path $ast) {
        New-Venv $ast 'AstrBot'
        $req = Join-Path $ast 'requirements.txt'
        if (Test-Path $req) {
            Say "安装 AstrBot 依赖（可能几分钟）"
            & (Join-Path $ast '.venv\Scripts\python.exe') -m pip install --upgrade pip --quiet
            & (Join-Path $ast '.venv\Scripts\python.exe') -m pip install -r $req
            Ok "AstrBot 依赖安装完成"
        } else { Warn "缺少 $req，跳过" }
    } else { Warn "没有 astrbot 目录，跳过" }
}

# ---- akasha-bridge ----
if (-not $SkipBridgeDeps) {
    $br = Join-Path $Root 'akasha-bridge'
    if (Test-Path $br) {
        New-Venv $br 'akasha-bridge'
        $req = Join-Path $br 'requirements.txt'
        if (Test-Path $req) {
            Say "安装 akasha-bridge 依赖"
            & (Join-Path $br '.venv\Scripts\python.exe') -m pip install --upgrade pip --quiet
            & (Join-Path $br '.venv\Scripts\python.exe') -m pip install -r $req
            # wxui.py 需要 windows-mcp（PyPI 包名 windows-mcp）
            & (Join-Path $br '.venv\Scripts\python.exe') -m pip install windows-mcp
            Ok "akasha-bridge 依赖安装完成"
        } else { Warn "缺少 $req，跳过" }
    } else { Warn "没有 akasha-bridge 目录，跳过" }
}

# ---- WeFlow ----
if (-not $SkipWeFlowDeps) {
    $wf = Join-Path $Root 'weflow'
    if ((Test-Path $wf) -and $npm) {
        if (Test-Path (Join-Path $wf 'node_modules\electron\dist\electron.exe')) {
            Ok "WeFlow node_modules 已就绪"
        } else {
            Say "在 weflow 里执行 npm install（首次下载 Electron，约 200-300 MB，耐心等）"
            Push-Location $wf
            try { & npm install --no-audit --no-fund } finally { Pop-Location }
            Ok "WeFlow 依赖安装完成"
        }
    } else { Warn "跳过 WeFlow 依赖（缺目录或缺 npm）" }
}

# ---- ffmpeg ----
if ($FfmpegPath) {
    if (-not (Test-Path $FfmpegPath)) { Warn "FfmpegPath 不存在：$FfmpegPath" }
    else {
        $dstDir = Join-Path $Root 'weflow\node_modules\ffmpeg-static'
        if (Test-Path (Split-Path -Parent $dstDir)) {
            New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
            Copy-Item $FfmpegPath (Join-Path $dstDir 'ffmpeg.exe') -Force
            Ok "已放置 ffmpeg.exe -> $dstDir"
        } else { Warn "weflow\node_modules 还不存在，先跑 npm install 再传 -FfmpegPath" }
    }
} else { Warn "未提供 -FfmpegPath：WeFlow 解不出 HEVC/wxgf 图片（普通 jpg/png 不受影响）" }

# ---- 配置模板 ----
Say "生成配置文件"
$pairs = @(
    @{ ex = 'akasha-bridge\config.example.json'; dst = 'akasha-bridge\config.json' },
    @{ ex = 'astrbot\data\cmd_config.json.example'; dst = 'astrbot\data\cmd_config.json' }
)
foreach ($p in $pairs) {
    $ex  = Join-Path $Root $p.ex
    $dst = Join-Path $Root $p.dst
    if (-not (Test-Path $ex)) { Warn "缺少模板 $($p.ex)"; continue }
    if (Test-Path $dst) { Ok "$($p.dst) 已存在，保留（不覆盖你的配置）"; continue }
    Copy-Item $ex $dst
    Ok "已生成 $($p.dst)  ← 需要你填 API key / token"
}

# ---- WeFlow DLL 补丁 ----
if (-not $SkipPatch) {
    $wf = Join-Path $Root 'weflow'
    if (Test-Path $wf) {
        Say "检查 WeFlow wcdb_api.dll 补丁"
        & $py (Join-Path $Root 'patches\weflow\apply_wcdb_patch.py') --check $wf
        if ($LASTEXITCODE -ne 0) {
            Warn "补丁未就位，尝试自动应用…"
            & $py (Join-Path $Root 'patches\weflow\apply_wcdb_patch.py') --apply $wf
        }
    }
}

Say "完成。接下来（人工步骤）"
Write-Host @"
  1. 打开 akasha-bridge\config.json，填：
       - access_token       = WeFlow 的 httpApiToken
                              （在 %APPDATA%\weflow\WeFlow-config.json 里）
       - weflow_base_url    = http://127.0.0.1:5031
       - bot_wxid           = 你自己的微信 wxid
       - bot_nicknames      = 你的微信昵称
       - sender_backend     = "wxui"
       - send_dry_run       = true   ← 先保持 true，确认链路通了再改 false
       - wxui_python / wxui_script = 指向本目录的 .venv 与 scripts\wxui.py
  2. 打开 astrbot\data\cmd_config.json，填模型平台的 API key。
  3. 启动顺序（每个都等上一次稳定后再起下一个）：
       a) 微信 PC 版登录
       b) scripts\start-weflow50.cmd        （等 5031 可访问）
       c) akasha-bridge\start.bat           （8766 面板 http://127.0.0.1:8766）
       d) astrbot\.venv\Scripts\python.exe astrbot\main.py
  4. 面板里先看发送链路自检：
       curl http://127.0.0.1:8766/senders
     确认 backend_active = "WxuiSender"、dry_run = false、白名单符合预期。
  5. 排障先看 docs\SETUP.zh-CN.md 与 docs\VERIFICATION.md。

  更完整的架构与接口说明见 README.md 与 docs\PROJECT-BOOK.zh-CN.md。
"@ -ForegroundColor Gray
