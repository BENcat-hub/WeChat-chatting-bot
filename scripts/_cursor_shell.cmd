@echo off
"<REPO_ROOT>\rvc-backend\.venv\Scripts\python.exe" -X utf8 "<REPO_ROOT>\scripts\_probe_cursor.py" shell > "<REPO_ROOT>\logs\wmcp\cursor_probe_shell.json" 2>&1
