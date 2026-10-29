@echo off
chcp 65001 >nul
schtasks /create /tn dsh-restart-stack /tr "cmd.exe /c ""<REPO_ROOT>\scripts\restart-stack-task.cmd""" /sc once /st 23:59 /ru "DESKTOP-LD9CS9H\Administrator" /rl HIGHEST /it /f >> "<REPO_ROOT>\logs\task-run.log" 2>&1
echo CREATE_EXIT=%ERRORLEVEL% >> "<REPO_ROOT>\logs\task-run.log"
schtasks /run /tn dsh-restart-stack >> "<REPO_ROOT>\logs\task-run.log" 2>&1
echo RUN_EXIT=%ERRORLEVEL% >> "<REPO_ROOT>\logs\task-run.log"
