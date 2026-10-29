"""梗/网络用语联网速查。

遇到不认识的梗或网络用语时，用 AstrBot 4.29 新接入的 DeepSeek tools API
（``/v1/tools/search`` 与 ``/v1/tools/fetch``，即 web_search_deepseek /
deepseek_fetch_web_page 背后的同一组接口）按固定优先级查：

1. B 站：``<词> 是什么意思``（site:bilibili.com）
2. 梗 wiki：https://cnmeme.wiki/
3. 梗百科：https://gengbaike.cn/

前一步拿到的结果足够像释义就不再往下走；B 站视频页正文偏少，命中后会把
标题/简介拼成一句短释义，必要时再抓一次页面正文。群黑话联网参考与「空闲
自动浏览网页」共用这套策略。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from astrbot.api import logger

_DEEPSEEK_SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
_DEEPSEEK_FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

# 释义可信的信号词：标题/摘要里同时出现「词」和其中至少一个，多半是释义页。
_MEANING_HINTS = (
    "是什么意思",
    "什么梗",
    "什么梗",
    "梗的意思",
    "梗含义",
    "含义",
    "解释",
    "出处",
    "来源",
    "网络用语",
    "什么含义",
    "是指",
    "指的是",
)

_DEFAULT_SITES = (
    # (配置名, 站点域名, 显示名)
    ("bilibili", "bilibili.com", "bilibili"),
    ("cnmeme", "cnmeme.wiki", "梗wiki"),
    ("gengbaike", "gengbaike.cn", "梗百科"),
)

_TERM_PATTERN = re.compile(r"[\"'“”‘’「」【】《》\s]+")
_QUERY_TRIGGER_PATTERN = re.compile(
    r"(是什么意思|什么意思|是什么梗|什么梗|是啥意思|啥意思|怎么来的|是什么|什么来头)"
)
_QUERY_TAIL_NOISE = re.compile(
    r"(?:到底|究竟|都说|说的|所谓|那个|这个|一个|一下|请问|你懂|知道|是|个|的|吗|呢|啊|呀|嘛|吧|\s)+$"
)
_QUERY_STOP_TERMS = frozenset({"这梗", "那个梗", "这个梗", "梗", "什么", "啥"})

# 回复里「我不认识这个词」的三种句式；group("term") 就是要联网查的词。
_IGNORANCE_PATTERNS = (
    re.compile(
        r"(?:我)?(?:也|真|其实|还|反正)?(?:不知道|不太清楚|不清楚|不懂|没听(?:说)?过|没见过)"
        r"\s*(?:这个|那个)?\s*[「『“\"']?(?P<term>[\w\u4e00-\u9fa5\-]{2,16})[」』”\"']?\s*"
        r"(?:是什么意思|什么意思|是啥意思|啥意思|是什么梗|这个梗|的梗|是啥)"
    ),
    re.compile(
        r"(?P<term>[\w\u4e00-\u9fa5\-]{2,16})\s*(?:是|是个)?(?:什么意思|是什么梗|什么梗|啥意思)"
        r"\s*[，,、]?\s*(?:我|这个)?\s*(?:也|其实|真)?\s*"
        r"(?:不太?清楚|不太?懂|不知道|不清楚|没听过|没听(?:说)?过)"
    ),
    re.compile(
        r"[「『“\"'](?P<term>[\w\u4e00-\u9fa5\-]{2,16})[」』”\"']\s*[，,]?\s*(?:我)?\s*(?:也|其实|真)?\s*"
        r"(?:不太?懂|没听懂|没听(?:说)?过|不知道|不清楚)"
    ),
)

_IGNORANCE_STOP_TERMS = frozenset({"这个", "那个", "什么", "啥", "意思", "梗", "这个梗", "你", "我", "他", "她"})
_IGNORANCE_BAD_PARTS = ("什么", "啥", "意思", "梗是", "是什")


def _clean(text: Any, limit: int = 400) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    return raw[:limit]


def _now_ts() -> float:
    return datetime.now().timestamp()


def _safe_score(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _site_list(raw: Any) -> list[tuple[str, str, str]]:
    wanted = {item.strip().lower() for item in re.split(r"[,，\s]+", str(raw or "")) if item.strip()}
    if not wanted:
        wanted = {"bilibili", "cnmeme", "gengbaike"}
    sites: list[tuple[str, str, str]] = []
    for key, domain, label in _DEFAULT_SITES:
        if key in wanted or domain in wanted or label in wanted:
            sites.append((key, domain, label))
    return sites or list(_DEFAULT_SITES)


class MemeLookupMixin:
    """梗速查能力（DeepSeek search/fetch + B 站优先 + 梗 wiki 兜底）。"""

    # ---------------------------------------------------------------- 配置读取

    def _meme_lookup_enabled(self) -> bool:
        return bool(getattr(self, "enable_meme_web_lookup", True))

    def _meme_lookup_sites(self) -> list[tuple[str, str, str]]:
        return _site_list(getattr(self, "meme_lookup_sites", "bilibili,cnmeme,gengbaike"))

    def _meme_lookup_summary_chars(self) -> int:
        try:
            return max(80, min(800, int(getattr(self, "meme_lookup_summary_chars", 260) or 260)))
        except (TypeError, ValueError):
            return 260

    def _meme_lookup_provider_settings(self, umo: str = "") -> dict[str, Any]:
        getter = getattr(self, "_astrbot_web_search_provider_settings", None)
        if callable(getter):
            try:
                settings = getter(umo)
                if isinstance(settings, dict):
                    return settings
            except Exception:
                pass
        try:
            cfg = self.context.get_config(umo=umo) if umo else self.context.get_config()
        except Exception:
            try:
                cfg = self.context.get_config()
            except Exception:
                cfg = {}
        settings = cfg.get("provider_settings", {}) if isinstance(cfg, dict) else {}
        return dict(settings or {}) if isinstance(settings, dict) else {}

    def _meme_lookup_keys(self, umo: str = "") -> list[str]:
        settings = self._meme_lookup_provider_settings(umo)
        raw = settings.get("websearch_deepseek_key")
        if isinstance(raw, str):
            raw = [raw]
        keys = [_clean(item, 200) for item in (raw or []) if _clean(item, 200)]
        return keys

    def _meme_lookup_available(self, umo: str = "") -> bool:
        return bool(self._meme_lookup_keys(umo))

    # ------------------------------------------------------------ DeepSeek 调用

    async def _deepseek_tool_call(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        umo: str = "",
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        keys = self._meme_lookup_keys(umo)
        if not keys:
            raise RuntimeError("DeepSeek 联网接口未配置 API Key")
        last_error: Exception | None = None
        for key in keys:
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            }
            try:
                async with aiohttp.ClientSession(trust_env=True) as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response:
                        body = await response.text()
                        if response.status == 200:
                            data = json.loads(body or "{}")
                            return data if isinstance(data, dict) else {}
                        reason = _clean(body, 240)
                        if response.status in {401, 402, 403}:
                            last_error = RuntimeError(f"DeepSeek 接口拒绝 Key: status={response.status} {reason}")
                            continue
                        raise RuntimeError(f"DeepSeek 接口失败: status={response.status} {reason}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 网络类错误换 Key 也无意义，直接记录后重试一次下一个 Key
                last_error = exc
                continue
        raise RuntimeError(str(last_error or "DeepSeek 接口不可用"))

    async def _deepseek_search(
        self,
        query: str,
        *,
        count: int = 5,
        need_content: bool = False,
        ori_question: str | None = None,
        umo: str = "",
        timeout: float = 20.0,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": _clean(query, 300),
            "count": max(1, min(50, int(count or 5))),
            "need_content": bool(need_content),
        }
        if ori_question:
            payoff = _clean(ori_question, 128)
            if payoff:
                payload["ori_question"] = payoff
        data = await self._deepseek_tool_call(_DEEPSEEK_SEARCH_URL, payload, umo=umo, timeout=timeout)
        results: list[dict[str, Any]] = []
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            snippet = item.get("summary") or item.get("snippet") or ""
            if need_content and item.get("content"):
                snippet = "\n\n".join(part for part in (snippet, item.get("content")) if part)
            results.append(
                {
                    "title": _clean(item.get("title"), 200),
                    "url": _clean(item.get("url"), 600),
                    "snippet": _clean(snippet, 2000),
                    "content": _clean(item.get("content"), 4000),
                    "site_name": _clean(item.get("site_name"), 80),
                    "rerank_score": _safe_score(item.get("rerank_score")),
                    "authority": _safe_score(item.get("authority")),
                }
            )
        return results

    async def _deepseek_fetch(self, url: str, *, umo: str = "", timeout: float = 20.0) -> dict[str, Any]:
        parsed = urlsplit(_clean(url, 2000))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return {}
        data = await self._deepseek_tool_call(
            _DEEPSEEK_FETCH_URL,
            {"url": _clean(url, 2000)},
            umo=umo,
            timeout=timeout,
        )
        return {"title": _clean(data.get("title"), 200), "content": _clean(data.get("content"), 6000)}

    # ---------------------------------------------------------------- 结果判断

    @staticmethod
    def _meme_result_score(term: str, item: dict[str, Any]) -> int:
        text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
        lowered = text.casefold()
        score = 0
        if term and term.casefold() in lowered:
            score += 2
        if any(hint in lowered for hint in _MEANING_HINTS):
            score += 2
        if term and term.casefold() in str(item.get("url") or "").casefold():
            score += 1
        if len(text) >= 40:
            score += 1
        return score

    def _meme_pick_result(self, term: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        for item in results:
            # DeepSeek search 自带 rerank_score(0~3)，≥1.5 视为优质；本地信号只做辅助。
            score = float(_safe_score(item.get("rerank_score"))) + self._meme_result_score(term, item) * 0.35
            if best is None or score > best[0]:
                best = (score, item)
        if not best or best[0] < 1.5:
            return None
        return best[1]

    def _meme_build_summary(self, term: str, item: dict[str, Any]) -> str:
        text = _clean(f"{item.get('title') or ''} {item.get('snippet') or ''}", 1200)
        if term and term not in text:
            text = f"{term}：{text}"
        return _clean(text, self._meme_lookup_summary_chars())

    # ---------------------------------------------------------------- 单点速查

    def _meme_cache(self) -> dict[str, Any]:
        try:
            state = self.data.setdefault("meme_lookup_cache", {})
        except Exception:
            return {}
        return state if isinstance(state, dict) else {}

    async def _meme_lookup_term(
        self,
        term: str,
        *,
        umo: str = "",
        timeout: float = 22.0,
        ori_question: str = "",
    ) -> dict[str, Any]:
        """按 bilibili → 梗wiki → 梗百科 的顺序查一个梗。"""

        cleaned = _TERM_PATTERN.sub("", _clean(term, 20)).strip()
        result: dict[str, Any] = {
            "term": cleaned,
            "found": False,
            "site": "",
            "title": "",
            "url": "",
            "summary": "",
            "query": "",
            "steps": [],
        }
        if not cleaned or len(cleaned) < 2 or not self._meme_lookup_enabled():
            return result
        cached = self._meme_cache().get(cleaned)
        if isinstance(cached, dict) and _now_ts() - float(cached.get("at") or 0) < 3 * 24 * 3600:
            if cached.get("summary"):
                result.update(
                    {
                        "found": True,
                        "site": str(cached.get("site") or ""),
                        "title": str(cached.get("title") or ""),
                        "url": str(cached.get("url") or ""),
                        "summary": str(cached.get("summary") or ""),
                        "query": str(cached.get("query") or ""),
                        "steps": [_clean(s, 120) for s in (cached.get("steps") or []) if s],
                        "cached": True,
                    }
                )
                return result
        for key, domain, label in self._meme_lookup_sites():
            query = f'{cleaned} 是什么意思 site:{domain}'
            try:
                results = await self._deepseek_search(
                    query,
                    count=5,
                    ori_question=_clean(ori_question or f"{cleaned} 是什么意思", 128),
                    umo=umo,
                    timeout=timeout,
                )
            except Exception as exc:
                logger.debug("[MemeLookup] 梗速查搜索失败: term=%s site=%s err=%s", cleaned, domain, _clean(exc, 160))
                result["steps"].append(f"{label}: 搜索失败")
                continue
            picked = self._meme_pick_result(cleaned, results)
            result["steps"].append(f"{label}: {len(results)} 条命中，{'采用' if picked else '不采用'}")
            if not picked:
                continue
            summary = self._meme_build_summary(cleaned, picked)
            # 梗站正文更详细，摘要太短时抓一次页面正文补足。
            if key != "bilibili" and len(summary) < 60 and picked.get("url"):
                try:
                    page = await self._deepseek_fetch(str(picked.get("url")), umo=umo, timeout=timeout)
                    content = _clean(page.get("content"), 1200)
                    if content:
                        summary = _clean(content, self._meme_lookup_summary_chars())
                except Exception as exc:
                    logger.debug("[MemeLookup] 梗页抓取失败: url=%s err=%s", picked.get("url"), _clean(exc, 160))
            result.update(
                {
                    "found": bool(summary),
                    "site": label,
                    "title": _clean(picked.get("title"), 160),
                    "url": _clean(picked.get("url"), 600),
                    "summary": summary,
                    "query": query,
                }
            )
            if result["found"]:
                cache = self._meme_cache()
                if cache is not None:
                    cache[cleaned] = {
                        "site": label,
                        "title": result["title"],
                        "url": result["url"],
                        "summary": summary,
                        "query": query,
                        "steps": list(result["steps"]),
                        "at": _now_ts(),
                    }
                    keys = sorted(cache, key=lambda k: float((cache.get(k) or {}).get("at") or 0))
                    for stale in keys[:-120]:
                        cache.pop(stale, None)
                logger.info(
                    "[PrivateCompanion] 梗速查命中: term=%s site=%s query=%s url=%s",
                    cleaned,
                    label,
                    query,
                    result["url"],
                )
                return result
        logger.info("[PrivateCompanion] 梗速查未命中: term=%s steps=%s", cleaned, "/".join(result["steps"]))
        return result

    # -------------------------------------------------------------- 批量与格式化

    async def _meme_lookup_evidence(
        self,
        terms: list[str],
        *,
        limit: int = 2,
        umo: str = "",
        purpose: str = "group_slang",
    ) -> str:
        """把若干梗查成可以直接塞进提示词的短证据。"""

        if not self._meme_lookup_enabled() or not self._meme_lookup_available(umo):
            return ""
        lines: list[str] = []
        for term in terms:
            if len(lines) >= max(1, limit):
                break
            info = await self._meme_lookup_term(term, umo=umo)
            if not info.get("found"):
                continue
            lines.append(
                "- {term}（来源 {site}）：{summary}{url}".format(
                    term=info.get("term") or term,
                    site=info.get("site") or "网络",
                    summary=info.get("summary") or "",
                    url=f"（{info.get('url')}）" if info.get("url") else "",
                )
            )
        if lines:
            logger.info("[PrivateCompanion] 梗速查证据已收集: purpose=%s count=%s", purpose, len(lines))
        return "\n".join(lines)[:1800]

    def _meme_terms_from_query(self, query: str) -> list[str]:
        """从「XX是什么意思」这类查询里抽出梗词。

        先定位触发短语，再往左取最近的一个词块（允许中间夹「到底/究竟/那个」等
        连接词），避免 ``city不city 到底什么意思`` 只抽到一个「底」。
        """

        text = _clean(query, 200)
        if not text:
            return []
        if not any(key in text for key in ("梗", "什么意思", "啥意思", "网络用语", "怎么来的", "什么来头")):
            return []
        terms: list[str] = []
        for match in _QUERY_TRIGGER_PATTERN.finditer(text):
            head = text[max(0, match.start() - 24) : match.start()]
            head = _QUERY_TAIL_NOISE.sub("", head)
            tokens = re.findall(r"[\w\u4e00-\u9fa5\-]+", head)
            if not tokens:
                continue
            candidate = _TERM_PATTERN.sub("", tokens[-1]).strip()
            if 2 <= len(candidate) <= 16 and candidate not in _QUERY_STOP_TERMS and candidate not in terms:
                terms.append(candidate)
        return terms[:3]

    async def _meme_lookup_search_results(self, term: str, *, umo: str = "") -> list[dict[str, Any]]:
        """把速查结果转成与 ``_run_astrbot_web_search`` 相同的结构。"""

        info = await self._meme_lookup_term(term, umo=umo)
        if not info.get("found"):
            return []
        key = _clean(f"meme|{info.get('term')}|{info.get('url')}", 200)
        return [
            {
                "key": key,
                "title": info.get("title") or f"{info.get('term')} 是什么意思",
                "url": info.get("url") or "",
                "snippet": info.get("summary") or "",
                "provider": "meme_lookup",
                "site": info.get("site") or "",
            }
        ]

    # ------------------------------------------------ 回复里「不认识这个梗」修复

    def _meme_ignorance_terms(self, text: str) -> list[str]:
        """从模型自己的回复里找出「我不认识这个词」所指向的梗词。

        只认带明确词面的三种句式，避免把「不知道你在说什么」误判成要联网：
        1. 不知道「XX」是什么意思
        2. XX 是什么意思，我也不太清楚
        3. 「XX」我不太懂 / 没听过
        """

        body = _clean(text, 4000)
        if not body:
            return []
        terms: list[str] = []
        for pattern in _IGNORANCE_PATTERNS:
            for match in pattern.finditer(body):
                candidate = _TERM_PATTERN.sub("", match.group("term") or "").strip()
                candidate = candidate.strip("的了呢吧啊呀")
                if not (2 <= len(candidate) <= 16):
                    continue
                if candidate.endswith("是") or any(part in candidate for part in _IGNORANCE_BAD_PARTS):
                    continue
                if candidate in _IGNORANCE_STOP_TERMS or candidate in terms:
                    continue
                terms.append(candidate)
        return terms

    def _meme_strip_ignorance(self, text: str, terms: list[str]) -> str:
        """把声称「不认识这个梗」的那句从回复里摘掉，保留其余内容。"""

        body = _clean(text, 4000)
        if not body:
            return ""
        kept: list[str] = []
        for sentence in re.split(r"(?<=[。！？!?~…])\s*|\n+", body):
            piece = sentence.strip()
            if not piece:
                continue
            hit = any(term in piece for term in terms) and any(
                pattern.search(piece) for pattern in _IGNORANCE_PATTERNS
            )
            if hit:
                continue
            kept.append(piece)
        return "".join(kept).strip()

    def _meme_repair_system_suffix(self, evidence: str, stripped: str) -> str:
        lines = [
            "",
            "【临时补充】你上一条回复里说了自己不认识某个网络用语。下面是刚查到的释义，",
            "请直接用它把话接下去，不要再说「不知道/没听过/不太懂」，也不要提「我查了资料」「搜索了一下」。",
            "保持原本的人设、口语和长度（不超过三句），只重写这一次回复。",
        ]
        if stripped:
            lines.append(f"已经写好的部分：{stripped}")
        lines.append("释义：")
        lines.append(evidence)
        return "\n".join(lines)

    async def _meme_regenerate_reply(
        self,
        event: Any,
        resp: Any,
        evidence: str,
        stripped: str,
    ) -> str:
        """带着释义重新生成一次回复。"""

        request = None
        getter = getattr(self, "_model_replacement_event_extra", None)
        if callable(getter):
            try:
                request = getter(event, "provider_request", None)
            except Exception:
                request = None
        if request is None:
            request = getattr(event, "private_companion_model_replacement_request", None)
        provider_id = ""
        getter_provider = getattr(self, "_provider_id_from_llm_response", None)
        if callable(getter_provider):
            try:
                provider_id = str(getter_provider(resp) or "")
            except Exception:
                provider_id = ""
        base_system = ""
        if request is not None:
            base_system = str(getattr(request, "system_prompt", None) or "")
        prompt = ""
        if request is not None:
            prompt = str(getattr(request, "prompt", None) or "")
        if not prompt:
            prompt = str(getattr(event, "message_str", "") or "")
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "system_prompt": (base_system + self._meme_repair_system_suffix(evidence, stripped))[:20000],
            "contexts": getattr(request, "contexts", None) if request is not None else None,
            "image_urls": list(getattr(request, "image_urls", None) or []) if request is not None else None,
            "audio_urls": list(getattr(request, "audio_urls", None) or []) if request is not None else None,
        }
        if provider_id:
            kwargs["chat_provider_id"] = provider_id
        result = await self.context.llm_generate(**kwargs)
        return str(getattr(result, "completion_text", "") or "").strip()

    async def _repair_unknown_meme_response(self, event: Any, resp: Any) -> None:
        """回复里出现「我不认识 XX」时：截断 → 联网速查 → 带着释义重写。

        只重写一次（``event._meme_reply_repair_done`` 守卫），查不到就原样保留，
        绝不因为查询失败把正常回复吞掉。
        """

        if resp is None:
            return
        if not bool(getattr(self, "enabled", True)):
            return
        if not bool(getattr(self, "enable_meme_reply_repair", True)):
            return
        if not self._meme_lookup_enabled():
            return
        if bool(getattr(event, "_meme_reply_repair_done", False)):
            return
        text = str(getattr(resp, "completion_text", "") or "").strip()
        if not text or len(text) > 4000:
            return
        terms = self._meme_ignorance_terms(text)
        if not terms:
            return
        try:
            setattr(event, "_meme_reply_repair_done", True)
        except Exception:
            return
        max_terms = 2
        try:
            max_terms = max(1, min(3, int(getattr(self, "meme_reply_repair_max_terms", 2) or 2)))
        except (TypeError, ValueError):
            max_terms = 2
        terms = terms[:max_terms]
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        evidence = ""
        try:
            evidence = await asyncio.wait_for(
                self._meme_lookup_evidence(terms, limit=2, umo=umo, purpose="reply_repair"),
                timeout=25.0,
            )
        except asyncio.TimeoutError:
            logger.warning("梗速查重写跳过：查询超时 terms=%s", "/".join(terms))
            return
        except Exception as exc:
            logger.debug("梗速查重写跳过：%s", _clean(exc, 160))
            return
        if not evidence:
            logger.info("梗速查重写跳过：没查到释义 terms=%s", "/".join(terms))
            return
        stripped = self._meme_strip_ignorance(text, terms)
        rewritten = ""
        try:
            rewritten = await self._meme_regenerate_reply(event, resp, evidence, stripped)
        except Exception as exc:
            logger.warning("梗速查重写失败，保留原回复: %s", _clean(exc, 160))
            rewritten = ""
        if rewritten:
            rewritten = re.sub(r"\s*\n+\s*", " ", rewritten).strip()
        if rewritten and not self._meme_ignorance_terms(rewritten):
            resp.completion_text = rewritten
            logger.info(
                "回复里不认识这个梗，已联网查完并重写: terms=%s before=%s after=%s",
                "/".join(terms),
                _clean(text, 120),
                _clean(rewritten, 160),
            )
            return
        if stripped and stripped != text and not self._meme_ignorance_terms(stripped):
            resp.completion_text = stripped
            logger.info(
                "梗速查只截断未重写: terms=%s before=%s after=%s",
                "/".join(terms),
                _clean(text, 120),
                _clean(stripped, 160),
            )
            return
        logger.info("梗速查重写未采用，保留原回复 terms=%s", "/".join(terms))
