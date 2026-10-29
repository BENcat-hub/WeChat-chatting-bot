@echo off
chcp 65001 >nul
setlocal
set ROOT=<REPO_ROOT>
cd /d "%ROOT%"

echo ================================================================
echo  Restart WeChat AI stack with YOUR normal user token
echo  ROOT = %ROOT%
echo ================================================================
if not exist "%ROOT%\scripts\start-all.ps1" (
  echo [FATAL] stack folder not found: %ROOT%
  pause
  exit /b 1
)
> "%ROOT%\logs\wmcp\user_restart_stamp.txt" echo %DATE% %TIME% started by %USERNAME%

echo.
echo [1/3] probe input capability ...
"%ROOT%\rvc-backend\.venv\Scripts\python.exe" -X utf8 "%ROOT%\scripts\_probe_cursor.py" user >nul 2>&1
if exist "%ROOT%\logs\wmcp\cursor_probe_user.json" (echo   probe OK) else (echo   probe FAILED)

echo.
echo [2/3] stopping current services ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop-all.ps1"

echo.
echo [3/3] starting all services ... about 60 seconds
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start-all.ps1"

echo.
echo ----------------------------------------------------------------
if exist "%ROOT%\logs\wmcp\cursor_probe_user.json" (
  powershell -NoProfile -Command "$j=Get-Content '%ROOT%\logs\wmcp\cursor_probe_user.json' -Raw | ConvertFrom-Json; 'set_cursor_pos.ret = ' + $j.set_cursor_pos.ret + '   cursor: ' + ($j.cursor_before -join ',') + ' -> ' + ($j.cursor_after -join ',')"
) else (
  echo   cursor_probe_user.json NOT created
)
echo ----------------------------------------------------------------
echo  DONE. Press any key to close.
pause
endlocal