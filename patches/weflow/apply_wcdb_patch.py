#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 WeFlow v5.0.0 的 wcdb_api.dll 打两处 4 字节补丁，解除「构建日 + 15 天」硬时限。

背景（详见 patches/weflow/README.md）：
  未打补丁时，wcdb_api.dll 会拒绝初始化，HTTP API 返回 500 并在日志里留下
    InitProtection rc=-101      （时间闸门：超过构建日 + 15 天）
    wcdb_init rc=-1000          （mktime 年份上限）
  两处各改 4 字节后，可用期延长到 2037-09-30。

用法（只依赖标准库）：
    python apply_wcdb_patch.py --check  <wcdb_api.dll 或 WeFlow 树根>
    python apply_wcdb_patch.py --apply  <wcdb_api.dll 或 WeFlow 树根>
    python apply_wcdb_patch.py --revert <wcdb_api.dll 或 WeFlow 树根>

退出码：0 = 成功；2 = 校验不通过（文件尺寸/字节不匹配）；3 = 参数或路径错误。
"""

import argparse
import hashlib
import os
import shutil
import sys

# ---------------------------------------------------------------- 补丁定义

DLL_REL = os.path.join("resources", "wcdb", "win32", "x64", "wcdb_api.dll")
PRISTINE_SUFFIX = ".pristine"

EXPECTED_SIZE = 1_387_008
EXPECTED_SHA256 = None  # 未知则不校验整体哈希，只校验补丁点的字节

# (偏移, 原始字节, 打补丁后字节, 说明)
PATCHES = [
    (
        0x80DC1,
        bytes.fromhex("7FA2BD6A"),  # 0x6ABDA27F = 2026-09-30 23:59:59 UTC
        bytes.fromhex("FF8C6D7F"),  #      2137967999 = 2037-09-30 23:59:59 UTC
        "InitProtection 的「构建日 + 15 天」硬时限常量（修 -101）",
    ),
    (
        0xE858D,
        bytes.fromhex("7E000000"),  # mktime 的 tm_year = 126 -> 2026
        bytes.fromhex("89000000"),  #               tm_year = 137 -> 2037
        "wcdb_init 内部 mktime 的年份（修 -1000）",
    ),
]

# ---------------------------------------------------------------- 工具


def resolve_dll(path: str) -> str:
    """允许传 DLL 本身或 WeFlow 树根目录。"""
    p = os.path.abspath(path)
    if os.path.isdir(p):
        cand = os.path.join(p, DLL_REL)
        if os.path.isfile(cand):
            return cand
        # 兼容 6.x 打包结构 resources/resources/...
        cand2 = os.path.join(p, "resources", "resources", "wcdb", "win32", "x64", "wcdb_api.dll")
        if os.path.isfile(cand2):
            return cand2
        raise SystemExit(f"[x] 在 {p} 下找不到 {DLL_REL}")
    if os.path.isfile(p):
        return p
    raise SystemExit(f"[x] 路径不存在：{p}")


def read_bytes(dll: str) -> bytearray:
    with open(dll, "rb") as f:
        return bytearray(f.read())


def site_state(data: bytes, offset: int, old: bytes, new: bytes) -> str:
    cur = bytes(data[offset:offset + len(old)])
    if cur == old:
        return "original"
    if cur == new:
        return "patched"
    return "unknown(0x%s)" % cur.hex().upper()


def peek_policy_version(data: bytes) -> str:
    """粗略看一眼 wcdb_runtime_policy_version 的返回值（仅用于提示，非权威）。"""
    return ""


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def report(dll: str, data: bytes) -> bool:
    print(f"[i] 目标文件 : {dll}")
    print(f"[i] 文件大小 : {len(data):,} 字节 (期望 {EXPECTED_SIZE:,})")
    print(f"[i] sha256   : {sha256_of(dll)}")
    size_ok = len(data) == EXPECTED_SIZE
    if not size_ok:
        print("[!] 尺寸与预期的 v5.0.0 DLL 不一致——**不要**盲目打补丁，请先确认版本。")
    all_patched = True
    for offset, old, new, desc in PATCHES:
        st = site_state(data, offset, old, new)
        mark = "✓ 已补丁" if st == "patched" else ("  未补丁" if st == "original" else "✗ 异常 " + st)
        print(f"    {mark}  偏移 0x{offset:X}  {desc}")
        if st != "patched":
            all_patched = False
    print("[=] 结论：%s" % ("两处补丁均已就位。" if all_patched else "存在未打或异常的补丁点。"))
    return all_patched and size_ok


def do_apply(dll: str) -> int:
    data = read_bytes(dll)
    if not report(dll, data):
        # 尺寸或运行时不符时不静默继续
        if len(data) != EXPECTED_SIZE:
            return 2
        if all(site_state(data, o, old, new) == "unknown" for o, old, new, _ in PATCHES):
            return 2

    pristine = dll + PRISTINE_SUFFIX
    if not os.path.isfile(pristine):
        shutil.copy2(dll, pristine)
        print(f"[+] 已备份原始 DLL -> {pristine}")
    else:
        print(f"[i] 备份已存在，保留：{pristine}")

    changed = 0
    for offset, old, new, desc in PATCHES:
        cur = bytes(data[offset:offset + len(old)])
        if cur == new:
            print(f"[=] 0x{offset:X} 已经是补丁后的值，跳过")
            continue
        if cur != old:
            print(f"[x] 0x{offset:X} 字节不符（实际 0x{cur.hex().upper()}，期望 0x{old.hex().upper()}）——中止")
            return 2
        data[offset:offset + len(new)] = new
        changed += 1
        print(f"[+] 0x{offset:X} 0x{old.hex().upper()} -> 0x{new.hex().upper()}  {desc}")

    if changed:
        tmp = dll + ".patched.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dll)
        print(f"[+] 已写入 {dll}（{changed} 处）")

    report(dll, read_bytes(dll))
    print("[i] 还原方式：把 %s 覆盖回 %s，或运行 --revert" % (os.path.basename(pristine), os.path.basename(dll)))
    return 0


def do_revert(dll: str) -> int:
    pristine = dll + PRISTINE_SUFFIX
    if not os.path.isfile(pristine):
        print(f"[x] 找不到备份 {pristine}，无法还原（请从原始安装包取回 DLL）")
        return 2
    shutil.copy2(pristine, dll)
    print(f"[+] 已从 {pristine} 还原 {dll}")
    report(dll, read_bytes(dll))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="WeFlow v5.0.0 wcdb_api.dll 二进制补丁工具")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="只检查补丁状态")
    g.add_argument("--apply", action="store_true", help="应用补丁（自动备份 .pristine）")
    g.add_argument("--revert", action="store_true", help="从 .pristine 还原")
    ap.add_argument("path", help="wcdb_api.dll 路径，或 WeFlow 树根目录")
    args = ap.parse_args()

    dll = resolve_dll(args.path)
    if args.check:
        ok = report(dll, read_bytes(dll))
        return 0 if ok else 2
    if args.apply:
        return do_apply(dll)
    return do_revert(dll)


if __name__ == "__main__":
    sys.exit(main())
