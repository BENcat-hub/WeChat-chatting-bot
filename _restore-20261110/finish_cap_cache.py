# -*- coding: utf-8 -*-
"""P0-A 收尾：把 gc 兜底改成受开关控制，并给 bridge 的 status 加记忆。

前半段（image_runtime.py 的探测缓存 + 包装函数）已由 patch_cap_cache.py 写入；
本脚本补最后两处，可重复执行（幂等）。
"""
from __future__ import annotations

import os
import py_compile
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "astrbot", "data", "plugins")
RUNTIME = os.path.join(PLUGINS, "astrbot_plugin_image_companion", "image_runtime.py")
BRIDGE = os.path.join(
    PLUGINS,
    "astrbot_plugin_private_companion",
    "companion",
    "integrations",
    "image_companion_bridge.py",
)
TAG = "probecache2-20261110"

GC_OLD = "        for obj in gc.get_objects():"
GC_NEW = "        for obj in (gc.get_objects() if _PROBE_GC_ALLOWED else ()):"

TTL_ANCHOR = "class _ImageCurrentContractError(RuntimeError):"
TTL_LINE = '_IMAGE_STATUS_CACHE_TTL = float(os.environ.get("IMAGE_STATUS_CACHE_TTL", "20"))\n\n\n'

STATUS_OLD = """    def _image_companion_status(self) -> dict[str, Any]:
        \"\"\"Return the split service status without importing its runtime.\"\"\"
        mode, api, _generation, reason = self._image_companion_contract()"""

STATUS_NEW = """    def _image_companion_status(self) -> dict[str, Any]:
        \"\"\"Return the split service status, memoised for a short window.

        The proactive tick asks for this once per action and per user; with no
        cache that rebuilt the whole six-backend status dozens of times inside
        a single tick, on the event loop.
        \"\"\"
        mode, api, generation, reason = self._image_companion_contract()
        key = (id(api), generation, mode, reason)
        now = time.monotonic()
        cache = getattr(self, "_image_status_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._image_status_cache = cache
        hit = cache.get(key)
        if hit is not None and (now - hit[0]) <= _IMAGE_STATUS_CACHE_TTL:
            return dict(hit[1])
        value = self._image_companion_status_uncached(mode, api, generation, reason)
        if len(cache) > 16:
            cache.clear()
        cache[key] = (now, value)
        return value

    def _image_companion_status_uncached(
        self,
        mode: str,
        api: Any,
        _generation: int,
        reason: str,
    ) -> dict[str, Any]:
        \"\"\"Return the split service status without importing its runtime.\"\"\""""


def file_nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def adapt(text: str, nl: str) -> str:
    return text if nl == "\n" else text.replace("\n", nl)


def patch(path: str, pairs: list[tuple[str, str]], repeat_ok: bool = False) -> bool:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        src = fh.read()
    nl = file_nl(src)
    out = src
    changed = False
    for old, new in pairs:
        old_a, new_a = adapt(old, nl), adapt(new, nl)
        if new_a in out and old_a not in out:
            print(f"  [skip] 已打过：{old_a.splitlines()[0][:60]}")
            continue
        count = out.count(old_a)
        if count == 0:
            print(f"  [warn] 锚点不存在：{old_a.splitlines()[0][:60]}")
            continue
        if count > 1 and not repeat_ok:
            print(f"  [fail] 锚点出现 {count} 次（应为 1）：{old_a.splitlines()[0][:60]}")
            return False
        out = out.replace(old_a, new_a)
        changed = True
        print(f"  [ok] 替换 {count} 处：{old_a.splitlines()[0][:60]}")
    if not changed:
        return True
    backup = f"{path}.bak-{TAG}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)
    print(f"  [write] {os.path.basename(path)}（备份 {os.path.basename(backup)}）")
    return True


def main() -> int:
    ok = True
    print("image_runtime.py")
    ok &= patch(RUNTIME, [(GC_OLD, GC_NEW)], repeat_ok=True)
    print("image_companion_bridge.py")
    ok &= patch(BRIDGE, [(TTL_ANCHOR, adapt(TTL_LINE, file_nl(open(BRIDGE, encoding="utf-8", newline="").read())) + TTL_ANCHOR)])
    ok &= patch(BRIDGE, [(STATUS_OLD, STATUS_NEW)])
    if not ok:
        return 2
    for path in (RUNTIME, BRIDGE):
        py_compile.compile(path, doraise=True)
        with open(path, "r", encoding="utf-8", newline="") as fh:
            src = fh.read()
        print(
            f"compile OK {os.path.basename(path)}: gc_guarded={src.count(GC_NEW)} "
            f"ttl={src.count('_IMAGE_STATUS_CACHE_TTL')} uncached={src.count('_image_companion_status_uncached')}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
