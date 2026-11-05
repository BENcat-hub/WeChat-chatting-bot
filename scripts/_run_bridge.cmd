@echo off
rem Launched by WMI (Win32_Process.Create) so the parent is WmiPrvSE and the bridge
rem survives whatever shell asked for the restart. ASCII only on purpose: cmd.exe
rem garbles a UTF-8 BOM on line 1.
setlocal
set "ROOT=%~dp0.."
set "BRIDGE=%ROOT%\akasha-bridge"
cd /d "%BRIDGE%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%BRIDGE%\.venv\Scripts\python.exe" -X utf8 main.py >> "%ROOT%\logs\bridge-current.log" 2>&1
