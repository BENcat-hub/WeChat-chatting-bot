# Ensure every *.ps1 under scripts\ that contains non-ASCII bytes starts with a UTF-8 BOM.
# Windows PowerShell 5.1 decodes a BOM-less script as GBK on this box, which turns Chinese
# comments/strings into mojibake and can break parsing outright (2026-10-29: 17 syntax errors
# after an edit dropped the BOM; 2026-11-01: preflight.ps1 did the same after a targeted edit).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\_ensure_ps1_bom.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dir = Join-Path $root 'scripts'
$bom = [byte[]](0xEF, 0xBB, 0xBF)
$fixed = 0
$ok = 0

foreach ($f in (Get-ChildItem -LiteralPath $dir -Filter '*.ps1' -File)) {
    $bytes = [System.IO.File]::ReadAllBytes($f.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $ok++; continue }
    $nonAscii = $false
    foreach ($b in $bytes) { if ($b -gt 0x7F) { $nonAscii = $true; break } }
    if (-not $nonAscii) { $ok++; continue }
    [System.IO.File]::WriteAllBytes($f.FullName, [byte[]]($bom + $bytes))
    "BOM added: $($f.Name)"
    $fixed++
}

"done: fixed=$fixed alreadyOk=$ok"
