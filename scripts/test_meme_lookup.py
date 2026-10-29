"""梗速查自测：直接打 DeepSeek Search/Fetch，验证 bilibili→梗wiki→梗百科 链路。

用法（astrbot venv，插件目录外也能跑）：
    astrbot/.venv/Scripts/python.exe -X utf8 scripts/test_meme_lookup.py city不city
    第二个参数可覆盖站点顺序，例如 cnmeme,gengbaike 用来只验证兜底站。
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_private_companion"
sys.path.insert(0, str(PLUGIN_DIR))

# 插件模块在 import 时会拿 astrbot.api.logger，这里用最小桩避免启动 AstrBot。
if "astrbot" not in sys.modules:
    api = types.ModuleType("astrbot.api")

    class _Logger:
        def debug(self, *args, **kwargs):
            print("[debug]", args[0] % args[1:] if len(args) > 1 else args[0])

        info = debug
        warning = debug

    api.logger = _Logger()
    sys.modules["astrbot"] = types.ModuleType("astrbot")
    sys.modules["astrbot.api"] = api

from meme_lookup import MemeLookupMixin  # noqa: E402


def _provider_settings() -> dict:
    cfg = json.loads((ROOT / "astrbot" / "data" / "cmd_config.json").read_text(encoding="utf-8-sig"))
    return cfg.get("provider_settings") or {}


class _Stub(MemeLookupMixin):
    def __init__(self) -> None:
        self.data: dict = {}
        self.enable_meme_web_lookup = True
        self.meme_lookup_sites = "bilibili,cnmeme,gengbaike"
        self.meme_lookup_summary_chars = 260

    class _Context:
        def get_config(self, umo: str = ""):
            return {"provider_settings": _provider_settings()}

    context = _Context()


async def main() -> None:
    term = sys.argv[1] if len(sys.argv) > 1 else "city不city"
    stub = _Stub()
    if len(sys.argv) > 2:
        stub.meme_lookup_sites = sys.argv[2]
    print("available =", stub._meme_lookup_available(), "sites =", stub.meme_lookup_sites)
    raw = await stub._deepseek_search(f"{term} 是什么意思 site:bilibili.com", count=3)
    print(f"\n[search] {len(raw)} 条")
    for item in raw:
        print(f"- score={item.get('rerank_score')} site={item.get('site_name')} {item.get('title')}\n  {item.get('url')}")
    print("\n[lookup]")
    info = await stub._meme_lookup_term(term)
    print(json.dumps(info, ensure_ascii=False, indent=2))
    print("\n[evidence]")
    print(await stub._meme_lookup_evidence([term], limit=1, purpose="selftest"))


if __name__ == "__main__":
    asyncio.run(main())
