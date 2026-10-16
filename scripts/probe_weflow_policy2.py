"""看清 WeFlow 初始化保护：JS 侧的 formatInitProtectionError 文案 + DLL 里的到期逻辑线索。

用法（仅打印，不改任何文件）：
  python probe_weflow_policy.py js     # 打印 app.asar 里保护相关代码片段
  python probe_weflow_policy.py dll    # 打印 wcdb_api.dll 里的可疑字符串
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ASAR = Path(r"C:\Program Files\WeFlow\resources\app.asar")
DLL = Path(r"C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64\wcdb_api.dll")

JS_PATTERNS = [
    b"formatInitProtectionError",
    b"initProtection",
    b"wcdb_runtime_policy_version",
    b"-1008",
    b"protectionAuth",
]

DLL_WORDS = ["expire", "expir", "trial", "license", "licence", "授权", "到期", "试用",
             "policy", "authentic", "token", "issue", "clock", "time", "date", "day",
             "invalid", "mismatch", "denied", "forbid"]


def show_js() -> None:
    data = ASAR.read_bytes()
    for pat in JS_PATTERNS:
        hits = [m.start() for m in re.finditer(re.escape(pat), data)]
        print(f"\n===== {pat.decode()} : {len(hits)} 处 =====")
        for pos in hits[:3]:
            lo, hi = max(0, pos - 1200), min(len(data), pos + 2600)
            text = data[lo:hi].decode("utf-8", "replace")
            print(f"----- offset {pos} -----")
            print(text)


def show_dll() -> None:
    data = DLL.read_bytes()
    print(f"wcdb_api.dll 大小 {len(data):,} 字节")
    # 提取所有长度>=5 的可打印 ASCII 串
    strings = re.findall(rb"[\x20-\x7e]{5,}", data)
    print(f"可打印串 {len(strings)} 条")
    print("\n===== 命中关键词的串 =====")
    seen: set[str] = set()
    for raw in strings:
        s = raw.decode("ascii", "replace")
        low = s.lower()
        if any(w in low for w in DLL_WORDS):
            if s in seen:
                continue
            seen.add(s)
            print(f"  {s[:180]}")
    print("\n===== 可能的日期/版本常量附近（YYYY-MM-DD 或 20xx 数字）=====")
    for m in re.finditer(rb"20\d\d[-/]\d\d[-/]\d\d", data):
        print("  ", m.group().decode())
    print("\n===== UTF-16 中文串（可能是提示文案）=====")
    for m in re.finditer(rb"(?:[\x00-\xff][\x4e-\x9f]){4,}", data):
        try:
            s = m.group().decode("utf-16-le")
        except Exception:  # noqa: BLE001
            continue
        if any(w in s for w in ("到期", "授权", "试用", "过期", "验证", "许可", "时间")):
            print("  ", s[:120])


if __name__ == "__main__":
    what = (sys.argv[1] if len(sys.argv) > 1 else "js").lower()
    if what == "dll":
        show_dll()
    else:
        show_js()
