"""在 WeFlow 的 app.asar 里搜「native runtime policy mismatch」相关代码，看清 -105 是怎么判定的。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ASAR = Path(r"C:\Program Files\WeFlow\resources\app.asar")
NEEDLES = [b"policy mismatch", b"native runtime policy", b"POLICY_MISMATCH", b"runtime_policy"]


def main() -> int:
    data = ASAR.read_bytes()
    print(f"app.asar 大小 {len(data):,} 字节")
    for needle in NEEDLES:
        hits = [m.start() for m in re.finditer(re.escape(needle), data)]
        print(f"\n=== {needle.decode()} : {len(hits)} 处 ===")
        for pos in hits[:6]:
            lo = max(0, pos - 400)
            hi = min(len(data), pos + 400)
            chunk = data[lo:hi].decode("utf-8", "replace")
            print(f"--- offset {pos} ---")
            print(chunk.replace("\x00", "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
