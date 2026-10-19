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

Write-Host "`n=== 4) 下一步建议 ===" -ForegroundColor Cyan
if (-not (Port-Open 5031)) { Write-Host "  · 启动 WeFlow：pwsh -File scripts\weflow-start.ps1（首次需在窗口里点「设置 → API 服务 → 启动服务」）" }
if (-not (Port-Open 11229)) { Write-Host "  · 启动 AstrBot：pwsh -File scripts\astrbot-start.ps1" }
if (-not (Port-Open 9877)) { Write-Host "  · 启动 WeeMessenger：pwsh -File scripts\start-all.ps1（或看 logs\weemessenger-*.log）" }
if (-not (Port-Open 8766)) { Write-Host "  · 启动 bridge：pwsh -File scripts\start-all.ps1" }
Write-Host ""
