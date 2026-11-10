# -*- coding: utf-8 -*-
"""给 HUMANIZE-STYLE 文档追加第八节：语气词软断点 + 类体插入陷阱。"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "HUMANIZE-STYLE-20261111.md")

SECTION = """
## 八、语气词软断点（长句黏连的真解）

真实聊天里出现过这种气泡：

> 那我就不多嘴了傍晚了 你那边也开始冷下来了吧
> 那就这么定了我这会儿就去把靠垫摆好

模型不写标点，连接词软断（就/也/才…）覆盖不到「了/吧/啊」这种从句边界，于是两条语义黏在
一个气泡里。修法：`main.py::_soft_break_after_particle()` —— 长句（≥14 字）里，语气词
（了吧啊呢嘛呀哦）后面紧跟新小句且剩余 ≥6 字时补一个软逗号；`event_dispatch.py` 的
`_humanize_shorten` 内同名嵌套函数 `_particle_break` 做同一件事。

单测 `_restore-20261111/test_particle_break.py`（从源码抠函数、不加载 AstrBot）7 例全过：
`那就这么定了我这会儿就去把靠垫摆好` → `那就这么定了，我这会儿就去把靠垫摆好`；
`知道了就好` / `别熬了早点睡` 这类短句不动。

实测 v17（5 条话题）：均 5.8 / 中位 5.5 / ≤6 字 0.75，样本如 `泡个热水脚呗`、`谁夸的`、
`排这么长啊站累了吧`。

### 陷阱：0 缩进的 def 会悄悄截断类

第一版把 `_soft_break_after_particle` 写成 **模块级** 函数插到类体中间（`HELPER + anchor`），
等于提前结束 `class PrivateCompanionPlugin`，其后所有方法变成那个函数的**嵌套函数**。
`py_compile` 照样通过（语法合法），直到插件加载才炸：

```
AttributeError: 'PrivateCompanionPlugin' object has no attribute '_normalize_provider_config_mode'
```

正解：方法按 4 空格缩进插入，并在写盘前用 `ast` 校验「该类方法数 +1 且关键方法仍在」
（`_restore-20261111/patch_particle_break_v2.py` 的 `class_methods()`）。
经验：**改类体一律 AST 校验**；`py_compile` 只证明语法，不证明结构。
"""


def main() -> int:
    text = io.open(DOC, encoding="utf-8").read()
    if "语气词软断点" in text:
        print("已有该小节")
        return 0
    io.open(DOC, "w", encoding="utf-8").write(text.rstrip() + "\n" + SECTION)
    print("doc updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
