@echo off
rem ============================================================
rem  Watchdog guard -- keeps _bridge_watchdog.py alive.
rem  Started from Startup\WeChatAI-BridgeWatchdog.lnk (zero args).
rem
rem  The watchdog itself restarts dead local services:
rem    11229 AstrBot OneBot WS / 8766 bridge / 8790 jimeng
rem    8795 nahida-tts / 3333 AI cover backend
rem  If the watchdog process itself dies, this loop brings it back
rem  within 5 minutes. The .ps1 side is idempotent: it exits early
rem  when a watchdog is already running.
rem
rem  ASCII ONLY: cmd.exe breaks on BOM and on GBK Chinese.
rem ============================================================
setlocal
set "ROOT=C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack"
set "PS1=%ROOT%\scripts\_start_bridge_watchdog.ps1"
if not exist "%PS1%" (
  echo [guard] missing: %PS1%
  exit /b 1
)

:loop
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" >nul 2>&1
timeout /t 300 /nobreak >nul
goto loop
