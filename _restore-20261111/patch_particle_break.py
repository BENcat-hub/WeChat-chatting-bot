# -*- coding: utf-8 -*-
"""语气词后补软断点：真人长句黏连（"那就这么定了我这会儿就去把靠垫摆好"）的直接解法。

真实聊天里出现「那我就不多嘴了傍晚了 你那边也开始冷下来了吧」这类两条语义黏在一起
的气泡——模型经常不写标点，连接词软断（就/也/才…）覆盖不到这种从句边界。
语气词（了/吧/啊/呢/嘛/呀/哦）后面紧跟新主语，几乎一定是新的小句，在这里补一个软断点
最安全；不碰带引号/书名号/换行的创作摘录（那两处已有保护），也不碰技术内容（闸在前面）。
"""
from __future__ import annotations

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUG = os.path.join(ROOT, "astrbot", "data", "plugins", "astrbot_plugin_private_companion")

MAIN = os.path.join(PLUG, "main.py")
DISPATCH = os.path.join(PLUG, "event_dispatch.py")

HELPER = '''def _soft_break_after_particle(text: str, min_len: int = 14, tail_min: int = 6) -> str:
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


def patch_main() -> str:
    src = io.open(MAIN, encoding="utf-8", newline="").read()
    if "_soft_break_after_particle" in src:
        return "main.py 已打过补丁"
    anchor = "    def _humanize_split_chunks("
    if anchor not in src:
        return "!! main.py 找不到 _humanize_split_chunks"
    src = src.replace(anchor, HELPER + anchor, 1)
    old = '            text = re.sub(r"(?<=[\u4e00-\u9fff])(?=[就也才又还而且但可])", "，", text)'
    new = (
        '            text = _soft_break_after_particle(text)\n'
        '            text = re.sub(r"(?<=[\u4e00-\u9fff])(?=[就也才又还而且但可])", "，", text)'
    )
    if old not in src:
        return "!! main.py 找不到连接词软断那一行"
    src = src.replace(old, new, 1)
    io.open(MAIN, "w", encoding="utf-8", newline="").write(src)
    return "main.py patched"


NESTED = '''            def _particle_break(text: str, min_len: int = 14, tail_min: int = 6) -> str:
                """语气词后接新小句时补逗号，解决"那就这么定了我这会儿就…"这种黏连。"""
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


def patch_dispatch() -> str:
    src = io.open(DISPATCH, encoding="utf-8", newline="").read()
    if "_particle_break" in src:
        return "event_dispatch.py 已打过补丁"
    anchor = "            def _pieces(text: str) -> list[str]:"
    if anchor not in src:
        return "!! event_dispatch.py 找不到 _pieces"
    src = src.replace(anchor, NESTED + anchor, 1)
    old = '            def _pieces(text: str) -> list[str]:\r\n                raw = re.split('
    if old not in src:
        # 兜底：在 _pieces 体的 raw = re.split 前插一行
        old2 = "            def _pieces(text: str) -> list[str]:"
        idx = src.find(old2)
        seg = src[idx: idx + 400]
        marker = seg.find("raw = re.split(")
        if marker < 0:
            return "!! 找不到 _pieces 里的 re.split"
        insert_at = idx + marker
        src = src[:insert_at] + "text = _particle_break(text)\n                " + src[insert_at:]
    else:
        src = src.replace(old, old.split("\r\n")[0] + "\r\n                text = _particle_break(text)\r\n                raw = re.split(", 1)
    io.open(DISPATCH, "w", encoding="utf-8", newline="").write(src)
    return "event_dispatch.py patched"


if __name__ == "__main__":
    print(patch_main())
    print(patch_dispatch())
