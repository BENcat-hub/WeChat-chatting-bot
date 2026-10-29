# 只重启 AstrBot（不动微信/WeFlow/bridge），并让进程脱离当前 shell 进程树。
# 用途：改完插件代码后热加载。WMI 建进程，父进程是 WmiPrvSE，DSH 工具 job 结束也不会被连带杀掉。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot          # ...\wechat-ai-stack
$script = Join-Path $PSScriptRoot 'start-all.ps1'
$log = Join-Path $root 'logs\start-astrbot-detached.log'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path $script)) { throw "找不到 $script" }
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null

$inner = 'cmd.exe /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -SkipWeChat -SkipWeFlow -SkipBridge -NoPreflight > "{2}" 2>&1"' -f $ps, $script, $log
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = $inner
    CurrentDirectory = $root
}
if ($r.ReturnValue -ne 0) { throw ("Win32_Process.Create 失败 ReturnValue={0}" -f $r.ReturnValue) }
"已脱离启动 start-all.ps1（仅 AstrBot） pid=$($r.ProcessId)"
"日志: $log"
