# 链路自检：把「微信 ←→ WeFlow ←→ bridge ←→ AstrBot →(发送) WeeMessenger」每一环的健康状况打出来
# 用法：  pwsh -File scripts\preflight.ps1
# 说明：  不修改任何东西，只读探测；没有装/没在跑的项目会显示 ✗，按 docs\SETUP.zh-CN.md 补齐即可。

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot      # wechat-ai-stack
if (-not (Test-Path (Join-Path $root "akasha-bridge"))) { $root = $PSScriptRoot }

function Port-Open([int]$port) {
    try { return (Test-NetConnection -ComputerName 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) }
    catch { return $false }
}
function Http-Get([string]$url) {
    try { return (Invoke-WebRequest -Uri $url -TimeoutSec 6 -UseBasicParsing).Content } catch { return $null }
}
function Mark([bool]$ok) { if ($ok) { "OK " } else { "✗  " } }

Write-Host "`n=== 0) 上次是否非正常关机（断电后先看这里）===" -ForegroundColor Cyan
$shutdownEvents = @()
try { $shutdownEvents = @(Get-WinEvent -FilterHashtable @{ LogName = "System"; Id = 41, 6008 } -MaxEvents 5 -ErrorAction Stop) }
catch { $shutdownEvents = @() }
if ($shutdownEvents.Count -eq 0) {
    Write-Host "OK  近 5 条开关机记录里没有异常关机事件"
} else {
    $lastEv = $shutdownEvents | Sort-Object TimeCreated -Descending | Select-Object -First 1
    $ageH = [Math]::Round(((Get-Date) - $lastEv.TimeCreated).TotalHours, 1)
    $snip = ($lastEv.Message -replace '\s+', ' ')
    $snip = $snip.Substring(0, [Math]::Min(96, $snip.Length))
    if ($ageH -le 72) {
        Write-Host ("✗   上次是非正常关机（断电/硬关机）：事件时间 {0:yyyy-MM-dd HH:mm}，{1} 小时前，id={2}" -f $lastEv.TimeCreated, $ageH, $lastEv.Id) -ForegroundColor Yellow
        Write-Host ("    消息原文：{0}" -f $snip)
        Write-Host "    断电后必查三件："
        Write-Host "    1) 下面 §3.5 插件目录可读性——读不了就是 NTFS 安全描述符被断电弄坏了："
        Write-Host "         powershell -NoProfile -ExecutionPolicy Bypass -File scripts\_fix_plugin_acl.ps1 -LogPath logs\plugin-acl-fix.log   （需 UAC 点“是”）"
        Write-Host "    2) §1 里 8766 与 11229 必须同时通；断电后 bridge 和看门狗都得重拉："
        Write-Host "         scripts\_restart_bridge_detached.ps1  →  scripts\_start_bridge_watchdog.ps1"
        Write-Host "    3) plugin_data 里的 sqlite（supplement.db / search_archive.db）跑一次 PRAGMA quick_check"
    } else {
        Write-Host ("OK  上次非正常关机在 {0:yyyy-MM-dd HH:mm}（{1} 小时前，已经过去很久）" -f $lastEv.TimeCreated, $ageH)
    }
}

Write-Host "`n=== 1) 进程 / 端口 ===" -ForegroundColor Cyan
$wx4 = Get-Process -Name "Weixin" -ErrorAction SilentlyContinue
$wx3 = Get-Process -Name "WeChat" -ErrorAction SilentlyContinue
Write-Host ("{0} 微信 4.x (Weixin.exe)     : {1}" -f (Mark([bool]$wx4)), $(if ($wx4) { "运行中 PID=$($wx4.Id -join ',')" } else { "未运行 —— WeFlow 需要它，请先登录微信小号" }))
Write-Host ("{0} 微信 3.9 (WeChat.exe)     : {1}" -f (Mark([bool]$wx3)), $(if ($wx3) { "运行中（不要和 4.x 同时开自动发送）" } else { "未运行" }))

$ports = @(
    @{ n = "WeFlow HTTP API"; p = 5031; url = "http://127.0.0.1:5031/api/v1/health" },
    @{ n = "WeeMessenger 指令服务"; p = 9877; url = "http://127.0.0.1:9877/health" },
    @{ n = "bridge 控制面板"; p = 8766; url = "http://127.0.0.1:8766/status" },
    @{ n = "AstrBot 反向 WS (OneBot)"; p = 11229; url = $null },
    @{ n = "AstrBot 面板"; p = 6185; url = "http://127.0.0.1:6185/" }
)
foreach ($x in $ports) {
    $open = Port-Open $x.p
    $extra = ""
    if ($open -and $x.url) {
        $body = Http-Get $x.url
        $extra = if ($body) { " <- " + (($body -replace '\s+', ' ').Substring(0, [Math]::Min(80, ($body -replace '\s+', ' ').Length))) } else { " (端口通但 HTTP 无响应)" }
    }
    Write-Host ("{0} {1,-26} : {2}{3}" -f (Mark $open), $x.n, $(if ($open) { "监听中" } else { "未监听" }), $extra)
}

Write-Host "`n=== 2) 发送链路护栏与其配置 ===" -ForegroundColor Cyan
$senders = Http-Get "http://127.0.0.1:8766/senders"
if ($senders) {
    try {
        $s = $senders | ConvertFrom-Json
        Write-Host ("    后端(配置/生效) : {0} / {1}" -f $s.backend_config, $s.backend_active)
        Write-Host ("    dry_run         : {0}{1}" -f $s.dry_run, $(if ($s.dry_run) { "  (干跑：不会真的发微信)" } else { "  ⚠ 真实发送已开启" }))
        Write-Host ("    白名单          : {0}" -f $(if ($s.whitelist.Count) { $s.whitelist -join ', ' } else { "（空 = 不限对象 ⚠）" }))
        Write-Host ("    图片通道        : {0}" -f $s.image_mode)
        # /senders 里发送后端自己的健康信息统一放在 weemessenger 键下（wxui/weemessenger 共用该槽位）
        $h = $s.weemessenger
        $detail = if ($h.hwnd) { "hwnd=$($h.hwnd) rect=$($h.rect -join ',')" } else { "" }
        Write-Host ("    后端健康        : {0} {1}" -f $(if ($h.ok) { "健康" } else { "不可用 -> " + $h.error }), $detail)
        if ($s.backend_active -eq 'wxui' -and -not $h.ok) {
            Write-Host "    ⚠ 文本后端是 wxui 但它不可用：文本发不出去（微信窗口没开/脚本缺文件）" -ForegroundColor Yellow
        }
    } catch { Write-Host "    解析 /senders 失败" -ForegroundColor Yellow }
} else { Write-Host "    bridge 面板未运行，检查 config.json 后启动 bridge" -ForegroundColor Yellow }

Write-Host "`n=== 3) 配置文件 ===" -ForegroundColor Cyan
$cfg = Join-Path $root "akasha-bridge\config.json"
if (Test-Path $cfg) {
    $c = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    $tokNote = if ([string]::IsNullOrWhiteSpace($c.access_token)) { "空白 ✗ —— 去 WeFlow「设置 → API 服务」复制 Access Token（或看 secrets\weflow-token.txt）" } else { "已填写" }
    Write-Host ("    access_token    : {0}" -f $tokNote)
    Write-Host ("    机器人昵称      : {0}" -f ($c.bot_nicknames -join ', '))
    Write-Host ("    AstrBot OB 地址 : {0}" -f $c.astrbot_ob_url)
    Write-Host ("    图片描述        : {0} / {1}" -f $c.image_caption_provider, $c.image_caption_model)
    if ($c.image_caption_provider -eq 'ollama' -and -not (Port-Open 11434)) {
        Write-Host "    ⚠ 图片理解当前不可用：Ollama(11434) 没在跑，收到图片只会退化成 [图片]" -ForegroundColor Yellow
    }
} else { Write-Host "    ✗ 缺少 $cfg" -ForegroundColor Red }

Write-Host "`n=== 3.5) AstrBot 插件目录可读性 ===" -ForegroundColor Cyan
# 2026-11-01 事故护栏：插件目录的 NTFS 权限一旦坏掉，AstrBot 读不到 requirements.txt / main.py，
# 面板上只显示「加载失败插件」，pip 抛的还是误导性的 error code 1。这里提前把哑火的目录拎出来。
$pluginDir = Join-Path $root 'astrbot\data\plugins'
if (Test-Path $pluginDir) {
    $badDirs = @()
    foreach ($d in (Get-ChildItem -LiteralPath $pluginDir -Directory -Force -ErrorAction SilentlyContinue)) {
        try { Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction Stop | Out-Null }
        catch { $badDirs += $d.Name }
    }
    if ($badDirs.Count) {
        Write-Host ("    ✗ 这些插件目录读不了（NTFS 权限坏了 → AstrBot 必然加载失败）：{0}" -f ($badDirs -join ', ')) -ForegroundColor Red
        Write-Host "      修法：管理员身份跑 scripts\_fix_plugin_acl.ps1 -LogPath logs\plugin-acl-fix.log（takeown + icacls /reset，只改权限不动文件）" -ForegroundColor Yellow
    } else {
        Write-Host ("    OK  {0} 个插件目录全部可读" -f @(Get-ChildItem -LiteralPath $pluginDir -Directory -Force -ErrorAction SilentlyContinue).Count)
    }
} else { Write-Host "    ✗ 缺少 $pluginDir" -ForegroundColor Red }

Write-Host "`n=== 3.6) bridge 的桌面输入权限（微信 UI 驱动）===" -ForegroundColor Cyan
# 2026-11-02 事故护栏：bridge 若是普通权限（Medium），SetCursorPos / SendInput 会静默失效，
# 每一次点击都落空，最后只表现为「发送 45s 超时」「未验证送达」「UI 探活 ratio=0.0」。
$bridgeListen = (& netstat.exe -ano | Select-String ':8766\s+.*LISTENING' | Select-Object -First 1)
if ($bridgeListen -and $bridgeListen.Line -match '(\d+)\s*$') {
    $bridgePid = $matches[1]
    $ilPy = Join-Path $root 'akasha-bridge\.venv\Scripts\python.exe'
    $ilReporter = Join-Path $PSScriptRoot '_il_report.py'
    if ((Test-Path $ilPy) -and (Test-Path $ilReporter)) {
        $ilOut = & $ilPy -X utf8 $ilReporter $bridgePid 2>&1 | Out-String
        if ($ilOut -match 'IL=(Medium|Low|open-err)') {
            Write-Host ("    ✗ bridge pid={0} 是普通权限（{1}）→ 点不动微信，发消息必然失败" -f $bridgePid, $matches[1].Trim()) -ForegroundColor Red
            Write-Host "      修法：scripts\_start_elevated_stack.ps1（弹一次 UAC，bridge + 看门狗都提到 System）" -ForegroundColor Yellow
            Write-Host "      背景：docs\WECHAT-INPUT-IL-20261102.md" -ForegroundColor DarkGray
        } else {
            Write-Host ("    OK  bridge pid={0} {1}（有桌面输入权限）" -f $bridgePid, $ilOut.Trim()) -ForegroundColor Green
        }
    } else {
        Write-Host ("    ?  bridge pid={0}，缺 _il_report.py 或 venv，跳过权限判定" -f $bridgePid) -ForegroundColor DarkGray
    }
} else {
    Write-Host "    · bridge 没在跑，权限无从判断（起来之后再看这一段）" -ForegroundColor DarkGray
}

Write-Host "`n=== 3.7) 微信进程（UI 驱动的前提）===" -ForegroundColor Cyan
$wxProcs = @(Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue)
if ($wxProcs.Count -eq 0) {
    Write-Host "    ✗ 微信没在跑（找不到 Weixin.exe）" -ForegroundColor Red
    Write-Host "      后果：wxui 会报 'WeChat window not found'，所有主动消息都超时进重发队列，而 AstrBot 审计仍然写 sent（ack 假象）" -ForegroundColor Yellow
    Write-Host "      修法：正常启动微信、点「进入微信」；再看 §3.6 确认 bridge 有桌面输入权限" -ForegroundColor Yellow
} else {
    $wxPids = ($wxProcs | Select-Object -First 4 -ExpandProperty Id) -join ','
    Write-Host ("    OK  Weixin.exe 进程 {0} 个（pid {1}）" -f $wxProcs.Count, $wxPids) -ForegroundColor Green
}

Write-Host "`n=== 3.7) 微信进程（UI 驱动的前提）===" -ForegroundColor Cyan
# 2026-11-02 20:38 事故护栏：微信进程整只消失后，wxui 报 WeChat window not found，
# AstrBot 侧却因为只拿到 OB11 ack 而把审计写成 sent —— 看起来「发了」，实际一条没送到。
$wxProcs = @(Get-Process -Name 'Weixin' -ErrorAction SilentlyContinue)
if ($wxProcs.Count -eq 0) {
    Write-Host "    ✗ 微信没在跑（找不到 Weixin.exe）" -ForegroundColor Red
    Write-Host "      后果：wxui 会报 'WeChat window not found'，所有主动消息都在 45s 后进重发队列，而审计仍写 sent" -ForegroundColor Yellow
    Write-Host "      修法：正常启动微信并点「进入微信」；再回看 §3.6 确认 bridge 有桌面输入权限" -ForegroundColor Yellow
} else {
    $wxPids = ($wxProcs | Select-Object -First 4 -ExpandProperty Id) -join ','
    Write-Host ("    OK  Weixin.exe 进程 {0} 个（pid {1}）" -f $wxProcs.Count, $wxPids) -ForegroundColor Green
}

Write-Host "`n=== 3.8) memory_companion 记忆库可写性 ===" -ForegroundColor Cyan
# 2026-11-02 事故护栏：AstrBot 卡住一个未提交的写事务后，插件写入全部报
# `unable to open database file`，记忆静默停止更新（只在 WARNING 里露一行）。
$memDb = Join-Path $root 'astrbot\data\plugin_data\astrbot_plugin_memory_companion\memory_companion.db'
$memPy = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
$memProbe = Join-Path $PSScriptRoot '_probe_dbwrite.py'
if (-not (Test-Path -LiteralPath $memDb)) {
    Write-Host "    · 记忆库还没生成（$memDb），跳过" -ForegroundColor DarkGray
} elseif ((Test-Path -LiteralPath $memPy) -and (Test-Path -LiteralPath $memProbe)) {
    $memOut = (& $memPy -X utf8 $memProbe $memDb 2 2>&1 | Out-String).Trim()
    if ($memOut -match '^OK') {
        Write-Host "    OK  写锁可获取（记忆能正常落库）" -ForegroundColor Green
    } else {
        Write-Host ("    ✗ 拿不到写锁 → 记忆写入会报 'unable to open database file'：{0}" -f $memOut) -ForegroundColor Red
        Write-Host "      修法：scripts\_restart_astrbot_only.ps1 重启 AstrBot 释放卡死的写事务" -ForegroundColor Yellow
    }
} else {
    Write-Host "    ? 缺 AstrBot venv 或 _probe_dbwrite.py，跳过" -ForegroundColor DarkGray
}

Write-Host "`n=== 4) 下一步建议 ===" -ForegroundColor Cyan
if (-not (Port-Open 5031)) { Write-Host "  · 启动 WeFlow：pwsh -File scripts\weflow-start.ps1（首次需在窗口里点「设置 → API 服务 → 启动服务」）" }
if (-not (Port-Open 11229)) { Write-Host "  · 启动 AstrBot：pwsh -File scripts\astrbot-start.ps1（或桌面「一键启动-微信AI服务.cmd」）" }
if (-not (Port-Open 8766)) { Write-Host "  · 启动 bridge：pwsh -File scripts\start-all.ps1（WMI 拉起，别用 Start-Process）" }
# 2026-11-02：普通权限读不到 System 进程的 CommandLine（取回来是空串），
# 以前按 CommandLine 匹配 _bridge_watchdog.py 会误报「看门狗没在跑」。改用心跳文件 + 进程兜底。
$wdHeartbeat = Join-Path $root 'logs\bridge-watchdog.heartbeat'
$wdAlive = $false
if (Test-Path -LiteralPath $wdHeartbeat) {
    $wdAge = ((Get-Date) - (Get-Item -LiteralPath $wdHeartbeat).LastWriteTime).TotalSeconds
    if ($wdAge -lt 180) { $wdAlive = $true }
}
if (-not $wdAlive) {
    if (@(Get-Process -Name pythonw -ErrorAction SilentlyContinue).Count -gt 0) { $wdAlive = $true }
}
if (-not $wdAlive) { Write-Host "  · 看门狗没在跑（8766 掉了不会自动拉回）：pwsh -File scripts\_start_bridge_watchdog.ps1" }
if ((Port-Open 8766) -and (Port-Open 11229) -and $wdAlive) { Write-Host "  · 链路齐了 → 在微信里给机器人发一条消息，确认被动回复和回读 localId" }
Write-Host "  · WeeMessenger(9877) 不启动是设计如此：文字/图片都走 scripts\wxui.py（UI 自动化）" -ForegroundColor DarkGray
Write-Host ""
