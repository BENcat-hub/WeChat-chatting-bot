"""梗速查「回复里不认识就重写」自测（不联网、不启动 AstrBot）。

用法：
    astrbot/.venv/Scripts/python.exe -X utf8 scripts/test_meme_reply_repair.py

覆盖：
1. 三种「不认识这个梗」句式的抽词；
2. 截断只摘掉问题句、保留其余；
3. 查询命中时用重写结果替换回复；
4. 查询为空时保留原回复；
5. 只重写一次（第二次调用不再动回复）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "astrbot" / "data" / "plugins" / "astrbot_plugin_private_companion"
sys.path.insert(0, str(PLUGIN_DIR))

if "astrbot" not in sys.modules:
    api = types.ModuleType("astrbot.api")

    class _Logger:
        def debug(self, *args, **kwargs):
            pass

        info = debug
        warning = debug
        error = debug

    api.logger = _Logger()
    sys.modules["astrbot"] = types.ModuleType("astrbot")
    sys.modules["astrbot.api"] = api

from meme_lookup import MemeLookupMixin  # noqa: E402


class _Resp:
    def __init__(self, text: str) -> None:
        self.completion_text = text


class _Event:
    def __init__(self, message: str = "这是什么梗") -> None:
        self.unified_msg_origin = "default:FriendMessage:1"
        self.message_str = message


class _Provider:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[dict] = []

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(completion_text=self.answer)


class _Ctx:
    def __init__(self, provider: _Provider) -> None:
        self.provider = provider

    async def llm_generate(self, **kwargs):
        return await self.provider.llm_generate(**kwargs)


class _Stub(MemeLookupMixin):
    def __init__(self, evidence: str, answer: str = "哦，city不city 就是问一句够不够有城市味。") -> None:
        self.enabled = True
        self.enable_meme_reply_repair = True
        self.enable_meme_web_lookup = True
        self.meme_lookup_sites = "bilibili,cnmeme,gengbaike"
        self.meme_lookup_summary_chars = 260
        self.meme_reply_repair_max_terms = 2
        self.data: dict = {}
        self._evidence = evidence
        self.provider = _Provider(answer)
        self.context = _Ctx(self.provider)
        self.lookup_terms: list[str] = []

    async def _meme_lookup_evidence(self, terms, limit=2, umo="", purpose=""):
        self.lookup_terms.append("/".join(terms))
        return self._evidence


def _check(name: str, ok: bool) -> bool:
    print(("[PASS] " if ok else "[FAIL] ") + name)
    return ok


async def main() -> int:
    passed = True

    stub = _Stub(evidence="- city不city（来源 bilibili）：形容城市味很足。")
    cases = {
        "这个我还真不知道 city不city 是什么意思，你说说看。": ["city不city"],
        "city不city 是什么意思，我也不太清楚。": ["city不city"],
        "「skibidi」我不太懂，你解释一下？": ["skibidi"],
        "哈哈好啊，今天天气不错。": [],
        "不知道这个梗是什么意思。": [],
    }
    for text, expected in cases.items():
        passed &= _check(f"抽词 {text!r} -> {expected}", stub._meme_ignorance_terms(text) == expected)

    original = "这个我还真不知道 city不city 是什么意思，你说说看。\n别的不说，先吃饭吧。"
    passed &= _check(
        "截断只摘问题句",
        stub._meme_strip_ignorance(original, ["city不city"]) == "别的不说，先吃饭吧。",
    )

    # 命中 → 替换
    resp = _Resp(original)
    event = _Event()
    await stub._repair_unknown_meme_response(event, resp)
    passed &= _check("命中后回复被重写", resp.completion_text == "哦，city不city 就是问一句够不够有城市味。")
    passed &= _check("查询词正确", stub.lookup_terms == ["city不city"])
    passed &= _check("重写时带上了释义", "city不city" in str(stub.provider.calls[0].get("system_prompt", "")))
    passed &= _check("重写调用带 provider 之外的 prompt", bool(stub.provider.calls[0].get("prompt")))

    # 只重写一次
    resp2 = _Resp("这个我还真不知道 city不city 是什么意思。")
    event2 = _Event()
    await stub._repair_unknown_meme_response(event2, resp2)
    first = resp2.completion_text
    await stub._repair_unknown_meme_response(event2, resp2)
    passed &= _check("同一事件只重写一次", resp2.completion_text == first)

    # 查不到 → 原样保留
    empty = _Stub(evidence="")
    resp3 = _Resp("这个我还真不知道 city不city 是什么意思。")
    await empty._repair_unknown_meme_response(_Event(), resp3)
    passed &= _check("查不到时保留原回复", resp3.completion_text == "这个我还真不知道 city不city 是什么意思。")
    passed &= _check("查不到时不再调用模型", empty.provider.calls == [])

    # 重写结果仍含「不认识」→ 退化为截断
    still = _Stub(evidence="- city不city（来源 bilibili）：形容城市味很足。", answer="我还是不知道 city不city 是什么意思。")
    resp4 = _Resp("这个我还真不知道 city不city 是什么意思。\n别的不说，先吃饭吧。")
    await still._repair_unknown_meme_response(_Event(), resp4)
    passed &= _check("重写仍不懂则只截断", resp4.completion_text == "别的不说，先吃饭吧。")

    print("\n结果：", "全部通过" if passed else "有失败")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
