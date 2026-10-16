#Requires -RunAsAdministrator
# WeFlow -101: 管理员权限下的“方案 1”验证（杀软白名单 / 临时停火绒 → 重启 WeFlow 看日志）
# 结果写到 logs/weflow-admin-test-<stamp>.log
$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outDir = '<REPO_ROOT>\logs'
$log = Join-Path $outDir "weflow-admin-test-$stamp.log"
Start-Transcript -Path $log -Force | Out-Null

function Say($m) { Write-Output ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

Say "elevated=$(whoami) IsAdmin=$((New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"

# --- 1) Defender 实时防护 + 排除项 ---
Say "--- Defender ---"
try { Add-MpPreference -ExclusionPath 'C:\Program Files\WeFlow', "$env:APPDATA\weflow", "$env:LOCALAPPDATA\WeFlow" -ErrorAction Stop; Say "排除项已添加" } catch { Say "Add-MpPreference 失败: $($_.Exception.Message)" }
try { (Get-MpPreference).ExclusionPath | ForEach-Object { Say "exclusion: $_" } } catch { Say "读排除项失败: $($_.Exception.Message)" }
try { Set-MpPreference -DisableRealtimeMonitoring $true -ErrorAction Stop; Say "Defender 实时防护已临时关闭" } catch { Say "关闭 Defender 实时防护失败: $($_.Exception.Message)" }

# --- 2) 火绒：先记录，再尝试停 ---
Say "--- 火绒（停之前） ---"
Get-Service HipsDaemon -ErrorAction SilentlyContinue | ForEach-Object { Say ("svc {0} = {1}" -f $_.Name, $_.Status) }
foreach ($d in 'sysdiag', 'hrwfpdrv', 'hrdevmon') { $s = (sc.exe query $d 2>&1 | Select-String 'STATE'); Say ("driver {0}: {1}" -f $d, ($s -join ' ')) }
$huorongWasRunning = [bool](Get-Service HipsDaemon -ErrorAction SilentlyContinue | Where-Object Status -eq 'Running')

foreach ($svc in 'HipsDaemon') {
  try { Stop-Service $svc -Force -ErrorAction Stop; Say "Stop-Service $svc 成功" } catch { Say "Stop-Service $svc 失败: $($_.Exception.Message)" }
}
foreach ($p in 'HipsTray', 'HipsMain', 'HipsDaemon', 'HipsFw') {
  Get-Process $p -ErrorAction SilentlyContinue | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Say "已结束进程 $p($($_.Id))" } catch { Say "结束 $p 失败: $($_.Exception.Message)" } }
}
foreach ($d in 'sysdiag', 'hrwfpdrv') { $r = sc.exe stop $d 2>&1; Say ("sc stop {0}: {1}" -f $d, (($r | Select-Object -First 2) -join ' | ')) }
Start-Sleep -Seconds 3
Get-Service HipsDaemon -ErrorAction SilentlyContinue | ForEach-Object { Say ("svc {0} = {1}（停之后）" -f $_.Name, $_.Status) }
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'Hips|Huorong|sysdiag' } | ForEach-Object { Say "残留进程: $($_.ProcessName)($($_.Id))" }

# --- 3) 重启 WeFlow 并观察 90 秒 ---
Say "--- 重启 WeFlow ---"
Get-Process WeFlow -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$wcdb = Join-Path $env:APPDATA 'weflow\logs\wcdb.log'
$before = (Get-Content $wcdb -ErrorAction SilentlyContinue).Count
Start-Process explorer.exe -ArgumentList "`"$env:ProgramFiles\WeFlow\WeFlow.exe`""
Start-Sleep -Seconds 90
$new = Get-Content $wcdb -ErrorAction SilentlyContinue | Select-Object -Skip $before
Say ("新增日志行数: {0}" -f @($new).Count)
$ok = @($new | Where-Object { $_ -match 'protection ok|policy ok|InitProtection rc=0' })
$bad = @($new | Where-Object { $_ -match 'policy mismatch value=-101|finalCode=-101' })
Say ("命中 protection ok / 成功: {0} 行" -f $ok.Count)
Say ("命中 -101: {0} 行" -f $bad.Count)
$new | Where-Object { $_ -notmatch 'koffi.load|initialize platform' } | Select-Object -First 15 | ForEach-Object { Say ("  " + $_.Substring(0, [Math]::Min(190, $_.Length))) }
"--- 结论 ---" | Out-File -FilePath (Join-Path $outDir "weflow-admin-test-$stamp.result.txt") -Encoding UTF8
if ($ok.Count -gt 0 -and $bad.Count -eq 0) { "SUCCESS: 停火绒后 WeFlow 初始化通过" | Out-File -FilePath (Join-Path $outDir "weflow-admin-test-$stamp.result.txt") -Encoding UTF8 -Append }
else { "STILL_FAILING: 停火绒后仍然 -101" | Out-File -FilePath (Join-Path $outDir "weflow-admin-test-$stamp.result.txt") -Encoding UTF8 -Append }

# --- 4) 恢复：把火绒拉起来（失败也算了，会告诉用户手动开） ---
if ($huorongWasRunning) {
  Say "--- 恢复火绒 ---"
  try { Start-Service HipsDaemon -ErrorAction Stop; Say "HipsDaemon 已重启" } catch { Say "启动 HipsDaemon 失败: $($_.Exception.Message)" }
  $tray = 'C:\Program Files\Huorong\Sysdiag\bin\HipsTray.exe'
  if (Test-Path $tray) { Start-Process $tray; Say "已拉起 HipsTray" } else { Say "未找到 HipsTray.exe" }
  Start-Sleep -Seconds 3
  Get-Service HipsDaemon -ErrorAction SilentlyContinue | ForEach-Object { Say ("svc {0} = {1}（恢复后）" -f $_.Name, $_.Status) }
}
Say "log: $log"
Stop-Transcript | Out-Null
