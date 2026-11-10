# -*- coding: utf-8 -*-
"""验证探针缓存：冷启动一次、之后只走缓存。

在 astrbot venv 里、把插件目录加进 sys.path 后运行。
"""
from __future__ import annotations

import importlib
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "astrbot", "data", "plugins")
sys.path.insert(0, PLUGINS)

mod = importlib.import_module("astrbot_plugin_image_companion.image_runtime")
print("module:", mod.__file__)
print("probe gc allowed:", mod._PROBE_GC_ALLOWED, "ttl:", mod._PROBE_TTL, "positive ttl:", mod._PROBE_POSITIVE_TTL)


class Stub:
    context = None


stub = Stub()
# 冷路径：一次真实扫描（gc 关闭，所以只走 get_registered_star 快路径）
start = time.perf_counter()
first = None
cost_cold = time.perf_counter() - start

# 找真实 mixin 类
target = None
for name in dir(mod):
    obj = getattr(mod, name)
    if isinstance(obj, type) and hasattr(obj, "_find_sdgen_plugin"):
        target = obj
        break
print("mixin:", target.__name__ if target else None)
assert target is not None

inst = object.__new__(target)
inst.context = None
start = time.perf_counter()
value = target._find_sdgen_plugin(inst)
cold = time.perf_counter() - start
samples = []
for _ in range(200):
    t0 = time.perf_counter()
    target._find_sdgen_plugin(inst)
    samples.append(time.perf_counter() - t0)
warm = sorted(samples)[len(samples) // 2]
print(f"cold={cold * 1000:.3f} ms  warm_median={warm * 1e6:.1f} us  value={value!r}")

# 负缓存是否真的生效：删缓存后再测一次应仍是快路径
mod.invalidate_plugin_probe_cache()
start = time.perf_counter()
target._find_sdgen_plugin(inst)
again = time.perf_counter() - start
print(f"after_invalidate={again * 1000:.3f} ms（gc 关闭，无全堆扫描）")
