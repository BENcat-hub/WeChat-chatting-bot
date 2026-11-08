@echo off
rem ============================================================
rem  Start WeFlow 5.0 (source tree, port 5031).
rem
rem  All logic lives in _start_weflow50.ps1 now, because cmd.exe
rem  cannot reliably UNSET ELECTRON_RUN_AS_NODE: "set VAR=" leaves
rem  it defined as an empty string, and electron treats that as
rem  "run as node" -> the app parses itself as JS and exits with
rem  no window. That was why this shortcut looked dead.
rem
rem  This file stays ASCII only (cmd.exe breaks on BOM / GBK).
rem ============================================================
setlocal
netstat -ano -p TCP | findstr /r /c:"LISTENING" | findstr /c:":5031 " >nul 2>&1
if not errorlevel 1 (
  echo [weflow50] already listening on 5031, nothing to do
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0_start_weflow50.ps1" -WaitSeconds 40
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo [weflow50] failed with code %RC%, see logs\weflow-5.0.err.log
)
exit /b %RC%
