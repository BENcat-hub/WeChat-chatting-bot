"""自检：插话触发概率是否真的按 60% 生效（不碰生产状态）。

用法：astrbot\\.venv\\Scripts\\python.exe -X utf8 scripts\\test_group_interject_probability.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_private_companion"


def load_module():
    """把插件目录当成一个临时包载入，绕过 __init__ 的运行时依赖。"""
    import types

    pkg_name = "_pc_probe_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PLUGIN)]
    sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.group_observation", PLUGIN / "group_observation.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Fake:
    """只装 _group_interjection_allowed 真正用到的那几个属性。"""

    def __init__(self, probability=0.6):
        self.enable_group_interjection = True
        self.group_interject_probability = probability
        self.group_interject_max_daily = 10
        self.group_interject_min_interval_minutes = 5

    def _filtered_group_recent_messages(self, group):
        return [{"talking_to": "group", "text": group.get("text", "")}]


def trial(module, obj, text, group=None, rounds=2000):
    module.random.seed(7)
    hits = 0
    last_reason = ""
    for _ in range(rounds):
        allowed, reason = module.GroupObservationMixin._group_interjection_allowed(
            obj, dict(group or {}), text
        )
        if allowed:
            hits += 1
            last_reason = reason
    return hits / rounds, last_reason


def main() -> int:
    module = load_module()
    obj = Fake(0.6)
    ok = True

    for text in ("有没有人懂这个怎么弄", "笑死我了这操作", "救命啊怎么办"):
        rate, reason = trial(module, obj, text)
        print(f"{text:<16} → {rate:.1%}  ({reason})")
        if not 0.55 <= rate <= 0.65:
            ok = False

    rate, reason = trial(module, obj, "今天天气不错", rounds=200)
    print(f"{'今天天气不错':<16} → {rate:.1%}  ({reason})")
    if rate != 0.0:
        ok = False

    off = Fake(0.0)
    rate, reason = trial(module, off, "有没有人懂这个怎么弄", rounds=200)
    print(f"概率设 0          → {rate:.1%}  ({reason})")
    if rate != 0.0:
        ok = False

    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
