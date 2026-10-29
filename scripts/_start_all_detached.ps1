# 以「脱离当前 shell 进程树」的方式启动整套服务。
# 原因：直接用 Start-Process 拉起的子进程挂在 DSH 工具 shell 下面，
# 工具 job 结束 / 会话重启时会被连带杀掉（2026-10-24 的 bridge 就是这样掉的）。
# WMI 的 Win32_Process.Create 由 WmiPrvSE 拉进程，父进程不是我们，因此能活下来。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot          # ...\wechat-ai-stack
$script = Join-Path $PSScriptRoot 'start-all.ps1'
$log = Join-Path $root 'logs\start-all-detached.log'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $script)) { throw "找不到 $script" }
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null

$inner = 'cmd.exe /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" > "{2}" 2>&1"' -f $ps, $script, $log
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $inner
    CurrentDirectory = $root
}
if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create 失败 ReturnValue={0}" -f $r.ReturnValue) }
"已脱离启动 start-all.ps1  pid=$($r.ProcessId)"
"日志: $log"
