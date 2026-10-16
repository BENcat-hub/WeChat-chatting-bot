#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微信 4.x 本地图片 .dat 解密 + 探测（只读源文件，输出到独立目录）。

背景：WeFlow 的 httpApi 只对"它已经导出过"的图片给 mediaUrl。图片是否有
mediaUrl 取决于 WeFlow 的 img_helper 钩子有没有截到微信的下载动作——实测
type=3 的普通图片经常没有（localId 152/231），而 type=47 表情基本都有。
但微信自己其实把图片落在本地了：
    ...\\xwechat_files\\wxid_*\\msg\\attach\\<hash>\\<yyyy-mm>\\Img\\<md5>.dat
所以可以绕过 WeFlow，自己把这些 .dat 解出来。

用法：
    python wechat_dat_decode.py --recent 10            # 列出最近 10 个 .dat 并尝试解密
    python wechat_dat_decode.py --file <path>          # 解密单个
    python wechat_dat_decode.py --recent 6 --out-dir <dir>
"""
import argparse
import os
import struct
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(r"C:\Users\<YOU>\Documents\xwechat_files")
DEFAULT_OUT = Path(r"<REPO_ROOT>\logs\dat-decode")

MAGICS = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG\r\n\x1a\n": ".png",
    b"GIF87a": ".gif",
    b"GIF89a": ".gif",
    b"BM": ".bmp",
    b"RIFF": ".webp",
    b"II*\x00": ".tif",
    b"MM\x00*": ".tif",
}


def sniff(payload: bytes) -> str:
    for magic, ext in MAGICS.items():
        if payload.startswith(magic):
            return ext
    return ""


def try_decode(data: bytes):
    """返回 (扩展名, 偏移, 密钥 xor 字节, 解码后的 bytes) 或 None。

    微信 .dat 的形态：文件头 6 字节固定魔数，之后整段用同一个字节做 XOR。
    不同版本/来源偏移与密钥不同，所以这里暴力试 key(0..255) × 偏移(0/6/…)。
    """
    for offset in (6, 0, 8, 4, 2):
        if len(data) < offset + 16:
            continue
        for key in range(256):
            head = bytes(b ^ key for b in data[offset:offset + 16])
            ext = sniff(head)
            if ext:
                payload = bytes(b ^ key for b in data[offset:])
                # 再确认一次（避免偶然命中）
                if sniff(payload) == ext:
                    return ext, offset, key, payload
    return None


def header_hex(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


def decode_file(path: Path, out_dir: Path, verbose: bool = True):
    data = path.read_bytes()
    result = try_decode(data)
    if not result:
        if verbose:
            print(f"  [未解出] {path.name}  size={len(data):,}B  head={header_hex(data)}")
        return None
    ext, offset, key, payload = result
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (path.stem + "_decoded" + ext)
    out_path.write_bytes(payload)
    if verbose:
        print(f"  [OK] {path.name}  size={len(data):,}B  -> {out_path.name} "
              f"({ext}, offset={offset}, key=0x{key:02X}, 解开后 {len(payload):,}B) "
              f"head={header_hex(data)}")
    return out_path


def find_recent(root: Path, limit: int):
    hits = []
    if not root.exists():
        return hits
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(".dat"):
                continue
            stem = name[:-4].lower()
            # _t/_h/_b 是缩略图/高清/气泡副本，优先原图
            if stem.endswith(("_t", "_h", "_b")):
                continue
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            hits.append((st.st_mtime, st.st_size, p))
    hits.sort(reverse=True)
    return hits[:limit]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--recent", type=int, default=10, help="列出最近 N 个 .dat 并解密")
    ap.add_argument("--file", type=Path, help="只解这一个文件")
    ap.add_argument("--minutes", type=float, default=0, help="只处理最近 N 分钟内的文件（0=不限）")
    args = ap.parse_args()

    if args.file:
        decode_file(args.file, args.out_dir)
        return 0

    items = find_recent(args.root, args.recent * 3 if args.minutes else args.recent)
    now = time.time()
    shown = 0
    print(f"扫描根目录: {args.root}")
    for mtime, size, path in items:
        age_min = (now - mtime) / 60
        if args.minutes and age_min > args.minutes:
            continue
        print(f"\n[{time.strftime('%H:%M:%S', time.localtime(mtime))}] {size:,}B  {path}")
        decode_file(path, args.out_dir)
        shown += 1
        if shown >= args.recent:
            break
    print(f"\n共处理 {shown} 个文件；解密结果目录: {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
