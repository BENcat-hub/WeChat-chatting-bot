@echo off
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
".\rvc-backend\.venv\Scripts\python.exe" -X utf8 ".\scripts\_probe_input_detached.py" detached > ".\logs\wmcp\input_probe_detached.out" 2>&1
