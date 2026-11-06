# 以 High 完整性跑一条命令（微信是 High，Medium 进程点不动它）。
# 用法：powershell -File _run_elevated.ps1 -Py "C:\...\python.exe" -Args "scripts\wxui.py","session" -Log "logs\run_high.log"
param(
    [Parameter(Mandatory = $true)][string]$Py,
    [string[]]$Args = @(),
    [Parameter(Mandatory = $true)][string]$Log,
    [string]$WorkDir = ""
)

Continue = 'Stop'

function Test-High {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-High)) {
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $PSCommandPath,
        '-Py', $Py, '-Log', $Log)
    if ($WorkDir) { $argList += @('-WorkDir', $WorkDir) }
    if ($Args.Count -gt 0) {
        $argList += '-Args'
        $argList += ($Args -join '||')
    }
    Start-Process powershell -ArgumentList $argList -Verb RunAs | Out-Null
    Write-Host "  -> elevated process started"
    return
}

if ($Args.Count -eq 1 -and $Args[0] -like '*||*') { $Args = $Args[0] -split '\|\|' }
$dir = if ($WorkDir) { $WorkDir } else { (Get-Location).Path }
$errLog = $Log + ".err.txt"
# rotate previous run: Start-Process redirects are overwrite-only
foreach ($f in @($Log, $errLog)) {
    if (Test-Path $f) {
        $prev = $f + ".1"
        if (Test-Path $prev) { Remove-Item $prev -Force -ErrorAction SilentlyContinue }
        Move-Item $f $prev -Force -ErrorAction SilentlyContinue
    }
}
Push-Location $dir
$argLine = ($Args | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
try {
    $proc = Start-Process -FilePath $Py -ArgumentList $argLine -WorkingDirectory $dir -RedirectStandardOutput $Log -RedirectStandardError $errLog -WindowStyle Hidden -PassThru -Wait
    $code = $proc.ExitCode
} finally {
    Pop-Location
}
Write-Host ("exit=" + $code)
