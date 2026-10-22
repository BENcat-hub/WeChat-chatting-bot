# 盯主动消息是否真的落到微信：记录 bridge 的发送结果 + 插件排期状态变化
#
# 背景：bridge 是「先回 OK 再发送」，失败只在 bridge.log 里留痕；插件侧记 status=sent。
# 所以判断「有没有真的发出去」必须看 bridge.log 的 [wxui] 行 + companions.json 的排期。
#
# 用法：pwsh -File scripts\watch_proactive_delivery.ps1 -Minutes 45
# 输出：logs\proactive-watch.log（追加写）

param([int]$Minutes = 45)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$out = Join-Path $root 'logs\proactive-watch.log'
$bridgeLog = Join-Path $root 'akasha-bridge\bridge.log'
$companions = Join-Path $root 'astrbot\data\plugin_data\astrbot_plugin_private_companion\companions.json'
$peekPy = Join-Path $root 'akasha-bridge\.venv\Scripts\python.exe'
$peekScript = Join-Path $PSScriptRoot 'companion_proactive_peek.py'
$env:PYTHONIOENCODING = 'utf-8'

$pattern = '\[wxui\]|未验证送达|文字已发送|图片已发送'
$seen = @(Select-String -Path $bridgeLog -Pattern $pattern -Encoding UTF8).Count
$lastMtime = (Get-Item $companions).LastWriteTime

"=== watch start $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')（已忽略历史 $seen 行）===" |
    Add-Content -Path $out -Encoding UTF8

$deadline = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 20

    $hits = @(Select-String -Path $bridgeLog -Pattern $pattern -Encoding UTF8)
    if ($hits.Count -gt $seen) {
        $hits | Select-Object -Skip $seen | ForEach-Object {
            ('[{0}] BRIDGE {1}' -f (Get-Date -Format 'HH:mm:ss'), $_.Line) | Add-Content -Path $out -Encoding UTF8
        }
        $seen = $hits.Count
    }

    $m = (Get-Item $companions).LastWriteTime
    if ($m -ne $lastMtime) {
        $lastMtime = $m
        $line = & $peekPy $peekScript --brief 2>&1
        ('[{0}] STATE  {1}' -f (Get-Date -Format 'HH:mm:ss'), ($line -join ' ')) | Add-Content -Path $out -Encoding UTF8
    }
}

"=== watch end $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Add-Content -Path $out -Encoding UTF8
