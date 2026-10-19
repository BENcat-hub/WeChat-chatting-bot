@echo off
rem ============================================================
rem  wechat-ai-stack 一键停止（双击即可）
rem  默认不动微信、不动 WeFlow 窗口（避免误杀正在用的聊天窗口）；
rem  要一起停：stop-all.cmd -IncludeWeFlow -IncludeWeChat
rem ============================================================
setlocal
set "HERE=%~dp0"
set "PSEXE=powershell"
where pwsh >nul 2>nul && set "PSEXE=pwsh"

"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%stop-all.ps1" %*

echo.
echo === 停止流程结束 ===
pause
