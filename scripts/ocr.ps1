# Windows OCR (Windows.Media.Ocr) over an image file -> JSON lines with boxes.
# Why: WeChat's Qt window exposes no UI-automation children, so the only robust
# way to find a session-list row by NAME (instead of a fragile index that shifts
# whenever a new message reorders the list) is to read the screenshot. Windows'
# built-in OCR handles Chinese when the zh-Hans OCR language is installed.
#
# Run with Windows PowerShell 5.1 (powershell.exe), NOT pwsh: PowerShell 7 cannot
# project WinRT types without the CsWinRT module.
param(
  [Parameter(Mandatory=$true)][string]$Image,
  [string]$Language = "zh-Hans-CN"
)
$ErrorActionPreference = "Stop"

[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Media.Ocr,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null

# WinRT IAsyncOperation projects .Status/.GetResults() under PS 5.1; poll instead
# of using AsTask (which needs reflection that differs between hosts).
function Wait-Op($op) {
  while ($op.Status -eq 0) { Start-Sleep -Milliseconds 30 }   # 0 = Started
  if ($op.Status -ne 1) { throw "WinRT async failed: status $($op.Status)" }  # 1 = Completed
  $op.GetResults()
}

$available = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag })
$engine = $null
try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $Language)) } catch { }
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) {
  Write-Output (ConvertTo-Json -Compress @{ ok = $false; error = "no OCR engine"; available = $available })
  exit 1
}

$path = (Resolve-Path -LiteralPath $Image).Path
$file = Wait-Op ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path))
$stream = Wait-Op ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read))
$decoder = Wait-Op ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream))
$bitmap = Wait-Op ($decoder.GetSoftwareBitmapAsync())
$result = Wait-Op ($engine.RecognizeAsync($bitmap))

$lines = @()
foreach ($line in $result.Lines) {
  $words = @()
  foreach ($w in $line.Words) {
    $words += [pscustomobject]@{ t = $w.Text; x = [int]$w.BoundingRect.X; y = [int]$w.BoundingRect.Y;
                                 w = [int]$w.BoundingRect.Width; h = [int]$w.BoundingRect.Height }
  }
  $lines += [pscustomobject]@{
    text = $line.Text
    x = ($words | Measure-Object x -Minimum).Minimum
    y = ($words | Measure-Object y -Minimum).Minimum
    words = $words
  }
}
ConvertTo-Json -Compress -Depth 6 @{ ok = $true; engine = $engine.RecognizerLanguage.LanguageTag; available = $available; lines = $lines }
