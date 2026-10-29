# 一键启动 wechat-ai-stack（顺序：微信 → WeFlow → AstrBot → bridge）
#
# 用法（在任意终端里执行，无需管理员）：
#     pwsh -File <REPO_ROOT>\scripts\start-all.ps1
# 常用变体：
#     ... start-all.ps1 -FixWeFlowAnchor      # WeFlow 起不来（5031 不监听）时先把 -105 anchor 修一遍
#     ... start-all.ps1 -SkipWeChat           # 微信已经开着
#     ... start-all.ps1 -SkipAstrBot          # 只补 bridge
#     ... start-all.ps1 -NoPreflight          # 最后不做端口自检
#
# 说明：
#   * WeFlow 走 scripts\start-weflow50.cmd（回收站找回的 5.0.0 树 + 已打两处 4 字节补丁的
#     wcdb_api.dll）。**不要**再启动 C:\Program Files\WeFlow\WeFlow.exe（6.3.2，组件已到期
#     → 必定 -101；且两者共用 %APPDATA%\weflow 与同一个微信 DB 句柄，不能同时运行）。
#   * WeeMessenger(9877) **故意不启动**：文本/图片发送都走 scripts\wxui.py（UI 自动化），
#     只有要发 GIF 表情包时才需要它；它的鼠标漫游已在 config.yaml 里关掉，但仍不需要常驻。
#   * 微信必须人工扫码登录；发送前窗口不能最小化（wxui.py 会自动 SW_RESTORE，但登录态只能人工来）。
#     因此本脚本启动微信后会调用 show-wechat-window.py，把收进托盘的主窗口还原成可见状态
#     ——否则 bridge 的 /senders 会一直是 ok=false / "WeChat window not found"，消息发不出去。
#   * AstrBot 需要 DEEPSEEK_API_KEY：本脚本从 User 作用域读进进程环境后再启动子进程（密钥不落盘）。
#   * 停止：pwsh -File scripts\stop-all.ps1

param(
    [switch]$SkipWeChat,
    [switch]$SkipWeFlow,
    [switch]$SkipAstrBot,
    [switch]$SkipBridge,
    [switch]$FixWeFlowAnchor,
    [switch]$NoPreflight
)

$ErrorActionPreference = 'Continue'
$root   = Split-Path -Parent $PSScriptRoot          # ...\wechat-ai-stack
$logs   = Join-Path $root 'logs'
$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$pids = @{}

$WECHAT_EXE = 'C:\Program Files\Tencent\Weixin\Weixin.exe'
$WEFLOW_EXE = 'C:\Program Files\WeFlow\WeFlow.exe'          # 6.3.2：组件已到期，仅用于识别/清理
$WEFLOW_LAUNCHER = Join-Path $PSScriptRoot 'start-weflow50.cmd'
$WEFLOW50_ROOT = '<WEFLOW_ROOT>'

function Test-Port([int]$port) {
    try { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
    catch { return $false }
}
function Wait-Port([int]$port, [int]$seconds = 40, [string]$label = '') {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port $port) { return $true }
        Start-Sleep -Seconds 1
    }
    return (Test-Port $port)
}
function Start-Svc([string]$name, [string]$exe, [string]$workdir, [string[]]$argList) {
    $log = Join-Path $logs "$name-$stamp.log"
    $err = Join-Path $logs "$name-$stamp.err.log"
    $p = Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $workdir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError $err
    $script:pids[$name] = @{ pid = $p.Id; log = $log; err = $err; workdir = $workdir }
    Write-Host ("  → {0} 已启动 PID={1} 日志={2}" -f $name, $p.Id, $log) -ForegroundColor DarkGray
}

Write-Host "`n=== wechat-ai-stack 一键启动 ===`n" -ForegroundColor Cyan

# ---------------------------------------------------------------- 1. 微信
Write-Host '[1/4] 微信（Weixin.exe）' -ForegroundColor Cyan
$wx = Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue
if ($SkipWeChat) {
    Write-Host '  已跳过（-SkipWeChat）'
} elseif ($wx) {
    Write-Host ("  已在运行 PID={0}" -f ($wx.Id -join ','))
} elseif (Test-Path $WECHAT_EXE) {
    Start-Process -FilePath $WECHAT_EXE | Out-Null
    Start-Sleep -Seconds 8
    $wx = Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue
    if ($wx) { Write-Host ("  已启动 PID={0}" -f ($wx.Id -join ',')) } else { Write-Host '  ⚠ 启动后没看到进程，请手动打开微信' -ForegroundColor Yellow }
} else {
    Write-Host ("  ✗ 找不到 $WECHAT_EXE") -ForegroundColor Red
}
Write-Host '  提示：需要人工扫码登录；登录前 WeFlow 读不到数据、wxui 也无法发送。' -ForegroundColor DarkYellow

# 微信窗口若被收进托盘（IsWindowVisible=False），wxui.py 会一直报
# "WeChat window not found" → 一条消息都发不出去。这里先无条件还原一次。
$showWeChat = Join-Path $PSScriptRoot 'show-wechat-window.py'
$showPy = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
if ((Test-Path $showWeChat) -and (Test-Path $showPy)) {
    $env:PYTHONIOENCODING = 'utf-8'
    & $showPy -X utf8 $showWeChat
} else {
    Write-Host '  （跳过微信窗口还原：缺 show-wechat-window.py 或 astrbot venv）' -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- 2. WeFlow
Write-Host "`n[2/4] WeFlow（消息读取 + SSE 推送，端口 5031）" -ForegroundColor Cyan
if ($SkipWeFlow) {
    Write-Host '  已跳过（-SkipWeFlow）'
} elseif (Test-Port 5031) {
    Write-Host '  已在监听 5031'
} else {
    if ($FixWeFlowAnchor) {
        Write-Host '  anchor 体检（6.3.2 时代的手段；5.0.0 下一般用不到）' -ForegroundColor DarkYellow
        & (Join-Path $PSScriptRoot 'weflow-anchor-check.ps1') -Fix
    }
    # 6.3.2 若在跑先关掉：它的组件已到期（必定 -101），且与 5.0.0 共用 %APPDATA%\weflow
    $old = Get-CimInstance Win32_Process -Filter "Name='WeFlow.exe'" -ErrorAction SilentlyContinue
    if ($old) {
        Write-Host ('  ⚠ 检测到 6.3.2 的 WeFlow.exe 在跑，先停掉：PID={0}' -f (($old | ForEach-Object { $_.ProcessId }) -join ',')) -ForegroundColor Yellow
        $old | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Path $WEFLOW_LAUNCHER)) {
        Write-Host ("  ✗ 找不到启动器 {0}" -f $WEFLOW_LAUNCHER) -ForegroundColor Red
    } else {
        # ffmpeg-static 的 exe 必须存在：微信收到的照片是 HEVC/wxgf，解码全靠它。
        # 5.0.0 树的 npm 包只带了 JS（postinstall 下载没跑过）→ 缺 exe 时所有来图都导不出来。
        $ffDst = Join-Path $WEFLOW50_ROOT 'node_modules\ffmpeg-static\ffmpeg.exe'
        if (-not (Test-Path $ffDst)) {
            $ffSrc = 'C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe'
            if (Test-Path $ffSrc) {
                Write-Host '  ffmpeg.exe 缺失，从 6.3.2 安装目录恢复（解码微信 HEVC 图片必需）' -ForegroundColor Yellow
                Copy-Item $ffSrc $ffDst -Force
            } else {
                Write-Host ("  ⚠ {0} 缺失且无来源可复制；对方发来的图片将无法识别" -f $ffDst) -ForegroundColor Yellow
            }
        }
        Write-Host '  启动 WeFlow 5.0.0（start-weflow50.cmd：清 ELECTRON_RUN_AS_NODE → electron.exe <树>）'
        Start-Process -FilePath $env:ComSpec -ArgumentList '/c', ('"{0}"' -f $WEFLOW_LAUNCHER) -WindowStyle Hidden | Out-Null
        if (Wait-Port 5031 60) {
            Write-Host '  5031 已监听'
        } else {
            Write-Host ("  ✗ 60 秒内 5031 仍未监听：检查 {0} 是否完整、是否误开了 6.3.2" -f $WEFLOW50_ROOT) -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------------- 3. AstrBot
Write-Host "`n[3/4] AstrBot（AI 大脑：面板 6185 + OneBot 反向 WS 11229）" -ForegroundColor Cyan
if ($SkipAstrBot) {
    Write-Host '  已跳过（-SkipAstrBot）'
} elseif (Test-Port 11229) {
    Write-Host '  已在监听 11229'
} else {
    $astrbotRoot = Join-Path $root 'astrbot'
    $exe = Join-Path $astrbotRoot '.venv\Scripts\astrbot.exe'
    if (-not (Test-Path $exe)) {
        Write-Host ("  ✗ 找不到 {0}" -f $exe) -ForegroundColor Red
    } else {
        # 关键：密钥必须在 Start-Process 之前进进程环境，否则子进程继承不到 → provider 报 Missing credentials
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONUTF8 = '1'
        # 插件热重载：见 astrbot-start.ps1 的说明（不开则改插件 .py 必须整进程重启）
        $env:ASTRBOT_RELOAD = '1'
        if (-not $env:DEEPSEEK_API_KEY) {
            $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
        }
        if (-not $env:DEEPSEEK_API_KEY) { Write-Host '  ⚠ DEEPSEEK_API_KEY 在进程环境与 User 作用域都为空，DeepSeek 提供方会不可用' -ForegroundColor Yellow }
        Start-Svc 'astrbot' $exe $astrbotRoot @('run')
        if (Wait-Port 11229 60) { Write-Host '  11229 已监听' }
        else { Write-Host '  ✗ 60 秒内 11229 未监听，查看 logs\astrbot-*.err.log' -ForegroundColor Yellow }
    }
}

# ---------------------------------------------------------------- 3.5 即梦生图本地服务
Write-Host "`n[+] 即梦生图本地服务（火山签名 → OpenAI 兼容，端口 8790）" -ForegroundColor Cyan
$jimengScript = Join-Path $PSScriptRoot 'jimeng_image_server.py'
$jimengPy = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
if (Test-Port 8790) {
    Write-Host '  已在监听 8790'
} elseif ((Test-Path $jimengScript) -and (Test-Path $jimengPy) -and (
        (Test-Path (Join-Path $root 'secrets\siliconflow_api_key.txt')) -or
        (Test-Path (Join-Path $root 'secrets\volcengine_ak_sk.txt')))) {
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    # 2026-10-22：火山图生图 req_key `jimeng_seedream46_cvtob` 在本账号被
    # HTTP 401 / code 50400 Access Denied 拒绝，带参考图的通道整条不可用。
    # 所以默认后端改成 SiliconFlow（文生图 Qwen/Qwen-Image，图生图
    # Qwen/Qwen-Image-Edit-2509，实测 13s / 26-63s）。要回火山就把下面这行
    # 改成 'volc'（或启动前自己设 $env:JIMENG_BACKEND）。
    if (-not $env:JIMENG_BACKEND) { $env:JIMENG_BACKEND = 'sf' }
    Start-Svc 'jimeng' $jimengPy $root @('-X', 'utf8', $jimengScript, '--port', '8790')
    if (Wait-Port 8790 20) { Write-Host '  8790 已监听' }
    else { Write-Host '  ✗ 20 秒内 8790 未监听，查看 logs\jimeng-*.err.log' -ForegroundColor Yellow }
} else {
    Write-Host '  ⚠ 缺少 jimeng_image_server.py / python 或 secrets 下的 volcengine_ak_sk.txt / siliconflow_api_key.txt，跳过（生图会失败）' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 3.7 AI点歌后端
Write-Host "`n[+] AI点歌后端（so-vits-svc + Demucs，Gradio 端口 3333）" -ForegroundColor Cyan
$coverScript = Join-Path $PSScriptRoot 'start-cover-backend.ps1'
$coverPy = Join-Path $root 'rvc-backend\.venv\Scripts\python.exe'
if ((Test-Path $coverScript) -and (Test-Path $coverPy)) {
    # 不再只判「端口在听」就跳过：卡死的旧进程会占着 3333 但不再响应，
    # 判断与清理都交给 start-cover-backend.ps1（它带 HTTP 探活 + 自动清理）。
    & $coverScript -Port 3333 | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
} else {
    Write-Host '  ⚠ 缺少 rvc-backend\.venv 或 start-cover-backend.ps1，跳过（AI点歌会失败）' -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 4. bridge
Write-Host "`n[4/4] akasha-bridge（中间桥接，面板 8766）" -ForegroundColor Cyan
$bridgeDir = Join-Path $root 'akasha-bridge'
$bridgePy  = Join-Path $bridgeDir '.venv\Scripts\python.exe'
if ($SkipBridge) {
    Write-Host '  已跳过（-SkipBridge）'
} elseif (Test-Port 8766) {
    Write-Host '  已在监听 8766'
} elseif ((Test-Path $bridgePy) -and (Test-Path (Join-Path $bridgeDir 'config.json'))) {
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Svc 'bridge' $bridgePy $bridgeDir @('main.py')
    if (Wait-Port 8766 40) { Write-Host '  8766 已监听' }
    else { Write-Host '  ✗ 40 秒内 8766 未监听，查看 logs\bridge-*.err.log（运行日志其实在 akasha-bridge\bridge.log）' -ForegroundColor Yellow }
} else {
    Write-Host ("  ✗ 缺少 {0} 或 config.json" -f $bridgePy) -ForegroundColor Red
}

# ---------------------------------------------------------------- PID 记录
$pidsFile = Join-Path $logs 'pids.json'
if (Test-Path $pidsFile) {
    try {
        $old = Get-Content $pidsFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $old.PSObject.Properties) { if (-not $pids.ContainsKey($p.Name)) { $pids[$p.Name] = $p.Value } }
    } catch {}
}
$pids | ConvertTo-Json -Depth 5 | Set-Content $pidsFile -Encoding UTF8

# ---------------------------------------------------------------- 自检
if (-not $NoPreflight) {
    Write-Host "`n等 6 秒后自检..." -ForegroundColor Cyan
    Start-Sleep -Seconds 6
    & (Join-Path $PSScriptRoot 'preflight.ps1')
}

Write-Host "`n=== 端口一览 ===" -ForegroundColor Cyan
foreach ($pair in @(@(5031, 'WeFlow 读消息'), @(6185, 'AstrBot 面板'), @(11229, 'AstrBot OneBot WS'), @(8766, 'bridge 面板'), @(8790, '即梦生图本地服务'), @(3333, 'AI点歌后端'))) {
    $ok = Test-Port $pair[0]
    Write-Host ("  {0,-6} {1,-20} {2}" -f $pair[0], $pair[1], $(if ($ok) { '在听' } else { '未监听' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Yellow' })
}
Write-Host '  9877   WeeMessenger         未启动（设计如此：文字/图片走 wxui）' -ForegroundColor DarkGray
Write-Host "`n下一步：确认微信已扫码登录 → 发消息给用户E 验证被动回复/表情包；停止用 stop-all.ps1。" -ForegroundColor Green
