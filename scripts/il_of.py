# -*- coding: utf-8 -*-
"""列出指定进程的完整性级别（TokenIntegrityLevel + GetSidSubAuthority）。"""
import ctypes, subprocess, sys
from ctypes import wintypes as wt

k = ctypes.WinDLL("kernel32", use_last_error=True)
adv = ctypes.WinDLL("advapi32", use_last_error=True)

k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k.OpenProcess.restype = wt.HANDLE
k.CloseHandle.argtypes = [wt.HANDLE]
adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
adv.OpenProcessToken.restype = wt.BOOL
adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
adv.GetTokenInformation.restype = wt.BOOL
adv.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
adv.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
adv.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
adv.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD)

NAMES = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium", 0x2001: "Medium-Plus",
         0x3000: "High", 0x4000: "System", 0x5000: "Protected"}

def il(pid):
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return "cannot-open(err=%d)" % ctypes.get_last_error()
    try:
        tok = wt.HANDLE()
        if not adv.OpenProcessToken(h, 0x0008, ctypes.byref(tok)):
            return "no-token(err=%d)" % ctypes.get_last_error()
        size = wt.DWORD(0)
        adv.GetTokenInformation(tok, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
        buf = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(tok, 25, buf, size, ctypes.byref(size)):
            return "gettokinfo(err=%d)" % ctypes.get_last_error()
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]      # SID_AND_ATTRIBUTES.Sid
        cnt = adv.GetSidSubAuthorityCount(sid).contents.value
        rid = adv.GetSidSubAuthority(sid, cnt - 1).contents.value
        return "%s(0x%x)" % (NAMES.get(rid, "?"), rid)
    finally:
        k.CloseHandle(h)

def pids_of(name):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + name, "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    res = []
    for line in out.strip().splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            res.append(int(parts[1]))
    return res

def listen_pid(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0] == "TCP" and f[1].endswith(":" + str(port)) and f[3] == "LISTENING":
            return int(f[4])
    return None

targets = sys.argv[1:] or ["Weixin.exe", "WeFlow.exe", "DSH Desktop.exe", "python.exe", "pythonw.exe"]
print("%-18s %-8s %s" % ("image", "pid", "integrity"))
for t in targets:
    for p in pids_of(t):
        print("%-18s %-8d %s" % (t, p, il(p)))
for port, label in ((8766, "bridge:8766"), (11229, "astrbot:11229")):
    p = listen_pid(port)
    if p:
        print("%-18s %-8d %s" % (label, p, il(p)))
