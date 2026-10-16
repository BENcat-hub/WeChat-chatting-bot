"""离线回归检查：排障页错误归类语义（不需要 AstrBot 在场）。

跑法：
    python scripts/check_diagnostic_envelope.py

背景：2026-10-16 凌晨修掉了「上游 502 被报成 reference_unavailable」的归类缺陷，
涉及两处：`astrbot_plugin_image_companion/main.py::_generation_failure_code()`（上游优先判定）
与 `astrbot_plugin_private_companion/diagnostic_envelope.py::classify_diagnostic_error()`。
本脚本只检查第二层的语义映射，用来防止后面又改回 unknown/不可重试。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

REPO = pathlib.Path(__file__).resolve().parent.parent
ENVELOPE = REPO / "astrbot" / "data" / "plugins" / "astrbot_plugin_private_companion" / "diagnostic_envelope.py"

# (排障页 detail 文本里的错误码, 期望 category, 期望 retryable)
EXPECTED = [
    ("provider_unavailable", "provider", True),
    ("provider_rate_limited", "provider", True),
    ("provider_access_denied", "authorization", False),
    ("reference_unavailable", "configuration", False),
    ("provider_timeout", "timeout", True),
]


def load_module(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if not ENVELOPE.is_file():
        print(f"找不到 {ENVELOPE}")
        return 2
    module = load_module(ENVELOPE)
    failures = 0
    for code, want_category, want_retryable in EXPECTED:
        detail = f"“我会画给你看”任务失败（{code}）。"
        envelope = module.normalize_diagnostic_result(
            {"type": "image_generation_selfie", "ok": False, "error": detail, "duration_ms": 2586}
        )
        got_category = envelope["error_category"]
        got_retryable = bool(envelope["retryable"])
        marked = module.classify_diagnostic_error(detail)
        ok = got_category == want_category and got_retryable == want_retryable and marked == want_category
        failures += 0 if ok else 1
        print(
            f"[{'OK ' if ok else 'BAD'}] {code:24s} category={got_category:14s}（期望 {want_category:14s}）"
            f" retryable={got_retryable!s:5s}（期望 {want_retryable!s:5s}） next_step={envelope['next_step'][:48]}"
        )
    print(f"结果：{len(EXPECTED) - failures}/{len(EXPECTED)} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
