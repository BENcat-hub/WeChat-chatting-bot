# -*- coding: utf-8 -*-
"""语气词软断点（v2，作为**类方法**插入，不再放模块级）。

v1 的教训：把 `def` 写成 0 缩进插到类体中间，等于**提前结束 PrivateCompanionPlugin**，
后面所有方法（`_humanize_split_chunks`、`_normalize_provider_config_mode`…）都变成了
那个模块级函数的**嵌套函数**——`py_compile` 照样通过，但插件加载时报
`AttributeError: 'PrivateCompanionPlugin' object has no attribute '_normalize_provider_config_mode'`。
所以这里明确按 4 空格缩进写成方法，并在插入后用 AST 校验类方法数量。
"""
from __future__ import annotations

import ast
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion", "main.py")

METHOD = '''    def _soft_break_after_particle(self, text: str, min_len: int = 14, tail_min: int = 6) -> str:
        """语气词后面接新小句时补一个逗号（只在长句里动手，避免把短句切碎）。"""
        if not text or len(text) < min_len:
            return text
        particles = "了吧啊呢嘛呀哦"
        stops = "，。！？、；：…~～\\n 「」《》『』"
        out: list[str] = []
        n = len(text)
        for idx, ch in enumerate(text):
            out.append(ch)
            if ch not in particles:
                continue
            nxt = text[idx + 1] if idx + 1 < n else ""
            if not nxt or nxt in stops or nxt in "吗呢吧啊呀哦嘛了的":
                continue
            if n - (idx + 1) < tail_min:
                continue
            out.append("，")
        return "".join(out)

'''


def class_methods(src: str) -> tuple[int, bool, bool]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PrivateCompanionPlugin":
            names = [m.name for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            return len(names), "_humanize_split_chunks" in names, "_normalize_provider_config_mode" in names
    return 0, False, False


def main() -> int:
    src = io.open(MAIN, encoding="utf-8", newline="").read()
    before = class_methods(src)
    print("before:", before)
    if "_soft_break_after_particle" in src:
        print("已存在 _soft_break_after_particle，重新校验即可")
        return 0
    anchor = "    def _humanize_split_chunks("
    if anchor not in src:
        print("!! 找不到 _humanize_split_chunks")
        return 2
    if src.count(anchor) != 1:
        print(f"!! 锚点出现 {src.count(anchor)} 次，拒绝改")
        return 2
    src = src.replace(anchor, METHOD + anchor, 1)

    old = '            text = re.sub(r"(?<=[\u4e00-\u9fff])(?=[就也才又还而且但可])", "，", text)'
    new = (
        "            text = self._soft_break_after_particle(text)\n"
        '            text = re.sub(r"(?<=[\u4e00-\u9fff])(?=[就也才又还而且但可])", "，", text)'
    )
    if old not in src:
        print("!! 找不到连接词软断那一行")
        return 2
    src = src.replace(old, new, 1)

    after = class_methods(src)
    print("after:", after)
    if after[0] != before[0] + 1 or not after[1] or not after[2]:
        print("!! AST 校验失败：类方法数没涨或关键方法丢失，未写盘")
        return 3
    io.open(MAIN, "w", encoding="utf-8", newline="").write(src)
    print("main.py patched (method form)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
