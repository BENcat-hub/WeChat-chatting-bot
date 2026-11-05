# 把服务看门狗拉成独立进程树（父进程 WmiPrvSE），不挂在调用者下面。
# 看门狗本体：scripts\_bridge_watchdog.py —— 盯 11229 / 8766 / 8790 / 8795 / 3333，
# 哪个端口不在听就把那个服务拉回来（不再只盯 bridge）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\_start_bridge_watchdog.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'akasha-bridge\.venv\Scripts\pythonw.exe'   # pythonw：永不分配控制台
if (-not (Test-Path $py)) { $py = Join-Path $root 'astrbot\.venv\Scripts\pythonw.exe' }
if (-not (Test-Path $py)) { throw "找不到 pythonw.exe" }
$script = Join-Path $PSScriptRoot '_bridge_watchdog.py'
if (-not (Test-Path $script)) { throw "找不到 $script" }
$log = Join-Path $root 'logs\bridge-watchdog.log'

$exist = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match '_bridge_watchdog\.py' }
if ($exist) {
    "看门狗已在运行 pid=$($exist[0].ProcessId)"
    exit 0
}

$cmd = '"{0}" -X utf8 "{1}"' -f $py, $script
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $cmd
    CurrentDirectory = $root
}
if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create 失败 ReturnValue={0}" -f $r.ReturnValue) }
"已启动看门狗 pid=$($r.ProcessId)（监控 11229/8766/8790/8795/3333）"
"日志: $log"
