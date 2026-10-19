@echo off
rem ============================================================
rem  wechat-ai-stack 一键启动（双击这个文件即可）
rem
rem  依次拉起：
rem    1. 微信 Weixin.exe（已开则跳过；需要人工扫码登录）
rem    2. WeFlow 5.0.0（读微信数据库 + SSE 推送，端口 5031）
rem       —— 走 start-weflow50.cmd，DLL 已打两处补丁，不要开 6.3.2
rem    3. AstrBot（AI 大脑：面板 6185 / OneBot 反向 WS 11229）
rem    4. 即梦生图本地服务（火山签名 → OpenAI 兼容，端口 8790）
rem    5. akasha-bridge（中间桥接，面板 8766）
rem
rem  参数（可选，直接跟在本行后面，例如 start-all.cmd -SkipWeChat）：
rem    -SkipWeChat / -SkipWeFlow / -SkipAstrBot / -SkipBridge / -NoPreflight
rem  停止：双击 stop-all.cmd
rem  完整说明：同目录 start-all.ps1 顶部注释
rem ============================================================
setlocal
set "HERE=%~dp0"
set "PSEXE=powershell"
where pwsh >nul 2>nul && set "PSEXE=pwsh"

"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%start-all.ps1" %*

echo.
echo === 启动流程结束（端口一览见上）===
pause
