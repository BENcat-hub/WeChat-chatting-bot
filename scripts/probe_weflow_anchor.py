"""定位 WeFlow 的离线防回拨状态（-105）是怎么判定的：JS 侧文件名/路径 + DLL 侧字符串。

只读，不修改任何文件。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ASAR = Path(r"C:\Program Files\WeFlow\resources\app.asar")
DLL = Path(r"C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64\wcdb_api.dll")

JS_NEEDLES = [
    b"anchor-v7",
    b"native-anchor",
    b"device-root",
    b"protectionAuth",
    b"issuedAtMs",
    b"safely",
]

DLL_NEEDLES = [
    b"anchor",
    b"Anchor",
    b"rollback",
    b"Rollback",
    b"anti_rollback",
    b"native-anchor",
    b"device-root",
    b"Runtime",
    b"Security",
    b"State",
    b"wcdb_runtime_policy_version",
    b"wcdb_authenticate",
]


def probe_js() -> None:
    data = ASAR.read_bytes()
    for needle in JS_NEEDLES:
        hits = [m.start() for m in re.finditer(re.escape(needle), data)]
        print(f"\n##### JS {needle.decode()} : {len(hits)} 处 #####")
        for pos in hits[:4]:
            lo, hi = max(0, pos - 900), min(len(data), pos + 1500)
            txt = data[lo:hi].decode("utf-8", "replace")
            print(f"--- offset {pos} ---")
            print(txt)


def probe_dll() -> None:
    data = DLL.read_bytes()
    for needle in DLL_NEEDLES:
        print(f"\n##### DLL {needle.decode()} #####")
        for m in list(re.finditer(re.escape(needle), data))[:12]:
            pos = m.start()
            lo, hi = max(0, pos - 120), min(len(data), pos + 160)
            chunk = data[lo:hi]
            ascii_view = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
            print(f"  off={pos}: {ascii_view}")


if __name__ == "__main__":
    what = (sys.argv[1] if len(sys.argv) > 1 else "js").lower()
    (probe_dll if what == "dll" else probe_js)()
