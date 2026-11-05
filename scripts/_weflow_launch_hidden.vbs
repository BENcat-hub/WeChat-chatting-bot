' 无窗口拉起 WeFlow 5.0（自启动用）。
' 原来 HKCU\...\Run 直接指向 start-weflow50.cmd，开机/登录时会闪一个黑 cmd 窗口；
' 用 wscript + WScript.Shell.Run(..., 0, False) 把窗口完全藏掉。
Option Explicit

Dim sh, root, cmd
Set sh = CreateObject("WScript.Shell")
root = "C:\Users\Administrator.DESKTOP-LD9CS9H\Documents\deepseek-harness\default-workspace\wechat-ai-stack"
sh.CurrentDirectory = root
cmd = "cmd.exe /c """ & root & "\scripts\start-weflow50.cmd"""
sh.Run cmd, 0, False
