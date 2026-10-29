@echo off
chcp 65001 >nul
cd /d "%~dp0.."
".\rvc-backend\.venv\Scripts\python.exe" -X utf8 "scripts\wechat_cleanup.py" --apply
echo.
pause
