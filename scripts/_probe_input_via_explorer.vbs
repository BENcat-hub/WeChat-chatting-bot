' 桌面输入能力探针：由 explorer.exe 拉起时，进程会落在**当前交互式 WinSta0**，
' 用它来验证「通过资源管理器启动的进程能移动光标」（本会话里的进程可能是旧登录会话的残留站）。
Option Explicit

Dim fso, f, py, root, logPath, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
root = "C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack"
logPath = root & "\logs\explorer-probe.txt"
py = root & "\_restore-20261103\probe_cur.py"

cmd = "cmd.exe /c """ & root & "\akasha-bridge\.venv\Scripts\python.exe"" -X utf8 """ & py & """ > """ & logPath & """ 2>&1"
CreateObject("WScript.Shell").Run cmd, 0, False
