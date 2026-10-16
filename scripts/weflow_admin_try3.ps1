#Requires -RunAsAdministrator
# WeFlow -101 方案 3：核查并临时停用 MuMu 虚拟化驱动 / 腾讯 ACE 反作弊驱动，再看 DLL 与 App 是否放行
# 结束后把停掉的东西恢复。
$ErrorActionPreference = 'Continue'
$root = '<REPO_ROOT>'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $root "logs\weflow-try3-$stamp.log"
Start-Transcript -Path $log -Force | Out-Null
function Say($m) { Write-Output ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) }

Say "=== 环境证据（只读）==="
try { Say ("SecureBoot: " + (Confirm-SecureBootUEFI -ErrorAction Stop)) } catch { Say "SecureBoot 查询失败: $($_.Exception.Message)" }
try { $dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard -ErrorAction Stop
  Say ("VBS status={0} SecurityServicesRunning={1} CodeIntegrityPolicyEnforced={2} VbsRunning={3}" -f $dg.VirtualizationBasedSecurityStatus, ($dg.SecurityServicesRunning -join ','), $dg.CodeIntegrityPolicyEnforcementStatus, $dg.VirtualizationBasedSecurityStatus) } catch { Say "DeviceGuard 查询失败: $($_.Exception.Message)" }
Say "--- bcdedit 关键项 ---"
$bcd = bcdedit /enum '{current}' 2>&1
$bcd | Select-String -Pattern 'hypervisorlaunchtype|vsmlaunchtype|testsigning|nointegritychecks|debug|vsm|vm ' | ForEach-Object { Say ("  " + $_.Line.Trim()) }
Say "--- 相关驱动/服务状态 ---"
foreach ($n in 'MuMuVMMDrv', 'ACE-ADVT', 'ACE-GAME', 'ACE-SSC-DRV64', 'MuMuRemoteService') {
  $s = sc.exe query $n 2>&1 | Select-String 'STATE'
  $q = sc.exe qc $n 2>&1 | Select-String 'START_TYPE'
  Say ("{0,-18} {1} | {2}" -f $n, (($s -join ' ') -replace '\s+', ' '), (($q -join ' ') -replace '\s+', ' '))
}

Say "=== 停用（MuMu 虚拟化驱动 + ACE 反作弊）==="
$stopped = @()
foreach ($n in 'MuMuRemoteService', 'MuMuRemoteBackend') {
  try { Stop-Service $n -Force -ErrorAction Stop; Say "Stop-Service $n 成功"; $stopped += $n } catch { Say "Stop-Service $n 失败: $($_.Exception.Message)" }
}
foreach ($n in 'MuMuVMMDrv', 'ACE-ADVT', 'ACE-GAME', 'ACE-SSC-DRV64') {
  $r = sc.exe stop $n 2>&1
  Say ("sc stop {0}: {1}" -f $n, (($r | Select-Object -First 2) -join ' | '))
  $st = (sc.exe query $n 2>&1 | Select-String 'STATE') -replace '\s+', ' '
  Say ("  现在: {0}" -f $st)
  if ("$st" -match 'STOPPED') { $stopped += $n }
}
Start-Sleep -Seconds 3
Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'MuMu|ACE|AntiCheat' } | ForEach-Object { Say "残留进程: $($_.ProcessName)($($_.Id))" }

Say "=== 直接探针 ==="
$py = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
& $py -X utf8 (Join-Path $root 'scripts\probe_weflow_policy_version.py') 2>&1 | ForEach-Object { Say ("  " + $_) }
$probe = $LASTEXITCODE
Say "探针退出码: $probe"

Say "=== 重启 WeFlow 观察 60 秒 ==="
$wcdb = Join-Path $env:APPDATA 'weflow\logs\wcdb.log'
Get-Process WeFlow -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
$before = @(Get-Content $wcdb -ErrorAction SilentlyContinue).Count
Start-Process explorer.exe -ArgumentList "`"$env:ProgramFiles\WeFlow\WeFlow.exe`""
Start-Sleep -Seconds 60
$new = @(Get-Content $wcdb -ErrorAction SilentlyContinue | Select-Object -Skip $before)
$ok = @($new | Where-Object { $_ -match 'protection ok|policy ok' })
$bad = @($new | Where-Object { $_ -match 'policy mismatch value=-101' })
Say ("新增 {0} 行 | 成功 {1} | -101 {2}" -f $new.Count, $ok.Count, $bad.Count)

Say "=== 恢复 ==="
foreach ($n in 'MuMuVMMDrv', 'ACE-ADVT', 'ACE-GAME', 'ACE-SSC-DRV64') { if ($stopped -contains $n) { $r = sc.exe start $n 2>&1; Say ("sc start {0}: {1}" -f $n, (($r | Select-Object -First 1) -join ' ')) } }
foreach ($n in 'MuMuRemoteService') { try { Start-Service $n -ErrorAction Stop; Say "Start-Service $n 成功" } catch { Say "Start-Service $n 失败: $($_.Exception.Message)" } }

$verdict = if ($probe -eq 0 -and $bad.Count -eq 0) { 'SUCCESS: 停 MuMu/ACE 后通过' } elseif ($probe -eq 0) { 'PROBE_OK_APP_FAIL' } else { 'STILL_FAILING' }
Say "结论: $verdict"
"$verdict" | Set-Content (Join-Path $root "logs\weflow-try3-$stamp.result.txt") -Encoding UTF8
Say "结果: $log"
Stop-Transcript | Out-Null
