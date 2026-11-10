# -*- coding: utf-8 -*-
"""量化 `gc.get_objects()` 全堆扫描的代价（AstrBot 卡顿的元凶）。

在 astrbot venv 里裸跑：只测堆扫描本身，不依赖插件代码。
"""
from __future__ import annotations

import gc
import statistics
import time


def main() -> None:
    gc.collect()
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        objs = gc.get_objects()
        cost = time.perf_counter() - start
        samples.append(cost)
        print(f"scan: {cost * 1000:8.1f} ms  objects={len(objs):,}")
        del objs
    print(
        f"median={statistics.median(samples) * 1000:.1f} ms  "
        f"min={min(samples) * 1000:.1f} ms  max={max(samples) * 1000:.1f} ms"
    )


if __name__ == "__main__":
    main()
