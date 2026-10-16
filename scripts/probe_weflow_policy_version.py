"""直接调 wcdb_api.dll 的 wcdb_runtime_policy_version()：-101 = 本机被环境闸门拒绝。
用法: python scripts/probe_weflow_policy_version.py  （退出码 0 = 通过，1 = 被拒）
"""
import ctypes
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

D = Path(r"C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64")
EXPECTED = 1464206081  # 0x57440001

os.add_dll_directory(str(D))
for pre in ("WCDB.dll", "SDL2.dll"):
    try:
        ctypes.WinDLL(str(D / pre))
    except OSError as exc:  # noqa: BLE001
        print(f"预加载 {pre} 失败: {exc}")

api = ctypes.WinDLL(str(D / "wcdb_api.dll"))
fn = api.wcdb_runtime_policy_version
fn.restype = ctypes.c_int
value = fn()
print(f"wcdb_runtime_policy_version() = {value}   (期望 {EXPECTED} = 0x57440001；-101 = 被拒)")
sys.exit(0 if value == EXPECTED else 1)
