# -*- coding: utf-8 -*-
"""P0-A: 拆掉事件循环上的 GC 全堆扫描。

两处 `gc.get_objects()` 兜底探测（sdgen / comfyui public service）在插件未安装时
每次调用都扫完全堆、返回 None，而且每个 user、每次 tick 都会重算，
导致 event loop 单次卡 15~39 秒（见 astrbot/data/logs/event_loop_watchdog.log）。

做法：
1. image_runtime.py 增加模块级探测缓存：负结果缓存 900s，正结果缓存 60s；
   默认**关闭** gc 全堆扫描（IMAGE_PROBE_GC=1 可应急打开）。
2. image_companion_bridge.py 的 _image_companion_status() 增加 20s 记忆，
   避免 12 个用户 × 每 tick 反复向上游要 status。

用法：
    python patch_cap_cache.py --check   # 只验证锚点
    python patch_cap_cache.py --apply   # 写盘（自动 .bak）
"""
from __future__ import annotations

import argparse
import os
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

PROBE_BLOCK = '''_PHOTO_GENERATION_TRACE_FILE_LOCK = threading.Lock()

# --- plugin probe cache -------------------------------------------------------
# `_find_sdgen_plugin` and `_get_comfyui_public_service` used to fall back to
# `gc.get_objects()` (a full-heap walk over >100k objects).  Neither backend is
# installed on this host, so every probe walked the whole heap and returned
# None: synchronously, inside the proactive tick, once per user.  That single
# pattern produced 15~39s event-loop stalls.  Results are memoised here and the
# heap walk is disabled by default (set IMAGE_PROBE_GC=1 to re-enable).
_PROBE_CACHE_LOCK = threading.Lock()
_PROBE_CACHE: dict[str, tuple[float, float, object]] = {}
_PROBE_TTL = float(os.environ.get("IMAGE_PROBE_TTL", "900"))
_PROBE_POSITIVE_TTL = float(os.environ.get("IMAGE_PROBE_POSITIVE_TTL", "60"))
_PROBE_GC_ALLOWED = os.environ.get("IMAGE_PROBE_GC", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def _probe_cache_get(key: str) -> tuple[bool, object]:
    with _PROBE_CACHE_LOCK:
        item = _PROBE_CACHE.get(key)
    if not item:
        return False, None
    ts, ttl, value = item
    if ttl > 0 and (time.time() - ts) > ttl:
        return False, None
    return True, value


def _probe_cache_set(key: str, value: object, ttl: float | None = None) -> None:
    resolved = _PROBE_POSITIVE_TTL if (ttl is None and value is not None) else ttl
    if resolved is None:
        resolved = _PROBE_TTL
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE[key] = (time.time(), float(resolved), value)


def invalidate_plugin_probe_cache() -> None:
    """Drop every cached plugin probe (call after installing/removing a plugin)."""
    with _PROBE_CACHE_LOCK:
        _PROBE_CACHE.clear()
'''

RUNTIME_ANCHOR = "_PHOTO_GENERATION_TRACE_FILE_LOCK = threading.Lock()\n"

SDGEN_OLD = '''    def _find_sdgen_plugin(self) -> Any | None:
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)'''

SDGEN_NEW = '''    def _find_sdgen_plugin(self) -> Any | None:
        hit, cached = _probe_cache_get("sdgen")
        if hit:
            return cached  # type: ignore[return-value]
        found = self._locate_sdgen_plugin()
        _probe_cache_set("sdgen", found)
        return found

    def _locate_sdgen_plugin(self) -> Any | None:
        try:
            getter = getattr(getattr(self, "context", None), "get_registered_star", None)'''

COMFY_OLD = '''    def _get_comfyui_public_service(self) -> Any | None:
        native = self._native_comfyui_service()'''

COMFY_NEW = '''    def _get_comfyui_public_service(self) -> Any | None:
        hit, cached = _probe_cache_get("comfyui_service")
        if hit:
            return cached  # type: ignore[return-value]
        found = self._locate_comfyui_public_service()
        _probe_cache_set("comfyui_service", found)
        return found

    def _locate_comfyui_public_service(self) -> Any | None:
        native = self._native_comfyui_service()'''

GC_OLD = "        for obj in gc.get_objects():"
GC_NEW = "        for obj in (gc.get_objects() if _PROBE_GC_ALLOWED else ()):"

BRIDGE_TTL_ANCHOR = "class _ImageCurrentContractError(RuntimeError):"
BRIDGE_TTL_BLOCK = '''_IMAGE_STATUS_CACHE_TTL = float(os.environ.get("IMAGE_STATUS_CACHE_TTL", "20"))

''' + BRIDGE_TTL_ANCHOR

STATUS_OLD = '''    def _image_companion_status(self) -> dict[str, Any]:
        """Return the split service status without importing its runtime."""
        mode, api, _generation, reason = self._image_companion_contract()'''

STATUS_NEW = '''    def _image_companion_status(self) -> dict[str, Any]:
        """Return the split service status, memoised for a short window.

        The proactive tick asks for this once per action, per user; without a
        cache that meant rebuilding the whole six-backend status dozens of
        times per tick.
        """
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
        """Return the split service status without importing its runtime."""'''

def _file_nl(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _adapt(text: str, nl: str) -> str:
    return text if nl == "\n" else text.replace("\n", nl)


def _apply(path: str, pairs: list[tuple[str, str]], tag: str) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        src = fh.read()
    problems: list[str] = []
    out = src
    nl = _file_nl(src)
    for old, new in pairs:
        old, new = _adapt(old, nl), _adapt(new, nl)
        count = out.count(old)
        if count != 1:
            problems.append(f"{os.path.basename(path)}: anchor matched {count}x -> {old.splitlines()[0][:60]!r}")
            continue
        out = out.replace(old, new, 1)
    if problems:
        return problems
    backup = f"{path}.bak-{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(out)
    return [f"patched {os.path.basename(path)} -> {os.path.basename(backup)}"]


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    runtime_pairs = [
        (RUNTIME_ANCHOR, PROBE_BLOCK),
        (SDGEN_OLD, SDGEN_NEW),
        (COMFY_OLD, COMFY_NEW),
    ]
    bridge_pairs = [
        (BRIDGE_TTL_ANCHOR, BRIDGE_TTL_BLOCK),
        (STATUS_OLD, STATUS_NEW),
    ]

    if args.check:
        ok = True
        for path, pairs in ((RUNTIME, runtime_pairs), (BRIDGE, bridge_pairs)):
            with open(path, "r", encoding="utf-8", newline="") as fh:
                src = fh.read()
            nl = _file_nl(src)
            for old, _new in pairs:
                count = src.count(_adapt(old, nl))
                flag = "OK " if count == 1 else "BAD"
                if count != 1:
                    ok = False
                print(f"  [{flag}] {os.path.basename(path)} x{count} {old.splitlines()[0][:70]}")
            gc_count = src.count(_adapt(GC_OLD, nl))
            if path == RUNTIME:
                print(f"  [{'OK ' if gc_count == 2 else 'BAD'}] gc.get_objects() 兜底点 = {gc_count}（应为 2）")
                if gc_count != 2:
                    ok = False
        print("CHECK", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    messages: list[str] = []
    for path, pairs in ((RUNTIME, runtime_pairs), (BRIDGE, bridge_pairs)):
        problems = _apply(path, pairs, "probecache-20261110")
        if problems:
            print("\n".join(problems))
            return 2
        messages.extend(problems)

    # 二次改写：两处 gc 兜底都要换成受开关控制
    with open(RUNTIME, "r", encoding="utf-8", newline="") as fh:
        src = fh.read()
    nl = _file_nl(src)
    replaced = src.replace(_adapt(GC_OLD, nl), _adapt(GC_NEW, nl))
    if replaced.count(_adapt(GC_NEW, nl)) != 2:
        print(f"gc fallback rewrite failed: {replaced.count(_adapt(GC_NEW, nl))} != 2")
        return 3
    with open(RUNTIME, "w", encoding="utf-8", newline="") as fh:
        fh.write(replaced)

    for path in (RUNTIME, BRIDGE):
        import py_compile

        py_compile.compile(path, doraise=True)
        print("compile OK:", os.path.basename(path))
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
