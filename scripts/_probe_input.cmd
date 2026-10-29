@echo off
setlocal
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
set WHO=%~1
if "%WHO%"=="" set WHO=wrapped
".\rvc-backend\.venv\Scripts\python.exe" -X utf8 ".\scripts\_probe_input_detached.py" %WHO% > ".\logs\wmcp\input_probe_%WHO%.out" 2>&1
endlocal
