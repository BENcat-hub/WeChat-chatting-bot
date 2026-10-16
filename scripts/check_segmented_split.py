"""自检：用真实插件配置跑一遍分段切分，看一条回复会被切成几条什么消息。

用法（任意目录，用 AstrBot 的 venv python）：
  astrbot 虚拟环境的 Scripts\\python.exe scripts\\check_segmented_split.py
  同上再加一个引号参数即可自定义待测文本，例如 "要测的文本"
不带参数时用三条内置真实样本。
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "astrbot", "data", "plugins"))

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin  # noqa: E402
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin  # noqa: E402

CFG = os.path.join(
    ROOT, "astrbot", "data", "config", "astrbot_plugin_private_companion_config.json"
)

SAMPLES = (
    "没有啊 96分就是厉害，我夸你是真心的，就是顺手损你一句都哭成那样，不行吗，"
    "墨镜那套图哪来的，你自己做的",
    "哦，顺手存的还拿来跟我显摆，行吧，图我先收下了，下次你考个更拽的分数我再还给你",
    "我今天有点累。晚点再说吧。你也早点睡。",
)


class _Event:
    unified_msg_origin = "QBot0:PrivateMessage:owner"


class _Harness(ProactiveMessageMixin, EventDispatchMixin):
    def _feature_enabled_or_temp_unlocked(self, key: str, default: bool = False) -> bool:
        return bool(getattr(self, key, default))

    @staticmethod
    def _segmented_scope_allows_event(_event: object) -> bool:
        return True

    @staticmethod
    def _segmented_scope_allows_umo(_umo: str) -> bool:
        return True

    @staticmethod
    def _segmented_platform_allows(**_kwargs: object) -> bool:
        return True


def _build_harness(cfg: dict) -> _Harness:
    harness = _Harness()
    for key, value in cfg.items():
        if key == "basic_config" or isinstance(value, dict):
            continue
        setattr(harness, key, value)
    for key, value in (cfg.get("basic_config") or {}).items():
        setattr(harness, key, value)
    return harness


def main() -> int:
    with open(CFG, encoding="utf-8-sig") as handle:
        cfg = json.load(handle)
    harness = _build_harness(cfg)

    print(f"配置: {CFG}")
    for key in (
        "enable_segmented_proactive_reply",
        "enable_llm_controlled_segmenting",
        "enable_segmented_plugin_rules",
        "enable_segmented_proactive_chat_profiles",
        "segmented_proactive_split_mode",
        "segmented_proactive_regex",
        "segmented_proactive_threshold",
        "segmented_proactive_min_segment_chars",
        "segmented_proactive_max_segments",
    ):
        print(f"  {key} = {getattr(harness, key, None)!r}")

    texts = sys.argv[1:] or list(SAMPLES)
    for text in texts:
        print("\n输入:", text)
        try:
            segments = harness._split_proactive_text(text, event=_Event())
        except Exception as exc:  # noqa: BLE001 - 自检脚本要把异常显示出来
            print(f"  <切分失败: {type(exc).__name__}: {exc}>")
            continue
        print(f"  切成 {len(segments)} 条:")
        for index, segment in enumerate(segments, 1):
            print(f"    {index}. {segment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
