@echo off
cd /d "%~dp0.."
"%~dp0..\rvc-backend\.venv\Scripts\python.exe" -X utf8 "%~dp0_probe_cursor.py" usertask > "%~dp0..\logs\wmcp\probe_task_stdout.txt" 2>&1
