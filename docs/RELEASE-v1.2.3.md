## 修复：Windows PowerShell 5.1 下「一键启动」起不来 AstrBot

`start-all.ps1` 解析错误已在 v1.2.2 修掉（13 个脚本补 UTF-8 BOM）。本轮补上真正的运行期问题。

### 现象
`powershell.exe -File scripts\astrbot-start.ps1` 启动后：
- 日志停在 `Loading plugin meme_manager ...`，6185 / 11229 永远不就绪
- 日志中文全乱码（`鎻掍欢鐩綍`、`涓€涓兘璁?`）
- stderr 里是 `NativeCommandError`

### 根因
1. 脚本尾部用 `& $exe run *>&1 | Tee-Object -FilePath $LogFile`。在 5.1 下，原生命令的 stderr 会被包装成 `NativeCommandError` 并中断整条管道，AstrBot 被半路掐死。
2. 控制台编码只在 `PSVersion -ge 6` 时才设 UTF-8；5.1 的控制台码页是 936，AstrBot 的 UTF-8 输出被按 GBK 解码再写回日志 → 双重乱码。

### 修法（`scripts/astrbot-start.ps1`）
- 编码：`try/catch` 里无条件设置 `[Console]::OutputEncoding = [Console]::InputEncoding = $OutputEncoding = UTF8`（5.1 / 7 都生效）。
- 启动：非 `-NoTee` 分支改为
  ```powershell
  $errLog = $LogFile -replace '\.log$', '.err.log'
  $proc = Start-Process -FilePath $exe -ArgumentList @('run') -WorkingDirectory $astrbotRoot `
      -WindowStyle Hidden -RedirectStandardOutput $LogFile -RedirectStandardError $errLog -PassThru
  $proc.WaitForExit(); exit $proc.ExitCode
  ```
  不再经过 PowerShell 管道，stderr 单独落到 `.err.log`。

### 验证
- `[Parser]::ParseFile`（powershell.exe 5.1）→ parse OK
- 实跑 `powershell.exe -File scripts\astrbot-start.ps1` → 75 秒内 **6185 与 11229 都在听**
- 日志出现 `Successfully fetched metadata for 4150 LLMs`、`SVC 模型/音色列表已更新并成功保存，共 6`

> 已知小瑕疵：插件自身用 GBK 写 stdout 的部分日志仍会显示乱码（`MSST鍒嗙鍙傛暟Debug`），只影响日志可读性，不影响收发。
