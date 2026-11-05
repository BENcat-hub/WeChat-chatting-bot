@echo off
rem ============================================================
rem  Start WeFlow 5.0.0 from its source tree (port 5031).
rem
rem  Why this way:
rem   * The 5.0 tree lives at wechat-ai-stack\weflow (recovered from the
rem     2026-10-29 share package). node_modules is installed locally.
rem   * Do NOT start "C:\Program Files\WeFlow\WeFlow.exe" (6.3.2): it
rem     shares %APPDATA%\weflow with 5.0 and its components are expired.
rem   * --no-sandbox is required on this machine, otherwise the Electron
rem     sandbox helpers fail and the app exits with code 3.
rem   * ELECTRON_RUN_AS_NODE must be UNSET, otherwise electron runs as
rem     plain node and crashes on the v8 snapshot assert.
rem
rem  ASCII ONLY: cmd.exe breaks on BOM and on GBK Chinese.
rem ============================================================
setlocal
rem Idempotency guard: exit quietly when the app is already listening on 5031,
rem so logon autostart (wscript -> this script) never spawns a second instance.
netstat -ano -p TCP | findstr /r /c:"LISTENING" | findstr /c:":5031 " >nul 2>&1
if not errorlevel 1 (
  echo [weflow50] already listening on 5031, nothing to do
  exit /b 0
)
set "ROOT=C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack\weflow"
set "ELECTRON=%ROOT%\node_modules\electron\dist\electron.exe"
set "FFMPEG=%ROOT%\node_modules\ffmpeg-static\ffmpeg.exe"

if not exist "%ELECTRON%" (
  echo [weflow50] electron missing: %ELECTRON%
  echo [weflow50] run: cd /d "%ROOT%" ^&^& npm ci --ignore-scripts
  exit /b 1
)
if not exist "%FFMPEG%" (
  echo [weflow50] ffmpeg-static binary missing, copying from the installed 6.3.2
  copy /y "C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe" "%FFMPEG%" >nul
)

rem Electron refuses to run as node when this is set (even to empty string).
set ELECTRON_RUN_AS_NODE=
set "TEMP=%ROOT%\..\.tmp-install"
set "TMP=%TEMP%"

cd /d "%ROOT%"
start "" "%ELECTRON%" . --no-sandbox
echo [weflow50] launched, HTTP API will listen on 127.0.0.1:5031
exit /b 0
