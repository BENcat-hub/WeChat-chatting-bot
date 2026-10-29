@echo off
rem 2026-10-27 供计划任务（用户交互令牌）调用的重启脚本：与 restart-stack-user.cmd 相同，但不会 pause。
chcp 65001 >nul
set ROOT=<REPO_ROOT>
if not exist "%ROOT%\scripts\start-all.ps1" (
  echo [FATAL] stack root not found: %ROOT%
  exit /b 2
)
echo [1/3] probe cursor with current token ...
"%ROOT%\rvc-backend\.venv\Scripts\python.exe" -X utf8 "%ROOT%\scripts\_probe_cursor.py" usertask
echo [2/3] stop all ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop-all.ps1" -IncludeWeFlow
timeout /t 3 /nobreak >nul
echo [3/3] start all ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start-all.ps1"
echo done
