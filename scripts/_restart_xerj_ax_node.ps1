<#
  重启 xerj ax 节点（10-31 交接文档 §5 点名要的那个脚本，11-03 误删时丢了）。

  背景 / 实测（2026-11-04）：
  - 这个版本的 xerj 是「裸服务器」：`xerj [OPTIONS]` 直接起，没有 `node start` 子命令。
  - 默认监听 native REST 127.0.0.1:8080、gRPC 127.0.0.1:8081（不是文档里写的 9200）。
  - `--data-dir ~/.xerj-code/data`（10-31 那次建的 295MB 库）现在起不来：
      Error: initialise engine / IO error: 拒绝访问。 (os error 5)
    换一个全新 data-dir 立刻正常 => 是那个旧库本身坏了/被占，不是 xerj 的问题。
    所以本脚本默认先试旧库，失败就自动改用 `-Fresh` 指定的新目录，并把旧库原地保留（不删）。

  用法：
    pwsh -File scripts\_restart_xerj_ax_node.ps1            # 旧库优先，坏了自动换新库
    pwsh -File scripts\_restart_xerj_ax_node.ps1 -FreshOnly # 直接用新库
    pwsh -File scripts\_restart_xerj_ax_node.ps1 -Port 9200 # 换 REST 端口

  注意：PS 5.1 必须带 UTF-8 BOM 才不把中文当乱码解析（本文件已带）。
#>
param(
    [string]$DataDir = (Join-Path $env:USERPROFILE ".xerj-code\data"),
    [string]$FreshDir = (Join-Path $env:USERPROFILE ".xerj-code\data2"),
    [int]$Port = 8080,
    [switch]$FreshOnly
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$outLog = Join-Path $logDir "xerj-ax-node.log"
$errLog = Join-Path $logDir "xerj-ax-node.err.log"

function Find-Xerj {
    $candidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\xerj.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\xerj.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command xerj -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "找不到 xerj.exe（试过 ~/.local/bin、~/.cargo/bin、PATH）"
}

function Stop-Xerj {
    Get-Process -Name xerj -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  停掉旧 xerj pid=$($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

function Test-Rest([int]$p) {
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -TimeoutSec 2 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-Node([string]$dir, [int]$p) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $exe = Find-Xerj
    Write-Host "  启动 xerj：$exe  data-dir=$dir  rest=127.0.0.1:$p"
    Start-Process -FilePath $exe `
        -ArgumentList @('--insecure', '--data-dir', $dir, '--bind', '127.0.0.1', '--port', "$p") `
        -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
    return (Test-Rest $p)
}

Write-Host "== 重启 xerj ax 节点 ==" -ForegroundColor Cyan
Stop-Xerj

$ok = $false
if (-not $FreshOnly) {
    $ok = Start-Node $DataDir $Port
    if (-not $ok) {
        $tail = if (Test-Path $errLog) { (Get-Content $errLog -Tail 6) -join ' | ' } else { '' }
        Write-Host "  旧库起不来（$tail）-> 换新库 $FreshDir" -ForegroundColor Yellow
        Stop-Xerj
    }
}
if (-not $ok) {
    if ((Test-Path $DataDir) -and -not $FreshOnly) {
        Write-Host "  旧库原地保留（不删）：$DataDir" -ForegroundColor Yellow
    }
    $ok = Start-Node $FreshDir $Port
}

if ($ok) {
    Write-Host "  OK  REST http://127.0.0.1:$Port  (gRPC 通常是 $($Port + 1))" -ForegroundColor Green
    Write-Host "  日志：$outLog"
} else {
    Write-Host "  失败：REST 没起来，看 $errLog" -ForegroundColor Red
    if (Test-Path $errLog) { Get-Content $errLog -Tail 12 }
    exit 1
}
