# 以脱离当前 shell 进程树的方式重启 akasha-bridge（WMI 拉起，父进程是 WmiPrvSE）。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\_restart_bridge_detached.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$inner = Join-Path $PSScriptRoot '_restart_bridge.ps1'
$log = Join-Path $root 'logs\bridge-restart-detached.log'
$cmd = 'cmd.exe /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" > "{2}" 2>&1"' -f $ps, $inner, $log
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $cmd
    CurrentDirectory = $root
}
if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create 失败 ReturnValue={0}" -f $r.ReturnValue) }
"已在独立进程树重启 bridge  launcher_pid=$($r.ProcessId)"
"日志: $log"
