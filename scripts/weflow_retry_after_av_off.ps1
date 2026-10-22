# WeFlow -101 方案 1 复测：前提是火绒已退出（火绒自我保护导致管理员也停不掉它）
# 用法（普通权限即可）: pwsh -File scripts/weflow_retry_after_av_off.ps1
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logDir = Join-Path $root 'logs'
$result = Join-Path $logDir "weflow-retry-$stamp.result.txt"
$lines = New-Object System.Collections.Generic.List[string]
function Say($m) { $t = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Write-Output $t; $lines.Add($t) }

# 1) 火绒是否真的退出了
$hips = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'Hips|Huorong' })
$daemon = Get-Service HipsDaemon -ErrorAction SilentlyContinue
Say ("火绒进程: " + $(if ($hips.Count) { ($hips | ForEach-Object { "$($_.ProcessName)($($_.Id))" }) -join ', ' } else { '（无）' }))
Say ("HipsDaemon 服务: " + $(if ($daemon) { $daemon.Status } else { '（不存在）' }))
if ($hips.Count -gt 0 -or ($daemon -and $daemon.Status -eq 'Running')) {
  Say "!! 火绒还在运行，测试无意义：请右键托盘火绒图标 → 退出（或在火绒里把 WeFlow 加入信任区）后再跑本脚本"
  $lines | Set-Content $result -Encoding UTF8
  exit 2
}

# 2) 直接调 DLL（最快最干净的信号）
Say "--- 直接探针 wcdb_runtime_policy_version() ---"
$py = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
& $py -X utf8 (Join-Path $root 'scripts\probe_weflow_policy_version.py') 2>&1 | ForEach-Object { Say ("  " + $_) }
$probeExit = $LASTEXITCODE
Say "探针退出码: $probeExit（0 = 通过）"

# 3) 真链路：重启 WeFlow 看日志
Say "--- 重启 WeFlow 并观察 90 秒 ---"
$wcdb = Join-Path $env:APPDATA 'weflow\logs\wcdb.log'
Get-Process WeFlow -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$before = @(Get-Content $wcdb -ErrorAction SilentlyContinue).Count
Start-Process explorer.exe -ArgumentList "`"$env:ProgramFiles\WeFlow\WeFlow.exe`""
Start-Sleep -Seconds 90
$new = @(Get-Content $wcdb -ErrorAction SilentlyContinue | Select-Object -Skip $before)
$ok = @($new | Where-Object { $_ -match 'protection ok|policy ok' })
$bad = @($new | Where-Object { $_ -match 'policy mismatch value=-101' })
Say ("新增 {0} 行；成功 {1} 行；-101 {2} 行" -f $new.Count, $ok.Count, $bad.Count)
$new | Where-Object { $_ -notmatch 'koffi.load|initialize platform|setPaths|setLogEnabled' } | Select-Object -First 12 | ForEach-Object { Say ("  " + $_.Substring(0, [Math]::Min(190, $_.Length))) }

# 4) 顺带看 HTTP API 是否活了
try {
  $tok = (Select-String -Path (Join-Path $root 'secrets\weflow-token.txt') -Pattern '\S' | Select-Object -First 1).Line.Trim()
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5031/api/v1/sessions?limit=1' -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 15 -UseBasicParsing
  Say ("HTTP API: HTTP {0}（活了）" -f $r.StatusCode)
} catch { Say ("HTTP API: 仍然失败 - {0}" -f $_.Exception.Message) }

if ($probeExit -eq 0 -and $bad.Count -eq 0) { Say "结论: SUCCESS —— 关掉杀软后 WeFlow 初始化通过" }
elseif ($probeExit -eq 0) { Say "结论: 探针通过但 App 仍报错（另查 App 侧逻辑）" }
else { Say "结论: STILL_FAILING —— 关掉火绒仍被拒，杀软不是原因（继续方案 2/3）" }
$lines | Set-Content $result -Encoding UTF8
Say "结果文件: $result"
