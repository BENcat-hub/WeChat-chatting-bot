# 以 High 完整性跑一条命令（微信是 High，Medium 进程点不动它）。
# 用法：powershell -File _run_elevated.ps1 -Py "C:\...\python.exe" -Args "scripts\wxui.py","session" -Log "logs\run_high.log"
param(
    [Parameter(Mandatory = $true)][string]$Py,
    [string[]]$Args = @(),
    [Parameter(Mandatory = $true)][string]$Log,
    [string]$WorkDir = ""
)

$ErrorActionPreference = 'Stop'

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
    Write-Host "  → 已交给提权进程处理"
    return
}

if ($Args.Count -eq 1 -and $Args[0] -like '*||*') { $Args = $Args[0] -split '\|\|' }
$dir = if ($WorkDir) { $WorkDir } else { (Get-Location).Path }
Push-Location $dir
try {
    & $Py @Args *> $Log
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Host "exit=$code log=$Log"
