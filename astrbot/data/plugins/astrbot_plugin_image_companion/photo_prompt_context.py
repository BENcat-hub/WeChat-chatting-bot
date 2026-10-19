from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from . import photo_wardrobe_decision as _wardrobe_rules


_SANITIZER_VERSION = 3
_SECTION_SOURCES = frozenset(
    {
        "user_request",
        "visual_memory",
        "scene_context",
        "preset",
        "fixed_prompt",
        "recent_continuity",
        "wardrobe_decision",
        "wardrobe_structure",
        "managed_reference",
        "reference_fallback",
        "composition",
        "edit_contract",
    }
)
_SELFIE_WORKFLOWS = frozenset({"selfie", "portrait", "自拍", "人像"})
_EDIT_WORKFLOWS = frozenset({"edit", "改图", "修图", "重绘", "p图"})
_GENERIC_WARDROBE_PATTERN = re.compile(
    r"衣服|服装|衣着|穿搭|配饰|服饰|着装|原来的衣服|参考图中的衣服|"
    r"\b(?:clothes|clothing|outfit|wardrobe|attire|garment|accessor(?:y|ies))\b",
    flags=re.I,
)
_DAILY_OUTFIT_PATTERN = re.compile(
    r"(?:今日穿搭|当天穿搭|日常穿搭|today'?s outfit|daily outfit)",
    flags=re.I,
)
_RECENT_OUTFIT_CONTINUITY_PATTERN = re.compile(
    r"(?:,\s*)?(?:(?:preserve\s+)?(?:the\s+)?)?exact\s+outfit\s+and\s+accessories"
    r"|(?:，\s*)?(?:精确保留|保持|延续)?(?:参考图(?:中|里的)?)?(?:完整|原有|相同)?服装(?:和|与)配饰",
    flags=re.I,
)
_SPECIFIC_OUTFIT_ITEM_PATTERN = re.compile(
    r"连衣裙|裙子|短裙|长裙|吊带|衬衫|外套|夹克|西装|制服|汉服|旗袍|和服|洛丽塔|"
    r"裤子|裤|毛衣|卫衣|T恤|背心|上衣|套装|袜子|袜|鞋子|鞋|"
    r"\b(?:dress|skirt|shirt|blouse|coat|jacket|suit|uniform|hoodie|sweater|pants|trousers|shorts|top)\b",
    flags=re.I,
)
_EMBEDDED_NEGATION_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|not|avoid|without|no|exclude|skip|remove)\s+"
    r"|(?:不要|避免|禁止|不许|不得|别(?:再)?)(?:穿|用|使用|选)?\s*",
    flags=re.I,
)
_NEGATIVE_TO_POSITIVE_TRANSITION_PATTERN = re.compile(
    r"\b(?:but|instead|however)\b\s*"
    r"|(?:但|不过|可是|而是)?(?:改穿|换成|换上|换为|改为|要穿|穿上)\s*",
    flags=re.I,
)

__all__ = [
    "PhotoPromptSection",
    "ResolvedPhotoPromptContext",
    "compile_local_photo_prompt",
    "compile_local_photo_negative_prompt",
    "resolve_photo_prompt_context",
]


@dataclass(frozen=True, slots=True)
class PhotoPromptSection:
    name: str
    source: str
    positive: str = ""
    negative: str = ""
    protected: bool = False
    sanitize_conflicts: bool | None = None

    def __post_init__(self) -> None:
        if self.source not in _SECTION_SOURCES:
            raise ValueError(f"unsupported photo prompt section source: {self.source}")


def _section_conflict_sanitization_enabled(section: PhotoPromptSection) -> bool:
    if section.sanitize_conflicts is not None:
        return bool(section.sanitize_conflicts)
    return not section.protected


@dataclass(frozen=True, slots=True)
class ResolvedPhotoPromptContext:
    final_prompt: str
    complete_prompt: str
    prompt_sections: tuple[PhotoPromptSection, ...]
    reference: Any
    detected_conflicts: tuple[dict[str, Any], ...]
    removed_conflicts: tuple[dict[str, Any], ...]
    residual_conflicts: tuple[dict[str, Any], ...]
    reference_removed: dict[str, Any] | None
    sanitizer_version: int = _SANITIZER_VERSION


def _value(subject: Any, name: str, default: Any = None) -> Any:
    if isinstance(subject, Mapping):
        return subject.get(name, default)
    return getattr(subject, name, default)


_CLIP_BOUNDARY_CHARS = frozenset(" \t\r\n,.;:!?，。；：！？")
_CLIP_BOUNDARY_PATTERN = re.compile(r"[\s,.;:!?，。；：！？]+")


def _clip_prefix_at_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    candidate = text[:limit]
    if len(text) <= limit or text[limit] in _CLIP_BOUNDARY_CHARS:
        return candidate.rstrip()
    matches = list(_CLIP_BOUNDARY_PATTERN.finditer(candidate))
    return candidate[: matches[-1].end()].rstrip() if matches else ""


def _clip_tail_at_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    start = max(0, len(text) - limit)
    candidate = text[start:]
    if start == 0 or text[start - 1] in _CLIP_BOUNDARY_CHARS:
        return candidate.lstrip()
    match = _CLIP_BOUNDARY_PATTERN.search(candidate)
    return candidate[match.end():].lstrip() if match else ""


def _clip(value: Any, limit: int, *, preserve_tail: bool = False) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit <= 0 or len(text) <= limit:
        return text
    marker = " ... [section compacted] ... "
    if not preserve_tail or limit <= len(marker) + 40:
        return _clip_prefix_at_boundary(text, limit)
    available = limit - len(marker)
    head_size = max(20, int(available * 0.62))
    tail_size = max(20, available - head_size)
    head = _clip_prefix_at_boundary(text, head_size)
    tail = _clip_tail_at_boundary(text, tail_size)
    if head and tail:
        return f"{head}{marker}{tail}"
    return head or tail


def _categories(value: Any) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            category
            for category, _start, _end, _matched in _wardrobe_rules._outfit_category_matches(value)
        )
    )


def _compatible(category: str, active_category: str) -> bool:
    return category == active_category


def _split_embedded_polarity(text: str) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw or not _EMBEDDED_NEGATION_PATTERN.search(raw):
        return raw, ""

    positive_parts: list[str] = []
    negative_parts: list[str] = []
    sentences = re.split(r"[;；.!?。！？]+", raw)
    for sentence in sentences:
        polarity = "positive"
        for comma_part in re.split(r"[,，]+", sentence):
            remaining = comma_part.strip()
            while remaining:
                if polarity == "positive":
                    marker = _EMBEDDED_NEGATION_PATTERN.search(remaining)
                    if marker is None:
                        positive_parts.append(remaining)
                        break
                    before = remaining[:marker.start()].strip()
                    if before:
                        positive_parts.append(before)
                    remaining = remaining[marker.end():].strip()
                    polarity = "negative"
                    continue

                transition = _NEGATIVE_TO_POSITIVE_TRANSITION_PATTERN.search(remaining)
                if transition is None:
                    negative_parts.append(remaining)
                    break
                before = remaining[:transition.start()].strip()
                if before:
                    negative_parts.append(before)
                remaining = remaining[transition.end():].strip()
                polarity = "positive"

    return ", ".join(positive_parts), ", ".join(negative_parts)


def _excluded_outfit_terms(wardrobe: Any) -> tuple[str, ...]:
    raw = _wardrobe_rules._clean_text(_value(wardrobe, "excluded_outfit_text", ""), 1200)
    terms: list[str] = []
    for clause in re.split(r"(?:\r?\n+|[；;，,。]+)", raw):
        cleaned = _wardrobe_rules._clean_text(clause, 240).strip(" ,.;；。，")
        if not cleaned:
            continue
        _negative, content = _wardrobe_rules._negative_clause_content(cleaned)
        term = _wardrobe_rules._clean_text(content or cleaned, 160).lower()
        if term:
            terms.append(term)
    return tuple(dict.fromkeys(terms))


def _specific_outfit_items(value: Any) -> frozenset[str]:
    normalized = _wardrobe_rules._clean_text(value, 1200).lower()
    return frozenset(match.group(0).lower() for match in _SPECIFIC_OUTFIT_ITEM_PATTERN.finditer(normalized))


def _generic_wardrobe_is_compatible(text: str, active_category: str) -> bool:
    normalized = _wardrobe_rules._clean_text(text, 4000).lower()
    if re.search(
        r"\b(?:one|single)\s+(?:single\s+)?coherent\s+outfit\b"
        r"|\bthe\s+same\s+outfit\b"
        r"|\b(?:requested|authoritative|resolved)\s+(?:wardrobe|outfit)\b"
        r"|\b(?:current|selected|established|stable|consistent)\s+(?:wardrobe|outfit|attire)\b"
        r"|\bwardrobe\s+(?:decision|ruling)\b"
        r"|\bconflicting\s+(?:schedule\s+location\s+or\s+)?wardrobe\b",
        normalized,
        flags=re.I,
    ):
        return True
    return active_category == "reference_outfit" and bool(
        re.search(r"\bexact\s+outfit\s+and\s+accessories\b", normalized, flags=re.I)
    )


def _preview(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"(?i)data:image/[^;\s]+;base64,[a-z0-9+/=]+", "[image-data]", text)
    text = re.sub(
        r"(?i)(?:[a-z]:\\[^,;\r\n]+|/(?:[^/\r\n,;]+/)+[^,;\r\n]*)",
        "[path]",
        text,
    )
    return text[:120]


def _audit(
    section: PhotoPromptSection,
    *,
    rule: str,
    category: str,
    action: str,
    text: str,
) -> dict[str, Any]:
    raw = str(text or "")
    return {
        "source": section.source,
        "section": section.name,
        "rule": rule,
        "category": category,
        "action": action,
        "preview": _preview(raw),
        "sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest(),
    }


def _positive_conflict(
    text: str,
    *,
    active_category: str,
    authoritative: bool,
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> tuple[str, str] | None:
    categories = _categories(text)
    daily = bool(_DAILY_OUTFIT_PATTERN.search(text))
    if authoritative:
        if daily and active_category != "daily_outfit":
            return "daily_outfit_conflict", "daily_outfit"
        incompatible = next(
            (category for category in categories if not _compatible(category, active_category)),
            "",
        )
        if incompatible:
            return "incompatible_wardrobe", incompatible
        if (
            not categories
            and not _generic_wardrobe_is_compatible(text, active_category)
            and (
                _wardrobe_rules._contains_specific_outfit_text(text)
                or _GENERIC_WARDROBE_PATTERN.search(text)
            )
        ):
            return "unverified_wardrobe", "unknown"
    excluded = next((category for category in categories if category in excluded_categories), "")
    if excluded:
        return "excluded_wardrobe", excluded
    normalized = _wardrobe_rules._clean_text(text, 4000).lower()
    if next((term for term in excluded_outfit_terms if term in normalized), ""):
        return "excluded_outfit_item", "specific_outfit"
    return None


def _negative_conflict(
    text: str,
    *,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
) -> tuple[str, str] | None:
    if not authoritative:
        return None
    categories = _categories(text)
    denied = next((category for category in categories if _compatible(category, active_category)), "")
    if denied:
        return "authoritative_wardrobe_negated", denied
    denied_item = next(
        (item for item in _specific_outfit_items(text) if item in authoritative_items),
        "",
    )
    if denied_item:
        return "authoritative_outfit_item_negated", denied_item
    return None


def _split_clauses(text: str) -> tuple[list[str], str]:
    raw = str(text or "").strip()
    if not raw:
        return [], "；"
    structural = [
        item.strip()
        for item in re.split(r"(?:\r?\n+|[；;]+|(?<=[.!?。！？])\s+)", raw)
        if item.strip()
    ]
    if len(structural) > 1:
        return structural, "；"
    comma_parts = [item.strip() for item in re.split(r"[,，]+", raw) if item.strip()]
    if len(comma_parts) > 1:
        return comma_parts, ", "
    return [raw], "；"


def _sanitize_field(
    section: PhotoPromptSection,
    text: str,
    *,
    negative: bool,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not text:
        return "", [], []
    clauses, separator = _split_clauses(text)
    conflicts: list[dict[str, Any]] = []
    removals: list[dict[str, Any]] = []
    kept: list[str] = []
    for clause in clauses:
        conflict = (
            _negative_conflict(
                clause,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
            )
            if negative
            else _positive_conflict(
                clause,
                active_category=active_category,
                authoritative=authoritative,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
        if conflict is None:
            kept.append(clause)
            continue
        rule, category = conflict
        conflicts.append(
            _audit(section, rule=rule, category=category, action="detected", text=clause)
        )
        action = (
            "section_dropped"
            if len(clauses) == 1 and rule == "unverified_wardrobe"
            else "clause_removed"
        )
        removals.append(
            _audit(section, rule=rule, category=category, action=action, text=clause)
        )
    return separator.join(kept), conflicts, removals


def _conflicts_in_section(
    section: PhotoPromptSection,
    *,
    active_category: str,
    authoritative: bool,
    authoritative_items: frozenset[str],
    excluded_categories: frozenset[str],
    excluded_outfit_terms: tuple[str, ...],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for text, negative in ((section.positive, False), (section.negative, True)):
        conflict = (
            _negative_conflict(
                text,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
            )
            if negative
            else _positive_conflict(
                text,
                active_category=active_category,
                authoritative=authoritative,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
        if conflict:
            rule, category = conflict
            found.append(
                _audit(section, rule=rule, category=category, action="residual", text=text)
            )
    return found


def _sanitize_sections(
    sections: Sequence[PhotoPromptSection],
    wardrobe: Any,
) -> tuple[
    list[PhotoPromptSection],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    excluded_categories = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    excluded_outfit_terms = _excluded_outfit_terms(wardrobe)
    authoritative_items = _specific_outfit_items(_value(wardrobe, "requested_outfit_text", ""))
    sanitized: list[PhotoPromptSection] = []
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    effective_roles = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "effective_reference_roles", ()) or ())
        if str(item or "").strip()
    )
    for section in sections:
        if not isinstance(section, PhotoPromptSection):
            raise TypeError("sections must contain PhotoPromptSection values")
        if (
            not _section_conflict_sanitization_enabled(section)
            or section.source in {"user_request", "wardrobe_decision"}
        ):
            sanitized.append(section)
            continue
        if section.name != "global_fixed_prompt":
            positive_text, embedded_negative = _split_embedded_polarity(section.positive)
            if embedded_negative:
                section = replace(
                    section,
                    positive=positive_text,
                    negative=", ".join(
                        part
                        for part in (section.negative.strip(), embedded_negative)
                        if part
                    ),
                )
        if section.source == "recent_continuity" and "outfit" not in effective_roles:
            match = _RECENT_OUTFIT_CONTINUITY_PATTERN.search(section.positive)
            if match:
                detected.append(
                    _audit(
                        section,
                        rule="inactive_reference_outfit_role",
                        category="reference_outfit",
                        action="detected",
                        text=match.group(0),
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="inactive_reference_outfit_role",
                        category="reference_outfit",
                        action="clause_rewritten",
                        text=match.group(0),
                    )
                )
                rewritten = _RECENT_OUTFIT_CONTINUITY_PATTERN.sub("", section.positive)
                rewritten = re.sub(r"\s+,", ",", rewritten)
                rewritten = re.sub(r",\s*([.;。；])", r"\1", rewritten)
                section = replace(section, positive=rewritten.strip())
        positive, positive_found, positive_removed = _sanitize_field(
            section,
            section.positive,
            negative=False,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        negative, negative_found, negative_removed = _sanitize_field(
            section,
            section.negative,
            negative=True,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        detected.extend((*positive_found, *negative_found))
        removed.extend((*positive_removed, *negative_removed))
        if any(
            item["action"] == "section_dropped"
            for item in (*positive_removed, *negative_removed)
        ):
            positive = ""
            negative = ""
        sanitized.append(replace(section, positive=positive, negative=negative))

    residual: list[dict[str, Any]] = []
    for index, section in enumerate(sanitized):
        if (
            not _section_conflict_sanitization_enabled(section)
            or section.source in {"user_request", "wardrobe_decision"}
        ):
            continue
        found = _conflicts_in_section(
            section,
            active_category=active_category,
            authoritative=authoritative,
            authoritative_items=authoritative_items,
            excluded_categories=excluded_categories,
            excluded_outfit_terms=excluded_outfit_terms,
        )
        if not found:
            continue
        detected.extend(found)
        for conflict in found:
            removed.append(
                {
                    **conflict,
                    "action": "section_dropped",
                }
            )
        sanitized[index] = replace(section, positive="", negative="")

    for section in sanitized:
        if (
            not _section_conflict_sanitization_enabled(section)
            or section.source in {"user_request", "wardrobe_decision"}
        ):
            continue
        residual.extend(
            _conflicts_in_section(
                section,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
    return sanitized, detected, removed, residual


def _scan_residual_conflicts(
    sections: Sequence[PhotoPromptSection],
    wardrobe: Any,
) -> list[dict[str, Any]]:
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    authoritative_items = _specific_outfit_items(_value(wardrobe, "requested_outfit_text", ""))
    excluded_categories = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    excluded_outfit_terms = _excluded_outfit_terms(wardrobe)
    residual: list[dict[str, Any]] = []
    for section in sections:
        if (
            not _section_conflict_sanitization_enabled(section)
            or section.source in {"user_request", "wardrobe_decision"}
        ):
            continue
        residual.extend(
            _conflicts_in_section(
                section,
                active_category=active_category,
                authoritative=authoritative,
                authoritative_items=authoritative_items,
                excluded_categories=excluded_categories,
                excluded_outfit_terms=excluded_outfit_terms,
            )
        )
    return residual


def _apply_budget(
    sections: list[PhotoPromptSection],
    indexes: list[int],
    budget: int,
    *,
    field: str = "positive",
) -> None:
    remaining = budget
    for index in indexes:
        section = sections[index]
        if section.protected or section.name == "global_fixed_prompt":
            continue
        current = getattr(section, field)
        clipped = _clip(current, remaining, preserve_tail=True) if remaining > 0 else ""
        sections[index] = replace(section, **{field: clipped})
        remaining -= len(clipped)
        if clipped and remaining > 0:
            remaining -= 1


def _budget_sections(sections: list[PhotoPromptSection]) -> list[PhotoPromptSection]:
    result = list(sections)
    indexes = lambda *sources: [
        index for index, section in enumerate(result) if section.source in sources
    ]
    _apply_budget(result, indexes("wardrobe_decision"), 420)
    _apply_budget(result, indexes("reference_fallback"), 320)
    _apply_budget(result, indexes("scene_context", "visual_memory"), 700)
    _apply_budget(result, indexes("preset"), 140)
    _apply_budget(result, indexes("fixed_prompt"), 100)

    for index in indexes("recent_continuity"):
        section = result[index]
        if section.protected:
            continue
        limit = 460 if section.positive.startswith("Recent-photo continuity:") else 280
        result[index] = replace(
            section,
            positive=_clip(section.positive, limit, preserve_tail=True),
        )
    _apply_budget(
        result,
        indexes("edit_contract", "composition", "recent_continuity"),
        680,
    )
    _apply_budget(
        result,
        [
            index
            for index, section in enumerate(result)
            if section.source not in {"user_request", "composition"}
        ],
        230,
        field="negative",
    )
    return result


def _join_field(
    sections: Sequence[PhotoPromptSection],
    sources: frozenset[str],
    field: str = "positive",
) -> str:
    return "\n".join(
        value
        for section in sections
        if section.source in sources
        for value in (getattr(section, field).strip(),)
        if value
    )


# Prompt budget policy (docs/IMAGE-QUALITY-FINDINGS.md): the upstream seedream edit API
# rejects prompts longer than 800 characters, and the local jimeng server keeps the head
# and the tail when it has to clamp. Production prompts ran 4000+ characters, so the
# head+tail clamp silently dropped the identity contract and the pose override sitting in
# the middle. Assemble the final prompt inside the budget here instead: the requirements
# that must never be lost go last, the scene description goes first.
_PROMPT_CHAR_BUDGET = 786
# 786 而不是 790：拼装时 head / critical / Avoid 三段之间插入 `\n\n`（每处比空格多 1 字符），
# 而生产日志实测最终 prompt 比本函数返回值多 4 个字符；留出余量后仍稳定 < 800（上游硬上限）。
_TAIL_NEGATIVE_BUDGET = 260
# A/B (2026-10-16, hands experiment n=3 per arm, blind VL scoring): naming the concrete
# hand failures here raised the hands axis 6.67 -> 7.0 and anatomy 5.67 -> 7.33
# (total 45.0 -> 49.33). These go last so the tail-preserving clip keeps them.
# 顺序 = 被裁掉的优先级（`preserve_tail=True` 从左侧开始丢）：先丢 chibi/比例类，
# 再丢手部词，水印词**永远排在最后**。2026-10-16 审计发现 selfie-短 的 Avoid 段被裁到
# 只剩服装+手部负面词，水印词整段消失（近 24 张生产图里 12.5% 出现伪水印叠加）。
_TAIL_NEGATIVES = (
    "chibi, oversized head, short stubby limbs, "
    "long fingers, elongated fingers, oversized hand, claw hand, fused fingers, hidden hands, extra fingers, "
    "weibo watermark, bilibili watermark, social media watermark, username handle, date stamp"
)
_CRITICAL_FRAMINGS = (
    "full-body wide shot",
    "three-quarter shot from the side",
    "medium over-the-shoulder shot",
    "low-angle full-body shot",
    "high-angle medium shot",
    "close upper-body shot with a blurred background",
)
_CRITICAL_POSES = (
    "sitting on the floor with knees tucked",
    "leaning on the sofa arm",
    "walking mid-stride while turning her head back",
    "lying on her stomach propped on her elbows, chin in one hand",
    "standing on tiptoe stretching both arms overhead",
    "sitting sideways on a chair looking at her phone",
    "standing in profile by the window holding the curtain",
    "crouching down to look at something on the floor",
)


def _critical_requirements_tail(
    negative: str,
    head_lower: str = "",
    character_present: bool = True,
    photographic: bool = False,
) -> str:
    """Build the requirement block that must survive the upstream 800-char limit.

    `head_lower` 传入**已裁剪**的头部小写文本时，头部已经写明的要求会被跳过：上游拼装层
    （`image_runtime._apply_photo_generation_prompt_format`）在短请求里会把这些句子留在头部，
    重复一遍既浪费 800 预算，又会让模型同时看到两条措辞不同的身份句。2026-10-16 03:3x 实测
    纯文生图「画一只在窗台上睡觉的橘猫」也被塞进了 `Pose override: … sitting sideways on a
    chair looking at her phone`，就是这一层无条件追加造成的。
    """

    def carried(*markers: str) -> bool:
        return bool(head_lower) and any(marker in head_lower for marker in markers)

    parts: list[str] = []
    if character_present and not carried("about 7 heads tall", "7 heads tall"):
        parts.append("Small head, about 7 heads tall, long legs.")
    if character_present and not carried("light-blue gradient tips", "long blue hair"):
        parts.append("Long blue hair with light-blue gradient tips, blue eyes.")
    if character_present and not carried("whale-fin ears", "whale fin ears"):
        parts.append("Keep the reference face, hair colour, whale-fin ears and whale tail.")
    # 2026-10-16 05:2x A/B（同一次盲评、每条 4 张、1024²、4.0、生产脸部裁切参考图、**生产链路**）：
    # 长版画风句（112 字符，见 git 历史的 "slice-of-life" 版本）把 style 轴从 7.75 抬到 9.0，
    # 但同时把 head_proportion 从 7.0 打到 4.25、anatomy 7.5 → 5.5，总分 **45.0 → 41.5**
    # （4/4 张被报 oversized head / chibi proportions）→ 已回退。短版（51 字符）见 findings §29.3 的对照。
    if character_present:
        if not carried("anime illustration", "cel-shaded", "cel shading"):
            parts.append("2D anime illustration, cel-shaded.")
    elif photographic:
        # 2026-10-18：纯场景的「随手拍」（午饭饭菜、桌面小物）要走手机实拍质感，
        # 不能再被这层塞一句 2D 动画风，否则前面的写实风格要求会被这一句顶掉。
        if not carried("realistic photo", "photographic detail"):
            parts.append("Realistic photo look, natural light, true-to-life texture.")
    # 纯场景且没要求写实时不加任何画风句：给模型留中性描述，避免「拍午饭」变成平涂插画。
    # 头部的机位/姿势句经常被 `head_budget` 裁成半截（只剩 `Pose override:` 甚至 `Pose`），
    # 只按 "pose override" 去重会让最终提示词留下一条没有内容的姿势要求，而尾部那条完整的
    # 又被跳过。必须确认头部同时含 `framing -` 与 `pose -` 才认为它真的带上了姿势。
    pose_in_head = (
        "pose override" in head_lower and "framing -" in head_lower and "pose -" in head_lower
    )
    if character_present and not pose_in_head:
        parts.append(
            "Pose override: framing - "
            + random.choice(_CRITICAL_FRAMINGS)
            + "; pose - "
            + random.choice(_CRITICAL_POSES)
            + "."
        )
    if character_present and not carried("five clear fingers"):
        parts.append("Hands with five clear fingers, not oversized.")
    # 水印句**不做去重**：它是唯一的句子级水印要求，而 `Avoid` 段在超预算时会被从左裁掉。
    # 2026-10-16 审计里 selfie-短 出现过「头部的这句话被裁掉、尾部又被 carried() 跳过」的窗口，
    # 整条提示词不含任何 watermark 字样（同期生产图 12.5% 带伪水印叠加）。宁可贵 40 字符。
    parts.append("No watermark, logo, handle or caption.")
    if negative or _TAIL_NEGATIVES:
        negatives = ", ".join(part for part in (negative, _TAIL_NEGATIVES) if part)
        parts.append(f"Avoid {_clip(negatives, _TAIL_NEGATIVE_BUDGET, preserve_tail=True)}.")
    return " ".join(part for part in parts if part)


def _assemble(sections: Sequence[PhotoPromptSection], prompt_format: str, character_present: bool = True, photographic: bool = False) -> str:
    groups = (
        (
            "User image request",
            _join_field(sections, frozenset({"user_request"})),
        ),
        (
            "Reference and wardrobe ruling",
            _join_field(sections, frozenset({"wardrobe_decision", "wardrobe_structure", "reference_fallback"})),
        ),
        (
            "Scene, style and final preset",
            _join_field(
                sections,
                frozenset({"scene_context", "visual_memory", "preset", "fixed_prompt"}),
            ),
        ),
        (
            "Composition and continuity",
            _join_field(
                sections,
                frozenset({"edit_contract", "composition", "recent_continuity"}),
            ),
        ),
    )
    positive_blocks = [f"[{label}]\n{text}" for label, text in groups if text]
    user_negative = _join_field(sections, frozenset({"user_request"}), "negative")
    decision_negative = "\n".join(
        section.negative.strip()
        for section in sections
        if section.source != "user_request" and section.negative.strip()
    )
    negative = ", ".join(value for value in (decision_negative, user_negative) if value)
    mode = str(prompt_format or "traditional").strip().lower().replace("-", "_")
    if mode in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion"}:
        # NAI mode: avoid [] section labels (down-weight syntax) and express negatives via negative weight.
        prompt = "\n\n".join(f"{label}:\n{text}" for label, text in groups if text)
        return f"{prompt}\n\n-1.5::{negative}::".strip() if negative else prompt.strip()
    if mode in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"}:
        # No [] labels and one flat paragraph: the upstream seedream API documents that
        # prompts should stay under 800 characters and avoid special symbols.
        joined = " ".join(text for _label, text in groups if text)
        tail = _critical_requirements_tail(negative, character_present=character_present, photographic=photographic)
        head_budget = max(200, _PROMPT_CHAR_BUDGET - len(tail))
        head = _clip(joined, head_budget, preserve_tail=False)
        # 头部裁剪之后再比对一次：只有确认这些要求真的活在头部里，尾部才不再重复它们。
        # 短请求（例如「画一只在窗台上睡觉的橘猫」）会把 image_runtime 的身份句留在头部，
        # 于是尾部又追加一遍，模型同时看到两条措辞不同的身份句和一句无关的 Pose override。
        deduped = _critical_requirements_tail(negative, head.lower(), character_present=character_present, photographic=photographic)
        if len(deduped) < len(tail):
            tail = deduped
            head_budget = max(200, _PROMPT_CHAR_BUDGET - len(tail))
            head = _clip(joined, head_budget, preserve_tail=False)
            tail = _critical_requirements_tail(negative, head.lower(), character_present=character_present, photographic=photographic)
        # 「Avoid …」另起一段：整段平铺时，模型容易把否定句当成正向描述来处理；
        # 而且下游（测试与排障页）用 `\n\nAvoid ` 作为「正向 / 负向」的切分标记。
        marker = "Avoid "
        split_at = tail.rfind(marker)
        if split_at > 0:
            critical, avoid_clause = tail[:split_at].rstrip(), tail[split_at:]
            prompt = f"{head}\n\n{critical}\n\n{avoid_clause}".strip()
        elif split_at == 0:
            prompt = f"{head}\n\n{tail}".strip()
        else:
            prompt = f"{head} {tail}".strip()
        if len(prompt) > 800:
            prompt = _clip(prompt, 800, preserve_tail=True)
        return prompt
    prompt = "Positive prompt:\n" + "\n\n".join(positive_blocks)
    if negative:
        prompt += f"\n\nNegative prompt:\n{negative}"
    return prompt.strip()


_LOCAL_VISUAL_PROMPT_SOURCES = frozenset(
    {
        "user_request",
        "visual_memory",
        "scene_context",
        "preset",
        "fixed_prompt",
        "wardrobe_structure",
        "composition",
    }
)


def _local_visual_section_text(section: PhotoPromptSection) -> str:
    """Project a sanitized section into text that an image model may render."""
    text = str(section.positive or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = re.sub(
        r"(?im)^\s*\[(?:User image request|Reference and wardrobe ruling|"
        r"Scene, style and final preset|Composition and continuity)\]\s*$",
        "",
        text,
    )
    if section.source == "user_request":
        text = re.sub(r"^\s*positive\s+prompt\s*:\s*", "", text, flags=re.I)
        text = re.sub(r"^\s*user\s+request\s*:\s*", "", text, flags=re.I)
    elif section.source == "scene_context":
        text = re.sub(r"^\s*resolved\s+selfie\s+scene\s+facts\s*:\s*", "", text, flags=re.I)
        text = re.split(
            r"\s*An explicit scene or location in the current request overrides\b",
            text,
            maxsplit=1,
            flags=re.I,
        )[0]
    elif section.source == "visual_memory":
        text = re.sub(r"^\s*visual\s+continuity\s+reference\s*:\s*", "", text, flags=re.I)
    elif section.source == "preset":
        text = re.sub(r"^\s*scene\s+preset\s*:\s*", "", text, flags=re.I)
    elif section.source == "fixed_prompt":
        text = re.sub(r"^\s*additional\s+(?:fixed\s+prompt|outfit\s+preference)\s*:\s*", "", text, flags=re.I)
    elif section.source == "wardrobe_structure":
        text = re.sub(r"^\s*concrete\s+wardrobe\s+specification\s*:\s*", "", text, flags=re.I)
    elif section.source == "composition":
        lower_name = str(section.name or "").strip().lower()
        if lower_name == "relationship_role_reference":
            text = "two distinct people in one coherent scene" if "shared frame" in text.lower() else ""
        elif lower_name == "composition":
            if "multi-person" in text.lower():
                text = "multi-person portrait, distinct people, one coherent scene"
            elif "back-view" in text.lower():
                text = "single character, back view, coherent outfit, natural environmental portrait"
            elif "mirror" in text.lower():
                text = "single character mirror portrait, coherent outfit, visible face"
            else:
                text = "single character, coherent outfit, one continuous scene, visible face, upper-body or three-quarter portrait"
        elif lower_name == "subject_count":
            text = "multi-person portrait, distinct referenced people" if "multi-person" in text.lower() else "single character, one person"
        else:
            text = ""
    text = text.replace(" ... [section compacted] ... ", ", ")
    text = re.sub(r"\s*\n+\s*", ", ", text)
    text = re.sub(r"\s*;\s*", ", ", text)
    text = re.sub(r"(?:\s*,\s*){2,}", ", ", text)
    return text.strip(" \t\r\n,.;；。")


def compile_local_photo_prompt(
    sections: Sequence[PhotoPromptSection],
    prompt_format: str,
) -> str:
    """Compile renderable positive content for single-text local image workflows.

    Traditional prompt envelopes contain orchestration metadata intended for an
    LLM. ComfyUI/SDGen often feed their only text input directly to CLIP/T5, so
    those labels, decisions and negative blocks must not share that input.
    """
    mode = str(prompt_format or "traditional").strip().lower().replace("-", "_")
    if mode != "traditional":
        return _assemble(sections, mode)
    values: list[str] = []
    seen: set[str] = set()
    for section in sections:
        if section.source not in _LOCAL_VISUAL_PROMPT_SOURCES:
            continue
        value = _local_visual_section_text(section)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return ", ".join(values).strip()


def compile_local_photo_negative_prompt(
    sections: Sequence[PhotoPromptSection],
    prompt_format: str,
) -> str:
    """Compile only negative constraints for a dedicated local workflow slot."""
    mode = str(prompt_format or "traditional").strip().lower().replace("-", "_")
    values: list[str] = []
    seen: set[str] = set()
    for section in sections:
        value = _clip(section.negative, 1600)
        if not value:
            continue
        if mode == "nai":
            value = re.sub(r"^-?\d+(?:\.\d+)?::(.*?)::$", r"\1", value).strip()
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            values.append(value)
    return ", ".join(values).strip(" ,")


def _reference_roles(reference: Any, wardrobe: Any) -> tuple[str, ...]:
    original = _value(reference, "reference_roles", ()) or ()
    effective = _value(wardrobe, "effective_reference_roles", ()) or ()
    return tuple(
        dict.fromkeys(
            str(item or "").strip().lower()
            for item in (*original, *effective)
            if str(item or "").strip()
        )
    )


def _sanitize_reference(reference: Any, wardrobe: Any, workflow_kind: str) -> tuple[Any, dict[str, Any] | None]:
    if reference is None:
        return None, None
    workflow = str(workflow_kind or "").strip().lower()
    if workflow in _EDIT_WORKFLOWS or workflow not in _SELFIE_WORKFLOWS:
        return reference, None
    roles = _reference_roles(reference, wardrobe)
    ignored_roles = frozenset(
        str(item or "").strip().lower()
        for item in (_value(reference, "ignored_reference_roles", ()) or ())
        if str(item or "").strip()
    )
    if "outfit" not in roles and "outfit" not in ignored_roles:
        return reference, None
    if (
        str(_value(reference, "kind", "") or "").strip().lower() == "recent_sent_photo"
        and "outfit" in ignored_roles
    ):
        return reference, None
    active_category = str(_value(wardrobe, "category", "") or "").strip().lower()
    authoritative = bool(_value(wardrobe, "lock_outfit", False) and active_category)
    excluded = frozenset(
        str(item or "").strip().lower()
        for item in (_value(wardrobe, "excluded_categories", ()) or ())
        if str(item or "").strip()
    )
    reference_category = str(_value(reference, "outfit_category", "") or "").strip().lower()
    reason = ""
    category = reference_category or "unknown"
    if reference_category and reference_category in excluded:
        reason = "reference_outfit_excluded"
    elif authoritative and reference_category and not _compatible(reference_category, active_category):
        reason = "reference_outfit_conflict"
    elif authoritative and not reference_category and active_category not in {"reference_outfit"}:
        reason = "reference_outfit_unknown"
    if not reason:
        return reference, None
    raw = str(_value(reference, "id", "") or _value(reference, "source", "") or "reference")
    return None, {
        "source": "reference",
        "section": raw,
        "rule": reason,
        "category": category,
        "action": "reference_removed",
        "preview": _preview(raw),
        "sha256": hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest(),
        "effective_reference_roles": [],
    }


def _remove_reference_dependent_context(
    sections: Sequence[PhotoPromptSection],
) -> tuple[list[PhotoPromptSection], list[dict[str, Any]], list[dict[str, Any]]]:
    sanitized: list[PhotoPromptSection] = []
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    reference_pattern = re.compile(
        r"\b(?:this|the|selected|provided|incompatible)\s+(?:image\s+)?reference\b"
        r"|\breference\s+(?:image|controls|responsibility)\b",
        flags=re.I,
    )
    for section in sections:
        if section.source == "user_request":
            sanitized.append(section)
            continue
        if section.source == "recent_continuity" and (section.positive or section.negative):
            for text in (section.positive, section.negative):
                if not text:
                    continue
                detected.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="detected",
                        text=text,
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="section_dropped",
                        text=text,
                    )
                )
            sanitized.append(replace(section, positive="", negative=""))
            continue

        fields: dict[str, str] = {}
        for field in ("positive", "negative"):
            text = getattr(section, field)
            clauses, separator = _split_clauses(text)
            kept: list[str] = []
            for clause in clauses:
                if not reference_pattern.search(clause):
                    kept.append(clause)
                    continue
                detected.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="detected",
                        text=clause,
                    )
                )
                removed.append(
                    _audit(
                        section,
                        rule="reference_context_removed",
                        category="reference",
                        action="clause_removed",
                        text=clause,
                    )
                )
            fields[field] = separator.join(kept)
        sanitized.append(replace(section, **fields))
    return sanitized, detected, removed


def resolve_photo_prompt_context(
    *,
    wardrobe: Any,
    sections: Sequence[PhotoPromptSection],
    prompt_format: str,
    workflow_kind: str,
    reference: Any = None,
    character_present: bool = True,
    photographic: bool = False,
) -> ResolvedPhotoPromptContext:
    # Requests may originate from another companion package that carries the
    # same public dataclass under a different module name. Normalize by fields
    # instead of requiring Python class identity across plugin boundaries.
    normalized_sections: list[PhotoPromptSection] = []
    for section in sections:
        if isinstance(section, PhotoPromptSection):
            normalized_sections.append(section)
            continue
        normalized_sections.append(
            PhotoPromptSection(
                name=str(_value(section, "name", "") or "section"),
                source=str(_value(section, "source", "") or "scene_context"),
                positive=str(_value(section, "positive", "") or ""),
                negative=str(_value(section, "negative", "") or ""),
                protected=bool(_value(section, "protected", False)),
                sanitize_conflicts=_value(section, "sanitize_conflicts", None),
            )
        )
    clean_reference, reference_removed = _sanitize_reference(reference, wardrobe, workflow_kind)
    prepared = normalized_sections
    detected: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    if reference_removed:
        prepared, reference_detected, reference_removals = _remove_reference_dependent_context(prepared)
        detected.extend(reference_detected)
        removed.extend(reference_removals)
    sanitized, section_detected, section_removed, residual = _sanitize_sections(tuple(prepared), wardrobe)
    detected.extend(section_detected)
    removed.extend(section_removed)
    complete_prompt = _assemble(sanitized, prompt_format, character_present=character_present, photographic=photographic)
    budgeted = _budget_sections(sanitized)
    residual.extend(_scan_residual_conflicts(budgeted, wardrobe))
    return ResolvedPhotoPromptContext(
        final_prompt=_assemble(budgeted, prompt_format, character_present=character_present, photographic=photographic),
        complete_prompt=complete_prompt,
        prompt_sections=tuple(budgeted),
        reference=clean_reference,
        detected_conflicts=tuple(detected),
        removed_conflicts=tuple(removed),
        residual_conflicts=tuple(residual),
        reference_removed=reference_removed,
    )
