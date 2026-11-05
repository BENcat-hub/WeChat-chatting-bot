@echo off
rem ============================================================
rem  Install / refresh the two desktop launchers.
rem  Double click once. They are byte-copied from scripts\desktop\.
rem  ASCII ONLY.
rem ============================================================
chcp 65001 >nul
set "SRC=%~dp0"
set "DST=%USERPROFILE%\Desktop"
for %%F in ("%SRC%一键启动-微信AI服务.cmd" "%SRC%restart-wechat-stack.cmd") do (
  copy /y "%%~fF" "%DST%\%%~nxF" >nul && echo [ok] %%~nxF && attrib +a "%DST%\%%~nxF" >nul
)
echo.
echo Done. Desktop launchers refreshed.
pause
