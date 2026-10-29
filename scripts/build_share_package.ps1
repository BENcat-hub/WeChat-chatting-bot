<#
.SYNOPSIS
    把 wechat-ai-stack 打包成「可分享」的发布树（默认给朋友分享 / 私有仓库用）。

.DESCRIPTION
    做三件事：
      1. 按白名单复制四个组件（WeFlow / akasha-bridge / astrbot 插件 / scripts 自研服务）。
      2. 排除可再生的巨型目录（.venv / node_modules / __pycache__）、运行期数据
         （logs / plugin_data / 队列图片 / *.db）与所有密钥（secrets、config.json 里的 key）。
      3. 生成配置模板（*.example）、NOTICE.md、THIRD-PARTY.md、.gitignore。

    不复制任何聊天记录、记忆库、人设私有内容；密钥一律换成占位符。

.PARAMETER OutBase
    输出根目录，默认 <workspace>\wechat-ai-stack-share；发布树落在该目录下的
    wechat-ai-stack\ 子目录，zip 也在 OutBase 下。

.PARAMETER IncludeVendor
    额外把 WeFlow 的 node_modules（约 813 MB，含 224 MB 的 electron.exe）也复制进去。
    注意：单文件 >100 MB 会被 GitHub 直接拒收，只在用网盘分享时才建议开。

.PARAMETER Zip
    打包完成后用 tar.exe 生成 wechat-ai-stack-share.zip。

.PARAMETER KeepExisting
    不先清空已有的输出目录（默认清空，保证可重复执行）。

.EXAMPLE
    pwsh -File .\scripts\build_share_package.ps1 -Zip
#>
[CmdletBinding()]
param(
    [string]$OutBase,
    [switch]$IncludeVendor,
    [switch]$Zip,
    [switch]$KeepExisting
)

$ErrorActionPreference = 'Stop'

$StackRoot = Split-Path -Parent $PSScriptRoot
$WorkspaceRoot = Split-Path -Parent $StackRoot
$WeFlowSrc = '<WEFLOW_ROOT>'
if (-not $OutBase) { $OutBase = Join-Path $WorkspaceRoot 'wechat-ai-stack-share' }
$Repo = Join-Path $OutBase 'wechat-ai-stack'

function Write-Step([string]$msg) { Write-Host ("[pack] " + $msg) -ForegroundColor Cyan }

function Copy-Tree {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Dest,
        [string[]]$ExcludeDirs = @(),
        [string[]]$ExcludeFiles = @()
    )
    if (-not (Test-Path $Source)) { Write-Warning "跳过（不存在）: $Source"; return }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $args = @($Source, $Dest, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
    if ($ExcludeDirs.Count)  { $args += '/XD'; $args += $ExcludeDirs }
    if ($ExcludeFiles.Count) { $args += '/XF'; $args += $ExcludeFiles }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy 失败（exit=$LASTEXITCODE）: $Source -> $Dest" }
    $global:LASTEXITCODE = 0
}

# ---------------------------------------------------------------- 0. 准备输出目录
if ((Test-Path $OutBase) -and (-not $KeepExisting)) {
    Write-Step "清空输出目录 $OutBase"
    Remove-Item -Recurse -Force $OutBase
}
New-Item -ItemType Directory -Force -Path $Repo | Out-Null
foreach ($d in 'docs', 'deploy', 'patches\weflow', 'tests') {
    New-Item -ItemType Directory -Force -Path (Join-Path $Repo $d) | Out-Null
}

# ---------------------------------------------------------------- 1. 自研服务（scripts）
Write-Step '复制 scripts\（自研 UIA 发送 / OCR / 即梦图片服务 / 启动与探测脚本）'
# `_` 开头的是本机一次性诊断/探针脚本（_probe_*.py / _speedcfg*.py / _add_*），
# 只服务于开发机排障，不进发布包。
Copy-Tree -Source (Join-Path $StackRoot 'scripts') -Dest (Join-Path $Repo 'scripts') `
    -ExcludeDirs '__pycache__' -ExcludeFiles '*.pyc', 'session-id-map.json', '_*.py'

# ---------------------------------------------------------------- 2. Akasha 桥接
Write-Step '复制 akasha-bridge\（桥接主进程、面板、发送后端；去掉 venv/日志/队列图片/真实配置）'
Copy-Tree -Source (Join-Path $StackRoot 'akasha-bridge') -Dest (Join-Path $Repo 'akasha-bridge') `
    -ExcludeDirs '.venv', '__pycache__', 'wxui_queue_assets' `
    -ExcludeFiles 'config.json', 'bridge.log', 'bridge.pid', 'wxui_pending.jsonl', 'ob_id_map.json', '*.pyc'

# ---------------------------------------------------------------- 3. AstrBot 插件（自研主体）
Write-Step '复制 astrbot\data\plugins\ 下 6 个插件源码（去掉 __pycache__ 与运行期 data）'
$pluginsDst = Join-Path $Repo 'astrbot\data\plugins'
New-Item -ItemType Directory -Force -Path $pluginsDst | Out-Null
foreach ($p in 'astrbot_plugin_private_companion', 'astrbot_plugin_memory_companion',
               'astrbot_plugin_image_companion', 'astrbot_plugin_proactive_chat',
               'astrbot_plugin_affection', 'meme_manager') {
    Copy-Tree -Source (Join-Path $StackRoot "astrbot\data\plugins\$p") -Dest (Join-Path $pluginsDst $p) `
        -ExcludeDirs '__pycache__', '.pytest_cache', 'temp' -ExcludeFiles '*.pyc'
}

# 自研的 t2i 模板与存储层测试（原本散在栈根）
Write-Step '复制栈根 data\t2i_templates 与 tests\'
Copy-Tree -Source (Join-Path $StackRoot 'data\t2i_templates') -Dest (Join-Path $Repo 'astrbot\data\t2i_templates')
Copy-Tree -Source (Join-Path $StackRoot 'tests') -Dest (Join-Path $Repo 'tests')

# ---------------------------------------------------------------- 4. 小信使（第三方，含源码）
Write-Step '复制 weemessenger\WeeMessenger-main\（第三方「小信使」；去掉 .venv）'
Copy-Tree -Source (Join-Path $StackRoot 'weemessenger\WeeMessenger-main') `
    -Dest (Join-Path $Repo 'weemessenger\WeeMessenger-main') `
    -ExcludeDirs '.venv', '__pycache__' -ExcludeFiles '*.pyc'

# ---------------------------------------------------------------- 5. WeFlow（Electron 应用整棵）
Write-Step '复制 WeFlow Electron 应用树（默认排除 node_modules）'
$wfExDirs = @('.git', '__pycache__')
$wfExFiles = @('*.pyc')
if (-not $IncludeVendor) { $wfExDirs += 'node_modules' }
Copy-Tree -Source $WeFlowSrc -Dest (Join-Path $Repo 'weflow') -ExcludeDirs $wfExDirs -ExcludeFiles $wfExFiles

# ---------------------------------------------------------------- 6. 文档
Write-Step '复制 docs\ 并清掉旧日志类附属物'
Copy-Tree -Source (Join-Path $StackRoot 'docs') -Dest (Join-Path $Repo 'docs')

# ---------------------------------------------------------------- 7. 密钥/配置模板
Write-Step '生成配置模板（密钥 -> 占位符）'
$secretKeyRx = '(?i)(api[_-]?key|apikey|secret|token|password|passwd|app[_-]?id|access[_-]?key|ak|sk|credential)'

function New-SanitizedJson {
    param([string]$Source, [string]$Dest, [string]$Placeholder = '<FILL_ME>')
    if (-not (Test-Path $Source)) { Write-Warning "缺少 $Source，跳过模板生成"; return }
    $obj = Get-Content $Source -Raw -Encoding UTF8 | ConvertFrom-Json

    function Clear-Secrets($node) {
        if ($null -eq $node) { return $null }
        if ($node -is [System.Collections.IDictionary]) {
            foreach ($k in @($node.Keys)) { $node[$k] = Clear-Secrets $node[$k] }
            return $node
        }
        if ($node -is [System.Management.Automation.PSCustomObject]) {
            foreach ($p in @($node.PSObject.Properties)) {
                if ($p.Name -match $secretKeyRx) {
                    if ($p.Value -is [bool]) { continue }
                    if ($null -ne $p.Value -and "$($p.Value)".Length -gt 0) { $p.Value = $Placeholder }
                } elseif ($p.Value -is [System.Management.Automation.PSCustomObject] -or $p.Value -is [System.Collections.IEnumerable] -and $p.Value -isnot [string]) {
                    $p.Value = Clear-Secrets $p.Value
                }
            }
            return $node
        }
        if ($node -is [System.Collections.IEnumerable] -and $node -isnot [string]) {
            $list = @()
            foreach ($item in $node) { $list += ,(Clear-Secrets $item) }
            return $list
        }
        return $node
    }

    $clean = Clear-Secrets $obj
    $clean | ConvertTo-Json -Depth 40 | Set-Content -Path $Dest -Encoding UTF8
}

New-SanitizedJson -Source (Join-Path $StackRoot 'akasha-bridge\config.json') `
                  -Dest   (Join-Path $Repo 'akasha-bridge\config.example.json')
New-SanitizedJson -Source (Join-Path $StackRoot 'astrbot\data\cmd_config.json') `
                  -Dest   (Join-Path $Repo 'astrbot\data\cmd_config.json.example')

# 环境变量/.env 模板（即梦、火山、WeFlow token 都只以占位符出现）
$secretsDir = Join-Path $Repo 'secrets'
New-Item -ItemType Directory -Force -Path $secretsDir | Out-Null
@(
    '# 复制成 .env 或按 README 放进环境变量；真实密钥不要提交。'
    'JIMENG_API_KEY=<FILL_ME>'
    'VOLCENGINE_AK=<FILL_ME>'
    'VOLCENGINE_SK=<FILL_ME>'
    'WEFLOW_TOKEN=<FILL_ME>'
    'DEEPSEEK_API_KEY=<FILL_ME>'
) -join "`r`n" | Set-Content (Join-Path $secretsDir '.env.example') -Encoding UTF8
@(
    'secrets\ 目录说明'
    '================='
    '真实密钥在本机是：jimeng_api_key.txt / volcengine_ak_sk.txt / weflow-token.txt /'
    'akasha-bridge\config.json / astrbot\data\cmd_config.json。'
    '分享包里只保留占位符模板，收到包的人需要自己填。'
) -join "`r`n" | Set-Content (Join-Path $secretsDir 'README.txt') -Encoding UTF8

# ---------------------------------------------------------------- 7.8 发布用模板（README/NOTICE/.gitignore/deploy/patches）
$tpl = Join-Path $StackRoot 'release-templates'
if (Test-Path $tpl) {
    Write-Step '复制 release-templates\（README / NOTICE / THIRD-PARTY / .gitignore / deploy / patches）'
    Get-ChildItem -LiteralPath $tpl -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Repo -Recurse -Force
    }
    # deploy 与 patches 需要在包内可执行；保留目录
    foreach ($d in 'deploy', 'patches') {
        $p = Join-Path $Repo $d
        if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    }
} else {
    Write-Step 'release-templates\ 不存在，跳过模板复制'
}

# ---------------------------------------------------------------- 7.5 脱敏（文本替换）
# 把本机的绝对路径、小号/联系人 wxid、数字 uid、联系人昵称、WeFlow token 全部换成占位符。
# 只处理自研部分；weflow\ 是第三方源码树，保持原样。
Write-Step '脱敏：绝对路径 / wxid / 数字 uid / 联系人昵称 / token -> 占位符'

$bodySlash  = '<REPO_ROOT>'
$bodyBs     = '<REPO_ROOT>'
$weflowSlash = '<WEFLOW_ROOT>'
$weflowBs    = '<WEFLOW_ROOT>'
$homeSlash  = 'C:/Users/<YOU>'
$homeBs     = 'C:\Users\<YOU>'

# 顺序有意义：先长后短，否则短前缀会先把长路径切断。
$scrubMap = [ordered]@{
    $bodyBs      = '<REPO_ROOT>'
    $bodySlash   = '<REPO_ROOT>'
    $weflowBs    = '<WEFLOW_ROOT>'
    $weflowSlash = '<WEFLOW_ROOT>'
    $homeBs      = 'C:\Users\<YOU>'
    $homeSlash   = 'C:/Users/<YOU>'
    # 开发机目录名（家目录替换后仍会残留的路径尾部）
    'Documents\<WORKSPACE>' = 'Documents\<WORKSPACE>'
    'Documents\<WORKSPACE>'                  = 'Documents\<WORKSPACE>'
    'Documents/<WORKSPACE>' = 'Documents/<WORKSPACE>'
    'Documents/<WORKSPACE>'                  = 'Documents/<WORKSPACE>'
    '<WEFLOW_SRC>'                      = '<WEFLOW_SRC>'
    '<WEFLOW_SRC>'                                  = '<WEFLOW_SRC>'
    # 微信账号标识
    'wxid_BOTACCOUNT'  = 'wxid_BOTACCOUNT'
    'wxid_CONTACT_A'  = 'wxid_CONTACT_A'
    'wxid_CONTACT_B'  = 'wxid_CONTACT_B'
    'wxid_CONTACT_C'  = 'wxid_CONTACT_C'
    'wxid_CONTACT_D'  = 'wxid_CONTACT_D'
    # OneBot 数字 uid（保留纯数字形态，脚本里 int/str 两种上下文都还能跑）
    '1000000001' = '1000000001'
    '1000000002' = '1000000002'
    '1000000003' = '1000000003'
    '1000000004' = '1000000004'
    '1000000005'  = '1000000005'
    '1000000006' = '1000000006'
    '1000000007' = '1000000007'
    '1000000008' = '1000000008'
    '1000000009' = '1000000009'
    '1000000010' = '1000000010'
    '1000000011'  = '1000000011'
    # 其余真实账号 / 群（来自 ob_id_map.json 的权威映射）
    '1000000012' = '1000000012'
    '1000000013' = '1000000013'
    '1000000015' = '1000000015'
    '1000000016' = '1000000016'
    '1000000017' = '1000000017'
    # 1057020421 = 「文件传输助手」，是微信内置对象，不视为隐私，保留原样
    # 联系人 / 群昵称
    '用户B'     = '用户B'
    # 2026-10-18 改名事故里出现过的备注名，与昵称同等视为隐私
    '用户B别名'         = '用户B别名'
    '用户A'         = '用户A'
    '用户C'       = '用户C'
    '用户D' = '用户D'
    '示例群聊'      = '示例群聊'
    '用户E'          = '用户E'
    '用户E'           = '用户E'
    '示例群聊B' = '示例群聊B'
    # 2026-10-29 补充：本轮 OCR/搜索修复涉及的联系人与群
    '用户F'           = '用户F'
    '用户F别名'         = '用户F别名'
    '用户G'           = '用户G'
    '用户G别名'           = '用户G别名'
    '用户H'            = '用户H'
    '用户H别名'         = '用户H别名'
    '用户I'         = '用户I'
    '用户J'         = '用户J'
    '示例群聊C'     = '示例群聊C'
    '用户K'          = '用户K'
    '示例群聊D'         = '示例群聊D'
    # 对应 wxid（硬性自检要求不残留裸 wxid）
    'wxid_CONTACT_E' = 'wxid_CONTACT_E'
    'wxid_CONTACT_F' = 'wxid_CONTACT_F'
    'wxid_CONTACT_G' = 'wxid_CONTACT_G'
    'wxid_CONTACT_H' = 'wxid_CONTACT_H'
    'wxid_CONTACT_I' = 'wxid_CONTACT_I'
    'wxid_CONTACT_J' = 'wxid_CONTACT_J'
    'wxid_CONTACT_K' = 'wxid_CONTACT_K'
    '12345678901@chatroom' = '12345678901@chatroom'
    '12345678902@chatroom' = '12345678902@chatroom'
    '12345678903@chatroom' = '12345678903@chatroom'
    # 真实密钥绝不写在这里：本脚本自己也会被复制进发布树并推到公开仓库，
    # 写在这里 = 明文泄露（2026-10-25 已真实发生过一次）。改为运行时读取，见下。
}

# ── 真实密钥：运行时从本地配置读取，脚本正文里不留任何明文 ──────────────────
# 事故记录：`sk-baf8…5efa`（DeepSeek）与 WeFlow 的 access_token 曾写死在上面的
# 清理表里，于是被「清理」逻辑原样复制进发布树，随仓库公开了。
$collectLocalSecrets = {
    $found = New-Object System.Collections.Generic.List[string]
    $sources = @(
        (Join-Path $StackRoot 'astrbot\data\cmd_config.json'),
        (Join-Path $StackRoot 'akasha-bridge\config.json')
    )
    foreach ($src in $sources) {
        if (-not (Test-Path $src)) { continue }
        try {
            $raw = Get-Content $src -Raw -Encoding UTF8
            foreach ($m in [regex]::Matches($raw, 'sk-[A-Za-z0-9]{20,}')) {
                if (-not $found.Contains($m.Value)) { $found.Add($m.Value) }
            }
            foreach ($m in [regex]::Matches($raw, '"access_token"\s*:\s*"([0-9a-f]{24,})"')) {
                $tok = $m.Groups[1].Value
                if ($tok -and -not $found.Contains($tok)) { $found.Add($tok) }
            }
        } catch {
            Write-Warning "读取本地密钥失败（跳过该文件）: $src"
        }
    }
    return $found
}
foreach ($secret in (& $collectLocalSecrets)) {
    $scrubMap[[string]$secret] = '<FILL_ME>'
    Write-Host "已加入清理表：$($secret.Substring(0, 6))…$($secret.Substring($secret.Length - 4))"
}

$textExt = '.py', '.md', '.json', '.ps1', '.txt', '.cmd', '.bat', '.js', '.ts', '.yaml', '.yml',
           '.html', '.css', '.toml', '.nsh', '.jsonl', '.ini', '.cfg', '.tsx', '.example'

# JSON 文件里的 Windows 路径是转义写法（C:\\Users\\...），单反斜杠的模式匹配不到，
# 所以每个含反斜杠的模式都要额外生成一个「双反斜杠」变体，且必须排在前面先替换。
$scrubPairs = New-Object System.Collections.Generic.List[object]
foreach ($kv in $scrubMap.GetEnumerator()) {
    $k = [string]$kv.Key
    if ($k.Contains('\')) { $scrubPairs.Add([pscustomobject]@{ Pattern = $k.Replace('\', '\\'); Replacement = $kv.Value }) }
    $scrubPairs.Add([pscustomobject]@{ Pattern = $k; Replacement = $kv.Value })
}

$targets = Get-ChildItem $Repo -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "$Repo\weflow\*" -and $_.Extension -in $textExt -and $_.Length -lt 16MB }
$scrub = @{}
foreach ($f in $targets) {
    $raw = [System.IO.File]::ReadAllText($f.FullName)
    $new = $raw
    foreach ($pair in $scrubPairs) {
        $k = $pair.Pattern
        if ($new.Contains($k)) {
            $n = ([regex]::Matches($new, [regex]::Escape($k))).Count
            $new = $new.Replace($k, $pair.Replacement)
            if (-not $scrub.ContainsKey($k)) { $scrub[$k] = 0 }
            $scrub[$k] += $n
        }
    }
    if ($new -ne $raw) { [System.IO.File]::WriteAllText($f.FullName, $new, (New-Object System.Text.UTF8Encoding($false))) }
}
foreach ($k in ($scrub.Keys | Sort-Object)) { Write-Host ("        {0,-8} <- {1}" -f $scrub[$k], $k) }

# ---------------------------------------------------------------- 8. 版本/依赖清单
Write-Step '写 requirements / 版本清单'
@'
# AstrBot 本体（第三方，通过 pip 安装，不随包分发源码）
astrbot==4.28.2
'@ | Set-Content (Join-Path $Repo 'astrbot\requirements.txt') -Encoding UTF8

@'
# akasha-bridge 依赖（Python 3.12）
# 与 akasha-bridge\requirements.txt 内容一致，这里再给一份便于单独安装。
requests>=2.31.0
pyautogui>=0.9.54
pyperclip>=1.8.2
pygetwindow>=0.0.9
uiautomation>=2.0.17
Pillow>=10.0.0
websockets>=12.0
'@ | Set-Content (Join-Path $Repo 'akasha-bridge\requirements.share.txt') -Encoding UTF8

# ---------------------------------------------------------------- 8. 自检：残留痕迹扫描
# 硬性条件用「本机身份」判断；其余（C:\Users\xxx 示例、10 位数字）只统计不报错，
# 因为上游插件文档里本来就有 C:\Users\99505 这类作者示例、以及大量时间戳数字。
Write-Step '自检：扫描发布树里的残留痕迹'
$hardPats  = @('Administrator\.DESKTOP-LD9CS9H', 'wxid_[a-z0-9]{8,}')
$softPats  = @('(?i)[A-Z]:[\\/]+Users[\\/]+(?!<YOU>)', '(?<!\d)1\d{9}(?!\d)')
$checkTargets = Get-ChildItem $Repo -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notlike "$Repo\weflow\*" -and $_.Extension -in $textExt -and $_.Length -lt 16MB }
$hardHits = 0; $softHits = 0; $softSample = @()
foreach ($f in $checkTargets) {
    $t = [System.IO.File]::ReadAllText($f.FullName)
    $rel = $f.FullName.Replace($Repo, '')
    foreach ($pat in $hardPats) {
        foreach ($mm in [regex]::Matches($t, $pat)) {
            $hardHits++
            Write-Host ("        [严重] {0}  @ {1}" -f $mm.Value, $rel) -ForegroundColor Red
        }
    }
    foreach ($pat in $softPats) {
        foreach ($mm in [regex]::Matches($t, $pat)) {
            if ($mm.Value -match '^10000000\d\d$') { continue }   # 我们自己的 uid 占位符
            $softHits++
            if ($softSample.Count -lt 5) { $softSample += ("{0} @ {1}" -f $mm.Value, $rel) }
        }
    }
}
if ($hardHits -eq 0) { Write-Host '        硬性自检通过：没有本机用户名 / 裸 wxid 残留' }
else { Write-Host ("        硬性自检失败：{0} 处（见上）" -f $hardHits) -ForegroundColor Red }
Write-Host ("        软性提示：{0} 处形如 C:\Users\xxx 或 10 位数字（其中绝大多数是上游文档示例与时间戳，请人工扫一眼）" -f $softHits)
foreach ($s in $softSample) { Write-Host ("                {0}" -f $s) }

# ---------------------------------------------------------------- 9. 打包结果统计
$files = Get-ChildItem $Repo -Recurse -File -Force -ErrorAction SilentlyContinue
$mb = [math]::Round(($files | Measure-Object -Sum Length).Sum / 1MB, 1)
Write-Step ("发布树: {0}（{1} 个文件, {2} MB）" -f $Repo, $files.Count, $mb)
if ($IncludeVendor) { Write-Host '[pack] 注意：已包含 node_modules，electron.exe 224MB > GitHub 100MB 单文件上限' -ForegroundColor Yellow }

if ($Zip) {
    $zipPath = Join-Path $OutBase 'wechat-ai-stack-share.zip'
    if (Test-Path $zipPath) { Remove-Item -Force $zipPath }
    Write-Step "压缩 -> $zipPath（tar.exe，约需 1-2 分钟）"
    Push-Location $OutBase
    try { & tar.exe -a -c -f $zipPath 'wechat-ai-stack' } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "tar 压缩失败（exit=$LASTEXITCODE）" }
    $zipMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
    Write-Step ("zip 完成: {0}（{1} MB）" -f $zipPath, $zipMb)
}
Write-Step 'DONE'
