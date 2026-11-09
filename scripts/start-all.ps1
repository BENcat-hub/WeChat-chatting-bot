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
$WEFLOW50_ROOT = $env:WEFLOW50_ENV_ROOT
if (-not $WEFLOW50_ROOT) {
    foreach ($cand in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\WeFlow50'),
            'D:\WeFlow50',
            'C:\WeFlow50',
            (Join-Path $env:USERPROFILE 'Documents\WeFlow50'))) {
        if ($cand -and (Test-Path (Join-Path $cand 'resources\image'))) { $WEFLOW50_ROOT = $cand; break }
    }
}
if (-not $WEFLOW50_ROOT) {
    # 2026-11-03 误删把 5.0 的树带走了，这里退回已安装的 6.3.2（组件到期，可能 -101）
    $WEFLOW50_ROOT = 'C:\Program Files\WeFlow'
    Write-Host '  ⚠ 没找到 WeFlow 5.0 的树（WEFLOW50_ENV_ROOT 未设置且候选目录都不在），退回 6.3.2' -ForegroundColor Yellow
}
$WEFLOW_LAUNCHER = Join-Path $PSScriptRoot 'start-weflow50.cmd'

# 2026-11-01 修：本机 Get-NetTCPConnection 不可用（NetTCPIP 模块缺失，见 HANDOVER §4 铁律 3）。
# 老写法 catch 里 return $false，等于**恒判"端口没在监听"**——已在跑的服务会被重复拉起，
# 每轮自检全刷 ✗。统一改成 netstat 解析，带 2 秒缓存（Wait-Port 每秒轮询，不能每次都起进程）。
$script:_listenMap = @{}
$script:_listenStamp = [datetime]::MinValue
function Get-ListenMap {
    if (((Get-Date) - $script:_listenStamp).TotalSeconds -ge 2 -or $script:_listenMap.Count -eq 0) {
        $map = @{}
        try {
            foreach ($line in (& netstat.exe -ano -p tcp 2>$null)) {
                if ($line -match '^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$') {
                    $map[[int]$Matches[1]] = [int]$Matches[2]
                }
            }
        } catch {}
        $script:_listenMap = $map
        $script:_listenStamp = Get-Date
    }
    return $script:_listenMap
}
function Test-Port([int]$port) {
    return (Get-ListenMap).ContainsKey($port)
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
    # 2026-11-04：改用 GUI 子系统的 pythonw + _astrbot_entry.py 启动。
    # 用 astrbot.exe（console 脚本）时 Windows 必给它建一个控制台，就是"跑 AstrBot 时
    # 冒出来的黑 cmd 窗口"——实测 astrbot.exe 会让 conhost 计数 +1，pythonw 为 +0。
    $exe = Join-Path $astrbotRoot '.venv\Scripts\pythonw.exe'
    $entry = Join-Path $PSScriptRoot '_astrbot_entry.py'
    if (-not (Test-Path $exe) -or -not (Test-Path $entry)) {
        Write-Host ("  ✗ 找不到 {0} 或 {1}" -f $exe, $entry) -ForegroundColor Red
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
        # zzz：变量名不能叫 $pid（PowerShell 只读自动变量）
        # 群会话补齐：换 platform_id（wechat-ob11）后，历史群在 AstrBot 里没有会话，
        # 群主动消息会撞 `No active conversation is available for proactive replies`。
        # 幂等：已存在的会话不会被改动。
        $ensureGroups = Join-Path (Split-Path $PSScriptRoot -Parent) '_restore-20261103\ensure_group_conversations.py'
        if (Test-Path $ensureGroups) {
            try {
                & $exe '-X' 'utf8' $ensureGroups '--apply' | ForEach-Object { '  ' + $_ }
            } catch {
                Write-Host '  ⚠ 群会话补齐失败（不影响启动）' -ForegroundColor Yellow
            }
        }
        Start-Svc 'astrbot' $exe $astrbotRoot @('-X', 'utf8', $entry, 'run')
        if (Wait-Port 11229 60) { Write-Host '  11229 已监听（无控制台窗口）' }
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

# ---------------------------------------------------------------- 3.6 纳西妲语音（Bert-VITS2 单段式）
Write-Host "`n[+] 纳西妲语音（Bert-VITS2 直出 44.1k，端口 8795；缺权重时回落 edge+svc）" -ForegroundColor Cyan
$nahidaScript = Join-Path $PSScriptRoot 'nahida_tts_server.py'
$voiceModel = Join-Path $root 'rvc-backend\models_voice\纳西妲\config.json'
$bv2Weight = Join-Path $root 'bertvits2\pretrained\纳西妲\G_114000.pth'
$rvcPy = Join-Path $root 'rvc-backend\.venv\Scripts\python.exe'
if (-not $env:NAHIDA_ENGINE) {
    # 2026-11-09：默认回到 edge + svc(models_voice/纳西妲 G_40000) —— Bert-VITS2 的
    # G_114000 对「忙归忙」这类口语会念成「王桂梅」，被听成粤语；edge 的中文朗读准确，
    # svc 再换成纳西妲音色。要用 Bert-VITS2 时设 NAHIDA_ENGINE=bv2。
    $env:NAHIDA_ENGINE = 'edge'
}
Write-Host ("  引擎：" + $env:NAHIDA_ENGINE)
if (Test-Port 8795) {
    Write-Host '  已在监听 8795'
} elseif ((Test-Path $nahidaScript) -and (Test-Path $voiceModel) -and (Test-Path (Join-Path $root 'secrets\mimo_api_key.txt'))) {
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUTF8 = '1'
    Start-Svc 'nahida-tts' $rvcPy $root @('-X', 'utf8', $nahidaScript, '--port', '8795')
    if (Wait-Port 8795 20) { Write-Host '  8795 已监听' }
    else { Write-Host '  ✗ 20 秒内 8795 未监听，查看 logs\nahida-tts.err.log' -ForegroundColor Yellow }
} else {
    Write-Host '  ⚠ 缺少 nahida_tts_server.py / 纳西妲模型 / miMo key，跳过（语音会回落到 mimo 原声）' -ForegroundColor Yellow
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
    # 8766 在听不等于「能发消息」：bridge 必须是 High 完整性，否则 UIPI 让它点不动微信。
    $ilPy = Join-Path $root '.venv-ocr\Scripts\python.exe'
    $ilScript = Join-Path $PSScriptRoot 'il_of.py'
    $ilLine = ''
    if ((Test-Path $ilPy) -and (Test-Path $ilScript)) {
        $ilLine = (& $ilPy -X utf8 $ilScript 2>$null | Select-String -Pattern 'bridge:8766').Line
    }
    if ($ilLine -match 'High') {
        Write-Host '  已在监听 8766（High 完整性，可操作微信）'
    } else {
        Write-Host ("  ⚠ 8766 在听但 bridge 不是 High 完整性（{0}）→ 提权重启，否则发消息会超时" -f ($(if ($ilLine) { $ilLine.Trim() } else { '未知' }))) -ForegroundColor Yellow
        $elevRestart = Join-Path $PSScriptRoot '_start_bridge_elevated.ps1'
        if (Test-Path $elevRestart) {
            & $elevRestart -Restart | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
            if (Wait-Port 8766 40) { Write-Host '  8766 已监听（bridge 以 High 完整性运行）' }
            else { Write-Host '  ✗ 提权重启后 8766 未监听，看 logs\bridge.out.log' -ForegroundColor Yellow }
        }
    }
} elseif ((Test-Path $bridgePy) -and (Test-Path (Join-Path $bridgeDir 'config.json'))) {
    $env:PYTHONIOENCODING = 'utf-8'
    # 2026-11-05：bridge **必须以 High 完整性运行**。微信是 High 进程（本机 UAC 设成
    # 「不提示直接提升」，管理员外壳启动的都拿 High），而 DSH 桌面拉起的进程是 Low，
    # UIPI 直接拦掉键鼠注入 → 发送全超时。提权后实测 SetCursorPos 返回 1、消息一发即达。
    $elevStart = Join-Path $PSScriptRoot '_start_bridge_elevated.ps1'
    if (Test-Path $elevStart) {
        & $elevStart | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
        if (Wait-Port 8766 40) { Write-Host '  8766 已监听（bridge 以 High 完整性运行）' }
        else { Write-Host '  ✗ 40 秒内 8766 未监听，查看 logs\bridge.out.log / akasha-bridge\bridge.log' -ForegroundColor Yellow }
    } else {
        Write-Host '  ⚠ 缺 scripts\_start_bridge_elevated.ps1，退回普通启动（会因 UIPI 发不出消息）' -ForegroundColor Yellow
        Start-Svc 'bridge' $bridgePy $bridgeDir @('main.py')
        if (Wait-Port 8766 40) { Write-Host '  8766 已监听' }
        else { Write-Host '  ✗ 40 秒内 8766 未监听' -ForegroundColor Yellow }
    }
} else {
    Write-Host ("  ✗ 缺少 {0} 或 config.json" -f $bridgePy) -ForegroundColor Red
}

# OCR 常驻服务（RapidOCR，命名管道）：wxui 读会话名靠它；缺失时只剩 Windows OCR，
# 读不出微信侧栏 12px 的昵称，发送会一路重试到 45s 超时。
$ocrPy = Join-Path $root '.venv-ocr\Scripts\python.exe'
$ocrSrv = Join-Path $PSScriptRoot 'ocr_server.py'
if ((Test-Path $ocrPy) -and (Test-Path $ocrSrv)) {
    $pipeOk = @([System.IO.Directory]::GetFiles('\\.\pipe\') | Where-Object { $_ -match 'wxui-ocr' }).Count -gt 0
    if ($pipeOk) { Write-Host '  OCR 服务在线（pipe \\.\pipe\wxui-ocr）' }
    else {
        $ocrCmd = "Start-Process -FilePath '$ocrPy' -ArgumentList @('-X','utf8','$ocrSrv','--serve','--idle','0') -WorkingDirectory '$root' -WindowStyle Hidden -Verb RunAs"
        Start-Process powershell -ArgumentList @('-NoProfile','-Command',$ocrCmd) -WindowStyle Hidden -Wait
        Start-Sleep -Seconds 4
        $pipeOk = @([System.IO.Directory]::GetFiles('\\.\pipe\') | Where-Object { $_ -match 'wxui-ocr' }).Count -gt 0
        if ($pipeOk) { Write-Host '  OCR 服务已拉起' } else { Write-Host '  ⚠ OCR 服务没起来，查看 logs\ocr-server.err.log' -ForegroundColor Yellow }
    }
} else {
    Write-Host ("  ⚠ 缺 {0} 或 ocr_server.py：wxui 只能退回 Windows OCR，发送很可能超时" -f $ocrPy) -ForegroundColor Yellow
}

# bridge 看门狗：8766 掉线、或 11229 的 OneBot 连接断了（2026-10-29 静默失联 3.5 小时，
# 期间 37 条主动消息全部 ApiNotAvailable 失败，见 docs\PROACTIVE-SILENT-20261029.md）
$wdScript = Join-Path $PSScriptRoot '_start_bridge_watchdog.ps1'
if (Test-Path $wdScript) {
    try { & $wdScript | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray } }
    catch { Write-Host ("  ⚠ bridge 看门狗没起来：{0}" -f $_.Exception.Message) -ForegroundColor Yellow }
    # 2026-11-02：看门狗会随发起它的进程树一起被杀（00:00 那次 bridge + 看门狗同时消失）。
    # 脚本说"已启动"不等于进程还在，这里复核一次真实存活。
    Start-Sleep -Seconds 2
    $wdAlive = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match '_bridge_watchdog\.py' })
    if ($wdAlive.Count -gt 0) {
        Write-Host ('  ✔ 看门狗存活 pid={0}' -f (($wdAlive | ForEach-Object { $_.ProcessId }) -join ',')) -ForegroundColor DarkGray
    } else {
        Write-Host '  ⚠ 看门狗进程不在（脚本报成功也是假的）→ 再跑一次 scripts\_start_bridge_watchdog.ps1' -ForegroundColor Yellow
    }
}

# bridge 看门狗：8766 掉线 / 11229 的 OneBot 连接断了（2026-10-29 那次静默失联 3.5h）都会自动拉回
$wdScript = Join-Path $PSScriptRoot '_start_bridge_watchdog.ps1'
if (Test-Path $wdScript) {
    try { & $wdScript | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray } }
    catch { Write-Host ("  ⚠ 看门狗没起来：{0}" -f $_.Exception.Message) -ForegroundColor Yellow }
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
foreach ($pair in @(@(5031, 'WeFlow 读消息'), @(6185, 'AstrBot 面板'), @(11229, 'AstrBot OneBot WS'), @(8766, 'bridge 面板'), @(8790, '即梦生图本地服务'), @(3333, 'AI点歌后端'), @(8795, '纳西妲语音'))) {
    $ok = Test-Port $pair[0]
    Write-Host ("  {0,-6} {1,-20} {2}" -f $pair[0], $pair[1], $(if ($ok) { '在听' } else { '未监听' })) -ForegroundColor $(if ($ok) { 'Green' } else { 'Yellow' })
}
Write-Host '  9877   WeeMessenger         未启动（设计如此：文字/图片走 wxui）' -ForegroundColor DarkGray
$wdNow = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match '_bridge_watchdog\.py' })
Write-Host ("  {0,-6} {1,-20} {2}" -f 'wd', 'bridge 看门狗', $(if ($wdNow.Count -gt 0) { '在跑 pid=' + (($wdNow | ForEach-Object { $_.ProcessId }) -join ',') } else { '未运行（8766 掉了不会自动拉回）' })) -ForegroundColor $(if ($wdNow.Count -gt 0) { 'Green' } else { 'Yellow' })
Write-Host "`n下一步：确认微信已扫码登录 → 发消息给用户E 验证被动回复/表情包；停止用 stop-all.ps1。" -ForegroundColor Green
