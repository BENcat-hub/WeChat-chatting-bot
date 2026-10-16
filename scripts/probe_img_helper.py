"""直接调用 WeFlow 的 img_helper.dll，取回 InitImgHelper 的真实错误信息。

WeFlow 的 ImageDownloadService（electron/services/imageDownloadService.ts:47-56）
用 koffi 加载 resources\\image\\win32\\x64\\img_helper.dll 并调用：
    bool InitImgHelper(uint32 pid, const char* whitelist)
    void UninstallImgHelper()
    const char* GetImgHelperError()
它把 img_helper 注入 Weixin.exe，让白名单会话收到的图片自动下载高清版。
本脚本用同样的调用序列复现一次，把错误字符串打出来（这是 WeFlow 只在 console 里丢掉的诊断）。

用法：
  python probe_img_helper.py            # 只看默认微信主进程
  python probe_img_helper.py --pid N
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys

DLL_PATH = os.path.join(
    r"<WEFLOW_ROOT>",
    "resources",
    "image",
    "win32",
    "x64",
    "img_helper.dll",
)


def find_main_wechat_pid() -> int | None:
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'Weixin.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not out:
        return None
    procs = json.loads(out)
    if not isinstance(procs, list):
        procs = [procs]
    cands = [
        p
        for p in procs
        if p.get("CommandLine") and "weixin.exe" in p["CommandLine"].lower()
    ]
    cands.sort(key=lambda p: len(p["CommandLine"]))
    return cands[0]["ProcessId"] if cands else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--whitelist", default="", help="以 \\0 分隔的会话列表")
    parser.add_argument("--no-whitelist", action="store_true", help="不传白名单（空指针）")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="注入成功后不调用 UninstallImgHelper（用于观察注入是否真的进了 Weixin.exe）",
    )
    args = parser.parse_args()

    if not os.path.isfile(DLL_PATH):
        print(f"!! 找不到 DLL: {DLL_PATH}")
        return 1
    pid = args.pid or find_main_wechat_pid()
    print(f"DLL = {DLL_PATH}")
    print(f"目标 Weixin.exe PID = {pid}")
    if not pid:
        return 1

    lib = ctypes.CDLL(DLL_PATH)
    lib.InitImgHelper.argtypes = [ctypes.c_uint32, ctypes.c_char_p]
    lib.InitImgHelper.restype = ctypes.c_bool
    lib.UninstallImgHelper.restype = None
    lib.GetImgHelperError.restype = ctypes.c_char_p

    payload = None
    if not args.no_whitelist:
        items = [x for x in args.whitelist.split("|") if x]
        if items:
            payload = ("\0".join(items) + "\0\0").encode("utf-8")
    print(f"白名单字节数 = {len(payload) if payload else 0}")
    try:
        ok = bool(lib.InitImgHelper(pid, payload))
    except Exception as exc:  # noqa: BLE001
        print(f"!! 调用崩溃: {exc!r}")
        return 1
    raw = lib.GetImgHelperError()
    err = raw.decode("utf-8", "replace") if raw else ""
    print(f"InitImgHelper -> {ok}  GetImgHelperError -> {err!r}")
    if ok:
        if args.keep:
            print("注入成功；--keep 指定，保留 hook（不调用 UninstallImgHelper）")
        else:
            print("注入成功，调用 UninstallImgHelper 复位")
            try:
                lib.UninstallImgHelper()
            except Exception:  # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
