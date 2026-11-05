@echo off
rem ============================================================
rem  WeChat AI stack - one-click launcher (just double click)
rem
rem  This file is ASCII ONLY on purpose: cmd.exe breaks on a
rem  UTF-8 BOM and on GBK-encoded Chinese. All Chinese user-facing
rem  text is printed by scripts\start-all.ps1 (UTF-8 BOM, parsed
rem  correctly by Windows PowerShell 5.1).
rem
rem  What start-all.ps1 does now:
rem    0) power-loss check + AstrBot plugin-dir readability gate
rem       (a hard power cut corrupts NTFS ACLs; 2026-11-01 incident)
rem    1) WeChat (needs manual QR login)
rem    2) WeFlow 5.0            :5031
rem    3) AstrBot               :6185 / OneBot WS :11229
rem    +  jimeng image server   :8790
rem    +  AI cover backend      :3333   (so-vits-svc + Demucs, harvest)
rem    +  Nahida voice server   :8795   (edge-tts -> resident svc, 2~3s)
rem    4) akasha-bridge         :8766   (launched via WMI, detached)
rem    +  service watchdog      (restarts any dead local port:
rem                              11229 / 8766 / 8790 / 8795 / 3333)
rem    +  preflight + port summary
rem
rem  Restart everything: double click restart-wechat-stack.cmd
rem  Stop:               run scripts\stop-all.ps1
rem ============================================================
title WeChat AI stack - start all
setlocal
chcp 65001 >nul
set "STACK=C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack"

if not exist "%STACK%\scripts\start-all.ps1" (
  echo [ERROR] start-all.ps1 not found under "%STACK%\scripts"
  pause
  exit /b 1
)
if not exist "%STACK%\logs" mkdir "%STACK%\logs"

powershell -NoProfile -ExecutionPolicy Bypass -File "%STACK%\scripts\start-all.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
echo === start-all finished (exit=%RC%) - services keep running in background ===
echo === press any key to close this window ===
pause >nul
endlocal