# WeFlow -101 diagnosis, attempt 4 (elevated):
#   1) stop the two driver groups the user asked for (MuMu virtualization driver + Tencent ACE anti-cheat drivers) and probe
#   2) test the community hypothesis from hicccc77/WeFlow issue #1227 ("确实是时间的问题") by sweeping the system clock back
#   3) restore drivers and system time, always
$ErrorActionPreference = 'Continue'
$root = 'C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack'
$ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $root "logs\weflow-admin-try4-$ts.log"
Start-Transcript -Path $log -Force | Out-Null

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Output "=== admin=$isAdmin"
Write-Output "=== now: $(Get-Date -Format o)"

$py = Join-Path $root 'astrbot\.venv\Scripts\python.exe'
$probePath = Join-Path $env:TEMP 'wf_probe_clock.py'
$code = @'
import ctypes, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
d = r"C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64"
for extra in ("WCDB.dll", "SDL2.dll"):
    p = os.path.join(d, extra)
    if os.path.exists(p):
        try:
            ctypes.CDLL(p)
        except Exception as e:
            print("preload", extra, "fail", e)
os.add_dll_directory(d)
lib = ctypes.CDLL(os.path.join(d, "wcdb_api.dll"))
f = lib.wcdb_runtime_policy_version
f.restype = ctypes.c_int32
v = f()
print("probe=%d hex=%s expected=%d" % (v, hex(v & 0xffffffff), 1464206081))
'@
$code | Set-Content $probePath -Encoding UTF8

function Probe([string]$label) {
  $out = (& $py -X utf8 $probePath 2>&1 | Out-String).Trim() -replace "`r?`n", " | "
  Write-Output ("[{0}] at {1} -> {2}" -f $label, (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $out)
}

$services = @('MuMuVMMDrv', 'ACE-ADVT', 'ACE-GAME', 'ACE-SSC-DRV64')
Write-Output "=== MuMu-related services present:"
Write-Output ((Get-Service | Where-Object { $_.Name -match 'MuMu|ACE' } | ForEach-Object { "$($_.Name)=$($_.Status)" }) -join ', ')
Write-Output "=== states BEFORE"
foreach ($s in $services) {
  $q = (sc.exe query $s 2>&1 | Select-String 'STATE') -join ' '
  Write-Output ("-- {0}: {1}" -f $s, $q)
}

Write-Output "=== baseline probe (drivers running)"
Probe 'baseline'

Write-Output "=== stopping drivers"
foreach ($s in $services) {
  $r = (sc.exe stop $s 2>&1) -join ' '
  Write-Output ("-- stop {0}: {1}" -f $s, $r)
}
Start-Sleep -Seconds 3
foreach ($s in $services) {
  $q = (sc.exe query $s 2>&1 | Select-String 'STATE') -join ' '
  Write-Output ("-- after stop {0}: {1}" -f $s, $q)
}

Write-Output "=== probe with drivers stopped"
Probe 'drivers-stopped'

Write-Output "=== clock sweep (community hint: -101 is time-based)"
$saved = Get-Date
foreach ($days in @(1, 2, 3, 5, 10)) {
  $target = $saved.AddDays(-$days)
  Set-Date -Date $target | Out-Null
  Write-Output ("-- clock set to {0} (offset -{1}d)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $days)
  Probe ("clock-{0}d" -f $days)
}
Set-Date -Date $saved | Out-Null
Write-Output ("=== clock restored to {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
Probe 'clock-restored'

Write-Output "=== restarting drivers"
foreach ($s in $services) {
  $r = (sc.exe start $s 2>&1) -join ' '
  Write-Output ("-- start {0}: {1}" -f $s, $r)
}
Start-Sleep -Seconds 2
foreach ($s in $services) {
  $q = (sc.exe query $s 2>&1 | Select-String 'STATE') -join ' '
  Write-Output ("-- final {0}: {1}" -f $s, $q)
}

Stop-Transcript | Out-Null
Write-Output "LOG=$log"
