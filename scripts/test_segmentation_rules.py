"""诊断：分段规则为什么不切长句（用真实运行配置跑 _split_proactive_text）。

用法：
    astrbot/.venv/Scripts/python.exe -X utf8 scripts/test_segmentation_rules.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_private_companion"
CFG_PATH = ROOT / "astrbot" / "data" / "config" / "astrbot_plugin_private_companion_config.json"
PKG = "_pc_seg_probe"


def load_package() -> types.ModuleType:
    if PKG in sys.modules:
        return sys.modules[PKG]
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[PKG] = pkg
    return pkg


def load_module(name: str):
    full = f"{PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, PLUGIN_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


def build_config():
    cfg = json.loads(CFG_PATH.read_text(encoding="utf-8-sig"))

    def flatten(value, prefix=""):
        out = {}
        if isinstance(value, dict):
            for key, item in value.items():
                out.update(flatten(item, f"{prefix}{key}_" if False else prefix))
        return out

    return cfg


class Harness:
    def __init__(self, cfg: dict, chat_type: str = "private") -> None:
        self.cfg = cfg
        self.chat_type = chat_type

    # runtime_persona_setting 走这个
    def persona_setting(self, key, default=None):
        value = self.cfg.get(key, default)
        return default if value is None else value

    def _feature_enabled_or_temp_unlocked(self, key, default=False):
        return bool(self.persona_setting(key, default))

    def _persona_value(self, key, default=None):
        return self.persona_setting(key, default)

    # ---- 分段域 ----
    def _segmented_chat_type_for_umo(self, umo):
        return self.chat_type

    def _segmented_chat_type_for_event(self, event):
        return self.chat_type

    def _segmented_chat_scope_allows(self, chat_type):
        if bool(self.persona_setting("enable_segmented_proactive_chat_profiles", False)):
            return bool(self.persona_setting(f"segmented_proactive_{chat_type}_enabled", True))
        scope = str(self.persona_setting("segmented_proactive_chat_scope", "all") or "all").lower()
        if scope not in {"all", "private", "group"}:
            scope = "all"
        return scope == "all" or scope == chat_type

    def _segmented_setting(self, name, *, event=None, umo="", chat_type="", default=None):
        fallback = self.persona_setting(f"segmented_proactive_{name}", default)
        if not bool(self.persona_setting("enable_segmented_proactive_chat_profiles", False)):
            return fallback
        resolved = str(chat_type or "").strip().lower() or self.chat_type
        if resolved not in {"private", "group"}:
            resolved = "private"
        return self.persona_setting(f"segmented_proactive_{resolved}_{name}", fallback)

    def _apply_segmented_content_replacements(self, text, *, event=None, umo="", chat_type=""):
        return text, 0

    def _normalize_tts_tags(self, text):
        return text

    def __getattr__(self, name):
        # 诊断用：未显式实现的私有方法一律当作「原样返回」的空操作
        if name.startswith("_"):
            def _noop(*args, **kwargs):
                for arg in args:
                    if isinstance(arg, str):
                        return arg
                return None

            return _noop
        raise AttributeError(name)


def main() -> int:
    load_package()
    dispatch = load_module("event_dispatch")
    cfg = build_config()
    h = Harness(cfg)

    print("关键配置：")
    for key in (
        "enable_segmented_proactive_reply",
        "enable_segmented_plugin_rules",
        "enable_segmented_proactive_chat_profiles",
        "segmented_proactive_max_segments",
        "segmented_proactive_min_segment_chars",
        "segmented_proactive_threshold",
        "segmented_proactive_split_mode",
        "segmented_proactive_chat_scope",
        "segmented_proactive_private_min_segment_chars",
    ):
        print(f"  {key} = {cfg.get(key)!r}")

    samples = [
        "补偿啊，我又不欠你钱，欠的是照片",
        "这样，这周末我专门出门一趟，好好拍一组给你，不糊的那种",
    ]
    for text in samples:
        chunks = dispatch.EventDispatchMixin._split_proactive_text(
            h, text, umo="default:PrivateMessage:1"
        )
        print(f"\n原文（{len(text)}字）：{text}")
        print(f"  切出 {len(chunks)} 段：{chunks}")

    # 模拟生产里 20:42 那次：模型自己切了 2 段，但每段都超长
    print("\n=== 模拟「模型已切段但段太长」的重切路径 ===")
    segmented = load_module("segmented_message")
    model_segments = [
        "补偿啊，我又不欠你钱，欠的是照片",
        "这样，这周末我专门出门一趟，好好拍一组给你，不糊的那种",
    ]
    planned: list[str] = []
    for segment in model_segments:
        needs_split = segmented.segment_exceeds_granularity(segment, 20)
        print(f"  「{segment}」超粒度？{needs_split}")
        if needs_split:
            parts = dispatch.EventDispatchMixin._split_proactive_text(
                h, segment, umo="default:PrivateMessage:1"
            )
            if len(parts) >= 2:
                planned.extend(parts)
                continue
        planned.append(segment)
    limited = segmented.limit_planned_segments([planned], 4)[0]
    print(f"  重切后 {len(planned)} 段，压回 max_segments=4 后 {len(limited)} 段：")
    for index, part in enumerate(limited, 1):
        print(f"    {index}. {part}  ({len(part)}字)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
