"""即梦生图本地服务：把火山签名 + 异步轮询包成 OpenAI 兼容的 /v1/images/generations。

为什么需要它：private_companion 只会用 `Authorization: Bearer …` 调 OpenAI 风格的
`{base}/images/generations`，而即梦（火山智能视觉平台）要求的是 **V4 签名**（AK/SK），
且是「提交任务 → 轮询取结果」两步。这个小服务就干这两件事，插件那边只当普通 OpenAI 图片接口用。

启动：
    python scripts/jimeng_image_server.py            # 默认 127.0.0.1:8790
    python scripts/jimeng_image_server.py --port 8790 --host 127.0.0.1

插件侧配置（用 scripts/set_image_model.py 改更省事）：
    platform      = openai
    base_url      = http://127.0.0.1:8790/v1
    api_key       = 任意（本机服务不校验）
    model         = jimeng-4.6   （备：jimeng-4.0）

接口：
    GET  /v1/models                 列出可用模型
    POST /v1/images/generations     {"model","prompt","n","size","response_format"}
    POST /v1/images/edits           同上，另带参考图（image / image_urls；http(s) 走 image_urls，
                                    本机文件或 data URL 自动转成 binary_data_base64）
    GET  /files/<name>              取回生成的图片文件
    GET  /healthz                   健康检查

签名实现见 scripts/volc_sign.py；AK/SK 读 secrets/volcengine_ak_sk.txt。
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import (  # noqa: E402
    REQ_KEY_JIMENG_40,
    REQ_KEY_JIMENG_46,
    sign_request,
)

SECRETS = REPO / "secrets" / "volcengine_ak_sk.txt"
FILES_DIR = REPO / "logs" / "jimeng-out"
REQ_LOG = REPO / "logs" / "jimeng-server-requests.jsonl"

SF_SECRETS = REPO / "secrets" / "siliconflow_api_key.txt"

# 上游后端：`volc`（即梦/火山，默认）或 `sf`（SiliconFlow）。
# 为什么要 sf：火山图生图的 req_key `jimeng_seedream46_cvtob` 在本账号被
# `HTTP 401 / code 50400 Access Denied` 拒绝，参考图通道整条不可用；
# SiliconFlow 的 `Qwen/Qwen-Image-Edit-2509` 实测能带参考图出图（约 26s）。
BACKEND = (os.environ.get("JIMENG_BACKEND") or "volc").strip().lower()
SF_API_BASE = (os.environ.get("SILICONFLOW_API_BASE") or "https://api.siliconflow.cn/v1").rstrip("/")
SF_MODEL_T2I = os.environ.get("SF_MODEL_T2I") or "Qwen/Qwen-Image"
SF_MODEL_EDIT = os.environ.get("SF_MODEL_EDIT") or "Qwen/Qwen-Image-Edit-2509"

# 提示词下传长度上限。即梦上游是硬限制，超过会直接报
# `invalid param: prompt can not be more than 800 chars`（实测 801 字符就 400），
# 所以这里必须自己先截断，但要截在句子/短语边界上，别把最后一个词切一半。
PROMPT_MAX_CHARS = 800

# clamp 时至少留给「场景描写」的字符数（其余预算优先给结尾的要求句：身份锚、姿势覆盖、负面词）。
MIN_HEAD_CHARS = 160

# 关于官方文档里的 `scale`（「文本描述影响的程度…输入图片影响程度越小」，默认 0.5）：
# 实测 `jimeng_seedream46_cvtob` **不接受这个参数** —— 带上它提交能成功（code 10000），
# 但 CVSync2AsyncGetResult 会返回 HTTP 500 / code 50500 "Internal Error"（或不结束）。
# 见 scripts/probe_jimeng_scale.py 与 scripts/probe_jimeng_scale70.py 的实测输出。
# 所以这里不发送 scale；要让文字压过参考图，只能靠提示词本身（见 flatten_prompt）。


def flatten_prompt(prompt: str) -> str:
    """把带 `[段落标题]` 与换行的提示词压成一段纯文本。

    官方文档明确：prompt 中英文均可、建议 ≤800 字符，且「除引号外不建议输入特殊的符号」。
    插件下发的提示词里有 `[User image request]`、`[Reference and wardrobe ruling]`
    这类方括号小标题和空行，属于文档不建议的特殊符号，压平后再送上游。
    """
    text = str(prompt or "")
    text = re.sub(r"\[[^\]\n]{1,60}\]", " ", text)
    text = re.sub(r"[ \t]*\n+[ \t]*", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


_SENTENCE_END_RE = re.compile(r"[.!?。！？]+(?:\s+|$)")
_AVOID_TAIL_RE = re.compile(r"(?is)(?:^|\s)((?:avoid|negative\s+prompt)\b.*)$")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """按句末标点切分，返回 [(start, end), ...]（标点与句尾空白留在 span 内）。"""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        spans.append((start, match.end()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _requirement_suffix(text: str, limit: int) -> tuple[str, str]:
    """把结尾的「要求句」整段摘出来（身份锚 + `Pose override:` + 否定句）。

    2026-10-16 04:4x 抓到的洞：旧实现只摘最后的否定句，于是头部 800 预算之外、否定句之前
    的那一段（身份锚句与 `Pose override:` 句）被整段丢掉 —— 实测 876 字符的提示词截到 751
    后 `Pose override` 直接消失，而姿势与身份恰恰是最不该丢的两段要求（复核方式：
    `logs/jimeng-server-requests.jsonl` 里 `stage=prompt_clamped` 的 `sent` 字段）。
    做法：从末尾往前累计整句，只要「要求句总量 + 至少 MIN_HEAD 字符的场景描写」还装得下，
    就把整句并进来 —— 宁可少写场景，也不丢姿势/身份/负面词。
    """
    spans = _sentence_spans(text)
    if not spans:
        return text, ""
    room = limit - MIN_HEAD_CHARS
    chosen = spans[-1][0]
    total = len(text) - chosen
    for start, _end in reversed(spans[:-1]):
        piece = len(text) - start
        if piece > room:
            break
        chosen = start
        total = piece
    if total > room:
        # 最后一句自己就吃掉大部分预算（例如超长负面词），退回「只保否定句」的老策略。
        match = _AVOID_TAIL_RE.search(text)
        if match and len(match.group(1)) <= room:
            return text[: match.start()].strip(), match.group(1).strip()
        return text, ""
    return text[:chosen].strip(), text[chosen:].strip()


def clamp_prompt(prompt: str, limit: int = PROMPT_MAX_CHARS) -> str:
    """把提示词收进上游长度限制内：先整段保住结尾的要求句，再在句子/逗号/空格边界收口。

    结尾的要求句（身份锚、`Pose override:`、`Avoid ...`）整段保留，被压缩的是中间的场景描写；
    要求句合计超过一半预算时退化为「只保最后的否定句」（见 `_requirement_suffix`）。
    否定句过长的老兜底保留：上游对超过 800 字符的请求直接 400
    （`prompt can not be more than 800 chars`），宁可丢场景也不能超限。
    """
    text = str(prompt or "")
    if len(text) <= limit:
        return text
    head_text, tail_text = _requirement_suffix(text, limit)
    tail = f" {tail_text}" if tail_text else ""
    budget = max(120, limit - len(tail))
    head = head_text[:budget]
    for sep in (". ", "。", "! ", "！", "? ", "？", "; ", "；", ", ", "，", " "):
        idx = head.rfind(sep)
        if idx >= int(budget * 0.6):
            head = head[: idx + len(sep)]
            break
    else:
        head = head.rstrip()
    result = (head.strip() + tail).strip()
    if len(result) > limit:
        # 兜底：尾部（要求句）优先保留，头部按剩余空间再收一次。
        # 2026-10-16 实测：tail 接近 limit//2 时旧算法会产出 823 字符，
        # 上游直接 400 `prompt can not be more than 800 chars`（一次实验 3/3 全废）。
        keep_tail = tail.strip()
        if len(keep_tail) >= limit:
            return keep_tail[:limit]
        room = limit - len(keep_tail) - 1
        result = (head_text[:room].rstrip() + " " + keep_tail).strip()[:limit]
    return result

MODEL_ALIASES = {
    "jimeng-4.6": REQ_KEY_JIMENG_46,
    "jimeng-4-6": REQ_KEY_JIMENG_46,
    "jimeng4.6": REQ_KEY_JIMENG_46,
    "seedream-4.6": REQ_KEY_JIMENG_46,
    "seedream-4-6": REQ_KEY_JIMENG_46,
    "doubao-seedream-4-6": REQ_KEY_JIMENG_46,
    "jimeng_seedream46_cvtob": REQ_KEY_JIMENG_46,
    "jimeng-4.0": REQ_KEY_JIMENG_40,
    "jimeng-4-0": REQ_KEY_JIMENG_40,
    "jimeng4.0": REQ_KEY_JIMENG_40,
    "seedream-4.0": REQ_KEY_JIMENG_40,
    "seedream-4-0": REQ_KEY_JIMENG_40,
    "doubao-seedream-4-0": REQ_KEY_JIMENG_40,
    "jimeng_t2i_v40": REQ_KEY_JIMENG_40,
}
SUPPORTED_SIZES = [
    # (面积, (宽, 高)) —— 取自官方文档推荐值；解析不出来时按面积兜底
    (1024 * 1024, (1024, 1024)),
    (2048 * 2048, (2048, 2048)),
    (2304 * 1728, (2304, 1728)),
    (2496 * 1664, (2496, 1664)),
    (2560 * 1440, (2560, 1440)),
    (3024 * 1296, (3024, 1296)),
    (4096 * 4096, (4096, 4096)),
    (4694 * 3520, (4694, 3520)),
    (4992 * 3328, (4992, 3328)),
    (5404 * 3040, (5404, 3040)),
    (6198 * 2656, (6198, 2656)),
]


def log_event(payload: dict) -> None:
    try:
        REQ_LOG.parent.mkdir(parents=True, exist_ok=True)
        with REQ_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **payload},
                                ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def load_creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in SECRETS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"{SECRETS} 需要两行：AccessKeyID / SecretAccessKey")
    return lines[0], lines[1]


def load_sf_key() -> str:
    env = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if env:
        return env
    if SF_SECRETS.exists():
        key = SF_SECRETS.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise RuntimeError(f"缺少 SiliconFlow API Key：设置 SILICONFLOW_API_KEY 或写入 {SF_SECRETS}")


SF_SIZE_MIN = 256
SF_SIZE_MAX = 2048


def sf_image_size(size_params: dict) -> str:
    """火山参数（width/height 或面积）→ SiliconFlow 的 `image_size: "WxH"`。"""
    width = int(size_params.get("width") or 0)
    height = int(size_params.get("height") or 0)
    if not width or not height:
        area = int(size_params.get("size") or 0)
        if area:
            side = max(SF_SIZE_MIN, min(SF_SIZE_MAX, int(round(area ** 0.5))))
            side = int(round(side / 16)) * 16
            return f"{side}x{side}"
        return "1024x1024"
    width = max(SF_SIZE_MIN, min(SF_SIZE_MAX, int(round(width / 16)) * 16))
    height = max(SF_SIZE_MIN, min(SF_SIZE_MAX, int(round(height / 16)) * 16))
    return f"{width}x{height}"


def _sf_images(refs: list[str]) -> list[str]:
    """把 refs 规整成 SiliconFlow 认的「URL 或 data URI」列表。"""
    url_refs, blob_refs = split_refs(refs)
    images: list[str] = [u for u in url_refs if u]
    for blob in blob_refs:
        try:
            data = base64.b64decode(blob + "=" * (-len(blob) % 4))
            mime = sniff_image_mime(data)
            images.append(f"data:{mime};base64," + base64.b64encode(data).decode("ascii"))
        except Exception:  # noqa: BLE001
            images.append("data:image/png;base64," + str(blob))
    return images


def sf_generate(prompt: str, refs: list[str], size_params: dict, count: int = 1,
                timeout: float = 300.0) -> dict:
    """SiliconFlow 同步出图。带参考图走 Qwen-Image-Edit，否则 Qwen-Image。"""
    api_key = load_sf_key()
    images = _sf_images(refs)
    model = SF_MODEL_EDIT if images else SF_MODEL_T2I
    body: dict = {"model": model, "prompt": prompt,
                  "image_size": sf_image_size(size_params),
                  "n": max(1, min(4, int(count or 1)))}
    if images:
        # SiliconFlow 的 `image` 字段：单图必须是**字符串**，传成 list 会 400
        # `{"code":20015,"message":"image should be a string"}`（实测）。
        picked = images[:4]
        body["image"] = picked[0] if len(picked) == 1 else picked
    request = urllib.request.Request(
        f"{SF_API_BASE}/images/generations",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"SiliconFlow HTTP {exc.code}: {detail}") from exc
    urls = [item.get("url") for item in (payload.get("images") or [])
            if isinstance(item, dict) and item.get("url")]
    if not urls:
        raise RuntimeError(f"SiliconFlow 未返回图片：{json.dumps(payload, ensure_ascii=False)[:400]}")
    log_event({"stage": "sf_done", "model": model, "refs": len(images), "n": len(urls),
               "seconds": round(time.monotonic() - started, 1),
               "image_size": body["image_size"]})
    return {"image_urls": urls,
            "task_id": str(payload.get("shared_id") or payload.get("id") or ""),
            "raw": {"model": model, "timings": payload.get("timings")}}


SIZE_MIN_AREA = 1024 * 1024
SIZE_MAX_AREA = 4096 * 4096


def _scaled_size(width: int, height: int, target_area: float) -> dict:
    """等比缩放 (width, height) 让 w*h 贴近 target_area，宽高取偶数。"""
    scale = (target_area / max(1, width * height)) ** 0.5
    w = max(2, int(round(width * scale / 2)) * 2)
    h = max(2, int(round(height * scale / 2)) * 2)
    return {"width": w, "height": h}


def parse_size(value: object, aspect: str = "") -> dict:
    """把 OpenAI 的 '1024x1024' / 面积数值 / 空 解析成火山参数。

    显式给出宽高时以宽高为准（aspect_ratio 只在解析不出宽高时参与兜底），
    越界时按同一比例缩放；完全解析不出来才吸附到文档推荐值。
    """
    text = str(value or "").strip().lower().replace("*", "x").replace("×", "x")
    m = re.fullmatch(r"(\d{3,5})\s*x\s*(\d{3,5})", text)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        area = w * h
        ratio = w / h if h else 0
        if not (1 / 16) <= ratio <= 16:
            best = min(SUPPORTED_SIZES, key=lambda item: abs(item[0] - area))
            return {"width": best[1][0], "height": best[1][1]}
        # 火山在 GetResult 阶段硬校验 width*height ∈ [1024*1024, 4096*4096]：
        # 1280x720（92 万像素）SubmitTask 会通过，取结果时才报
        # "invalid param: width*height must in [1024 * 1024, 4096 * 4096]"。
        # 所以低于下限时按同一比例放大到刚好达标（1280x720 → 1372x772），
        # 高于上限时按比例缩小，宽高都取偶数。
        if area < SIZE_MIN_AREA:
            return _scaled_size(w, h, SIZE_MIN_AREA * 1.01)
        if area > SIZE_MAX_AREA:
            return _scaled_size(w, h, SIZE_MAX_AREA)
        return {"width": w, "height": h}
    m = re.fullmatch(r"(\d{5,9})", text)
    if m:
        area = int(m.group(1))
        if 1024 * 1024 <= area <= 4096 * 4096:
            return {"size": area}
    return {"size": 2048 * 2048}


def resolve_req_key(model: str) -> str:
    return MODEL_ALIASES.get(str(model or "").strip().lower(), REQ_KEY_JIMENG_46)


def sniff_image_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    return "image/png"


def split_refs(refs: list[str]) -> tuple[list[str], list[str]]:
    """把参考图拆成（公网 URL，裸 base64）。

    火山的 `image_urls` 只接受**能被它下载的** http(s) 地址：喂 data URL 会直接
    50220 `Download Url Error`。本机文件 / 内联图片必须走 `binary_data_base64`。
    """
    urls: list[str] = []
    blobs: list[str] = []
    for ref in refs:
        text = str(ref or "").strip()
        if not text:
            continue
        if text.startswith(("http://", "https://")):
            urls.append(text)
            continue
        raw = text.split(",", 1)[-1] if text.startswith("data:") else text
        raw = re.sub(r"\s+", "", raw)
        if not raw:
            continue
        try:
            base64.b64decode(raw + "=" * (-len(raw) % 4))
        except Exception:  # noqa: BLE001
            continue
        blobs.append(raw)
    return urls, blobs


_TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})

# 上游把「额度 / 权限」类失败统一回成 HTTP 401 + code 50400「Access Denied」。
# 2026-10-16 02:20 实测：前 187 次全部成功，之后连续 401（Access Denied / api
# forbidden），两种 action 都拒绝，重试无效。这类错误重试没有意义，反而可能
# 在限流/封禁窗口里继续撞墙，所以记一个冷却窗口：窗口内不再请求上游，直接把
# 真实原因快速带回调用方（用户看到的是「这次画不出来」，但日志里有明确归因）。
ACCESS_DENIED_COOLDOWN_SECONDS = 300.0
# 冷却窗口里「放一个请求真打上游」的最小间隔。值必须 > 0：额度恢复是账号级的、全有全无，
# 若窗口内一律快速失败，真实用户请求就永远发现不了「其实已经好了」，只能等到冷却自然过期；
# 而冷却又被看门狗每 120s 的探测不断续期，最坏情况会把恢复推迟到 5 分钟以上。
# 每 90s 最多放 1 个真请求，最坏代价是每分钟多烧 1 次调用（额度被拒时调用不产生出图费用）。
ACCESS_DENIED_PROBE_SECONDS = 90.0
# 最近一次上游真的回了 2xx 的时间戳。看门狗/实验驱动靠它判断「上游是否恢复」，
# 不需要各自再打一次真实探测请求——否则本地闸门 90s 放行一次、看门狗 120s 探一次、
# 驱动 120s 探一次，同一分钟里会重复烧掉三次调用额度。
_last_upstream_success_ts = ""
_access_denied_lock = threading.Lock()
_access_denied_state: dict = {
    "until": 0.0,
    "detail": "",
    "count": 0,
    "first_ts": "",
    "last_ts": "",
    "last_probe": 0.0,
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def note_access_denied(detail: str, *, cooldown: float = ACCESS_DENIED_COOLDOWN_SECONDS) -> None:
    """记录一次「上游拒绝访问」，并开启冷却窗口。"""
    with _access_denied_lock:
        _access_denied_state["until"] = max(
            float(_access_denied_state.get("until") or 0.0),
            time.monotonic() + max(0.0, cooldown),
        )
        _access_denied_state["detail"] = str(detail)[:600]
        _access_denied_state["count"] = int(_access_denied_state.get("count") or 0) + 1
        if not _access_denied_state.get("first_ts"):
            _access_denied_state["first_ts"] = _now_iso()
        _access_denied_state["last_ts"] = _now_iso()


def access_denied_snapshot() -> dict:
    """返回冷却状态快照（供 /v1/status 与错误响应使用）。"""
    with _access_denied_lock:
        until = float(_access_denied_state.get("until") or 0.0)
        left = max(0.0, until - time.monotonic())
        return {
            "active": left > 0,
            "seconds_left": round(left, 1),
            "count": int(_access_denied_state.get("count") or 0),
            "first_ts": _access_denied_state.get("first_ts") or "",
            "last_ts": _access_denied_state.get("last_ts") or "",
            "detail": _access_denied_state.get("detail") or "",
        }


def clear_access_denied(reason: str = "") -> None:
    """上游成功了一次 → 立刻解除冷却，别让下一个请求继续吃快速失败。

    只在「本来就处于冷却/被拒状态」时才写 `access_denied_cleared` 日志：回退期每个请求
    都会带着 `fallback ... ok` 调一次这里，若无条件记一行，请求日志上会看起来像「反复恢复」，
    把真正的那一次恢复淹没掉。
    """
    with _access_denied_lock:
        was_active = float(_access_denied_state.get("until") or 0.0) > time.monotonic()
        had_detail = bool(_access_denied_state.get("detail")) or int(_access_denied_state.get("count") or 0) > 0
        _access_denied_state["until"] = 0.0
        _access_denied_state["detail"] = ""
        _access_denied_state["count"] = 0
        _access_denied_state["first_ts"] = ""
        _access_denied_state["last_ts"] = ""
        _access_denied_state["last_probe"] = 0.0
    if reason and (was_active or had_detail):
        log_event({"stage": "access_denied_cleared", "reason": reason})


def allow_gate_probe(*, interval: float = ACCESS_DENIED_PROBE_SECONDS) -> bool:
    """冷却窗口内是否放这个请求「冒险」真打一次上游。

    返回 True 时调用方必须继续走正常上游路径：成功会被 clear_access_denied() 立刻解除冷却，
    失败会 note_access_denied() 续期（这是预期行为，不是 bug）。
    """
    now = time.monotonic()
    with _access_denied_lock:
        last = float(_access_denied_state.get("last_probe") or 0.0)
        if last and now - last < max(1.0, interval):
            return False
        _access_denied_state["last_probe"] = now
        return True


def is_access_denied_detail(text: str) -> bool:
    """错误文本是否属于上游「拒绝访问」（额度/权限/风控）。

    本机服务对插件来说是 Provider：插件把 5xx 一律渲染成「上游生图服务临时失败或网关中断」，
    只有状态码能穿过插件进入排障结论。所以「额度/资质被拒」必须回 4xx（403），
    否则排障会给出 retryable=true 的临时故障结论，用户和 LLM 都会无意义地重试。
    """
    low = str(text or "").lower()
    return any(
        marker in low
        for marker in ("http 401", "http 403", "50400", "access denied", "api forbidden")
    )


# 模型级回退链：上游的「Access Denied」是**按模型**给的，不是整号封禁。
# 2026-10-16 04:03 用 `scripts/probe_volc_reqkeys.py` 实测：
#   jimeng_seedream46_cvtob → 401/50400 拒绝；jimeng_t2i_v31 / v30 → 同样拒绝；
#   但 jimeng_t2i_v40 → HTTP 200 可用（submit→done 约 16s，出图正常）。
# 所以某个 req_key 被拒时换另一个继续出图，比让用户「今天画不出来」好得多。
FALLBACK_REQ_KEYS = {
    REQ_KEY_JIMENG_46: (REQ_KEY_JIMENG_40,),
    REQ_KEY_JIMENG_40: (REQ_KEY_JIMENG_46,),
}
REQ_KEY_DENIED_TTL = 600.0
_req_key_denied: dict = {}  # req_key -> monotonic 到期时刻


def mark_req_key_denied(req_key: str, detail: str = "", *, ttl: float = REQ_KEY_DENIED_TTL) -> None:
    with _access_denied_lock:
        _req_key_denied[str(req_key)] = time.monotonic() + max(1.0, ttl)
    log_event({"stage": "req_key_denied", "req_key": req_key, "ttl": ttl, "detail": str(detail)[:300]})


def clear_req_key_denied(req_key: str, reason: str = "") -> None:
    with _access_denied_lock:
        existed = _req_key_denied.pop(str(req_key), None) is not None
    if existed and reason:
        log_event({"stage": "req_key_recovered", "req_key": req_key, "reason": reason})


def req_key_denied(req_key: str) -> bool:
    now = time.monotonic()
    with _access_denied_lock:
        until = float(_req_key_denied.get(str(req_key)) or 0.0)
        if until and until <= now:
            _req_key_denied.pop(str(req_key), None)
            return False
        return until > now


def candidate_req_keys(primary: str) -> list:
    """先试主模型；被标记拒绝的模型排到最后（仍保留一次尝试机会）。"""
    ordered = [str(primary)] + [k for k in FALLBACK_REQ_KEYS.get(str(primary), ()) if k != str(primary)]
    fresh = [k for k in ordered if not req_key_denied(k)]
    stale = [k for k in ordered if req_key_denied(k)]
    return fresh + stale


def req_key_denied_snapshot() -> dict:
    now = time.monotonic()
    with _access_denied_lock:
        return {k: round(max(0.0, v - now), 1) for k, v in _req_key_denied.items() if v > now}


def volc_call(ak: str, sk: str, action: str, body: dict, *, attempts: int = 3) -> dict:
    """调用火山接口，对网络抖动与上游 5xx 做有限重试。

    生产上出现过 `URLError: <urlopen error [WinError 10060] 连接尝试失败>` 与
    `HTTP 500 Internal Error`，单次失败会让整次生图失败、把「这张画不出来」推给用户。
    4xx（除 429）是参数/权限问题，重试无意义，直接抛出。
    """
    last_error = ""
    global _last_upstream_success_ts
    for attempt in range(1, max(1, attempts) + 1):
        url, headers, payload = sign_request(ak, sk, action=action, version="2022-08-31", body=body)
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            _last_upstream_success_ts = _now_iso()
            # 上游回了话就说明额度/权限恢复了：立刻解除冷却，别让后续请求继续被本地闸门拦住。
            if access_denied_snapshot()["active"]:
                clear_access_denied("volc_call ok")
            return payload
        except urllib.error.HTTPError as exc:
            # 上游 4xx/5xx 的响应体里才有真正原因（code/message），必须带出来，
            # 否则插件只会看到「HTTP Error 400: Bad Request」这种没用的信息。
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                detail = ""
            last_error = (
                f"火山接口 HTTP {exc.code}（action={action}，第{attempt}/{attempts}次）："
                f"{detail[:800] or exc.reason}"
            )
            # 401 / 50400 Access Denied = 额度或权限被上游拒绝，重试无意义：
            # 记冷却窗口并立刻抛出，别在这个窗口里继续打上游。
            if exc.code == 401 or is_access_denied_detail(f"http {exc.code} {detail}"):
                note_access_denied(last_error)
                raise RuntimeError(last_error) from exc
            if exc.code not in _TRANSIENT_HTTP_CODES or attempt >= attempts:
                raise RuntimeError(last_error) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = f"火山接口网络错误（action={action}，第{attempt}/{attempts}次）：{exc}"
            if attempt >= attempts:
                raise RuntimeError(last_error) from exc
        time.sleep(3.0 * attempt)
    raise RuntimeError(last_error or f"火山接口调用失败（action={action}）")


def generate(ak: str, sk: str, req_key: str, params: dict, refs: list[str],
             timeout: float = 300.0, interval: float = 4.0) -> dict:
    """提交任务 + 轮询，返回 {"image_urls": [...], "task_id": ...} 或抛异常。"""
    body = {"req_key": req_key, "return_url": True, "force_single": bool(params.pop("force_single", True))}
    body.update({k: v for k, v in params.items() if v not in (None, "", [], {})})
    url_refs, blob_refs = split_refs(refs)
    if url_refs:
        body["image_urls"] = url_refs[:10]
    if blob_refs:
        body["binary_data_base64"] = blob_refs[:10]
    submitted = volc_call(ak, sk, "CVSync2AsyncSubmitTask", body)
    if submitted.get("code") not in (10000, None):
        raise RuntimeError(f"提交失败：{json.dumps(submitted, ensure_ascii=False)[:400]}")
    task_id = str((submitted.get("data") or {}).get("task_id") or "")
    if not task_id:
        raise RuntimeError(f"提交未返回 task_id：{json.dumps(submitted, ensure_ascii=False)[:400]}")

    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = volc_call(ak, sk, "CVSync2AsyncGetResult", {
            "req_key": req_key, "task_id": task_id,
            "req_json": json.dumps({"return_url": True}),
        })
        data = last.get("data") or {}
        status = str(data.get("status") or "").lower()
        urls = [u for u in (data.get("image_urls") or []) if u]
        if status == "done" or urls:
            if urls:
                return {"image_urls": urls, "task_id": task_id, "raw": data}
            raise RuntimeError(f"任务已结束但没有图片：{json.dumps(last, ensure_ascii=False)[:400]}")
        if status in {"not_found", "expired", "failed"}:
            raise RuntimeError(f"任务状态 {status}：{json.dumps(last, ensure_ascii=False)[:400]}")
        time.sleep(interval)
    raise TimeoutError(f"等待生图超时（{timeout:.0f}s），task_id={task_id}，最后状态="
                       f"{json.dumps(last, ensure_ascii=False)[:300]}")


def save_image(url: str, tag: str) -> Path:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".png"
    m = re.search(r"\.(png|jpe?g|webp)(?:[?#]|$)", url or "", re.I)
    if m:
        suffix = "." + m.group(1).lower()
    path = FILES_DIR / f"{tag}-{uuid.uuid4().hex[:8]}{suffix}"
    req = urllib.request.Request(url, headers={"User-Agent": "jimeng-image-server/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, path.open("wb") as fh:
        fh.write(resp.read())
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = "jimeng-image-server/1.0"

    def log_message(self, fmt: str, *args) -> None:  # 静音默认访问日志
        pass

    # ---------- helpers ----------
    def _send(self, code: int, payload: object, content_type: str = "application/json") -> None:
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _collect_refs(self, payload: dict) -> list[str]:
        refs: list[str] = []
        for key in ("image_urls", "images", "image", "input_image", "reference_images"):
            value = payload.get(key)
            if not value:
                continue
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, str) and item.strip():
                    refs.append(item.strip())
                elif isinstance(item, dict):
                    for sub in ("url", "image_url", "data_url", "b64_json", "base64"):
                        if item.get(sub):
                            refs.append(str(item[sub]).strip())
                            break
        out: list[str] = []
        for ref in refs:
            if ref.startswith("data:") or ref.startswith("http://") or ref.startswith("https://"):
                out.append(ref)
                continue
            # 裸 base64：按真实图片类型补上 data URL 前缀（后续统一拆成 binary_data_base64）
            raw = ref.split(",", 1)[-1]
            try:
                decoded = base64.b64decode(raw + "=" * (-len(raw) % 4))
            except Exception:  # noqa: BLE001
                continue
            out.append(f"data:{sniff_image_mime(decoded)};base64,{raw}")
        return out

    # ---------- routes ----------
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/healthz", "/"):
            self._send(200, {"ok": True, "service": "jimeng-image-server",
                             "models": sorted({v for v in MODEL_ALIASES.values()})})
            return
        if path in ("/v1/models", "/models"):
            data = [{"id": name, "object": "model", "owned_by": "jimeng"}
                    for name in ("jimeng-4.6", "jimeng-4.0")]
            self._send(200, {"object": "list", "data": data})
            return
        if path in ("/v1/status", "/status"):
            # 排障用：上游被拒绝时会进入冷却窗口，这里能看到剩余秒数与原始原因。
            snap = access_denied_snapshot()
            self._send(200, {"ok": not snap["active"], "service": "jimeng-image-server",
                             "access_denied": snap,
                             "req_key_denied": req_key_denied_snapshot(),
                             "last_upstream_success_ts": _last_upstream_success_ts,
                             "prompt_max_chars": PROMPT_MAX_CHARS})
            return
        if path.startswith("/files/"):
            file_path = (FILES_DIR / Path(path).name).resolve()
            if not str(file_path).startswith(str(FILES_DIR.resolve())) or not file_path.is_file():
                self._send(404, {"error": {"message": "not found"}})
                return
            ctype = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            self._send(200, file_path.read_bytes(), ctype)
            return
        self._send(404, {"error": {"message": f"unknown path {path}"}})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        raw = self._read_body()
        ctype = (self.headers.get("Content-Type") or "").lower()
        payload: dict = {}
        if "application/json" in ctype:
            try:
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception:  # noqa: BLE001
                self._send(400, {"error": {"message": "invalid JSON"}})
                return
        elif "multipart/form-data" in ctype:
            payload = self._parse_multipart(raw, ctype)
        else:
            try:
                payload = json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception:  # noqa: BLE001
                payload = {}

        if path not in ("/v1/images/generations", "/images/generations",
                        "/v1/images/edits", "/images/edits", "/v1/image_generation",
                        "/v1/image/generation"):
            log_event({"path": path, "stage": "404", "payload_keys": sorted(payload)})
            self._send(404, {"error": {"message": f"unknown path {path}"}})
            return

        model = str(payload.get("model") or "jimeng-4.6")
        req_key = resolve_req_key(model)
        prompt = flatten_prompt(str(payload.get("prompt") or payload.get("text") or ""))
        count = int(payload.get("n") or 1)
        size_params = parse_size(payload.get("size"), str(payload.get("aspect_ratio") or ""))
        refs = self._collect_refs(payload)
        response_format = str(payload.get("response_format") or "url").lower()

        # `model`/`req_key` 这里是**客户端请求**的模型，不代表真正打上游用的 req_key：模型级
        # 回退生效时（4.6 被拒 → 4.0）每个请求仍会记一行 model=jimeng-4.6，只有后面的
        # `attempt` / `fallback_ok` 才说明实际用了哪个 key。把候选顺序和当前拒绝标记一起记下来，
        # 免得运维把「请求的模型」当成「实际出图的模型」（2026-10-16 04:1x 踩过）。
        log_event({"path": path, "stage": "request", "model": model, "req_key": req_key,
                   "candidates": candidate_req_keys(req_key),
                   "denied": req_key_denied_snapshot(),
                   "prompt": prompt[:400], "prompt_len": len(prompt), "n": count, "size_params": size_params,
                   "refs": len(refs), "response_format": response_format,
                   "payload_keys": sorted(payload)})

        if not prompt:
            self._send(400, {"error": {"message": "prompt is required"}})
            return

        if BACKEND == "sf":
            # SiliconFlow 是同步接口（没有「提交 + 轮询」），但对外仍保持 OpenAI 形状，
            # 插件侧完全无感知。带参考图（edits / image 字段）时自动切到 Edit 模型。
            try:
                result = sf_generate(clamp_prompt(prompt), refs, size_params, count)
                data_items: list[dict] = []
                for url in result["image_urls"][: max(1, count)]:
                    saved = save_image(url, "sf")
                    item: dict = {"url": f"http://{self.headers.get('Host') or '127.0.0.1'}"
                                         f"/files/{saved.name}",
                                  "revised_prompt": prompt[:200]}
                    try:
                        item["b64_json"] = base64.b64encode(saved.read_bytes()).decode("ascii")
                    except Exception:  # noqa: BLE001
                        pass
                    data_items.append(item)
                log_event({"stage": "done", "backend": "sf", "task_id": result.get("task_id"),
                           "model": result["raw"].get("model"),
                           "images": [it["url"] for it in data_items]})
                self._send(200, {"created": int(time.time()), "data": data_items,
                                 "model": model, "backend": "sf",
                                 "req_key": result["raw"].get("model"),
                                 "task_id": result.get("task_id")})
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
                log_event({"stage": "error", "backend": "sf", "error": detail[:600]})
                self._send(502, {"error": {"message": f"siliconflow upstream error: {detail[:400]}",
                                           "type": "upstream_error"}})
            return

        params = dict(size_params)
        params["prompt"] = clamp_prompt(prompt)
        if len(params["prompt"]) != len(prompt):
            # head/tail are logged so we can see which prompt sections survive the
            # upstream 800-char limit (the clamp keeps head + tail).
            log_event({
                "stage": "prompt_clamped",
                "chars": [len(prompt), len(params["prompt"])],
                "head": params["prompt"][:260],
                "tail": params["prompt"][-320:],
                # 2026-10-16 04:4x：`head`/`tail` 之间还有一段没记（800 上限下最长 ~200 字符），
                # 于是「姿势覆盖句 / 身份锚句有没有被截掉」只能靠猜。clamped 结果本来就只有
                # ≤800 字符，直接整条记下来，省得以后再回放。
                "sent": params["prompt"],
            })
        params["force_single"] = count <= 1
        candidates = candidate_req_keys(req_key)
        cooling = access_denied_snapshot()
        if cooling["active"] and not any(not req_key_denied(k) for k in candidates):
            # 上游刚拒绝过访问（额度/权限），而且**所有候选模型**都被标记拒绝。窗口内基本不打
            # 上游：快速失败，把冷却原因和剩余秒数如实带回，避免在限流期继续加码。但每
            # ACCESS_DENIED_PROBE_SECONDS 放一个请求真打一次——否则额度恢复了也没人知道。
            if not allow_gate_probe():
                log_event({"stage": "cooldown_block", "model": model,
                           "seconds_left": cooling["seconds_left"], "detail": cooling["detail"][:300]})
                self._send(403, {"error": {
                    "message": ("jimeng upstream access denied (retrying suppressed): "
                                + cooling["detail"][:300]),
                    "type": "upstream_access_denied",
                    "retry_after": int(cooling["seconds_left"]) + 1,
                }})
                return
            log_event({"stage": "cooldown_probe", "model": model,
                       "seconds_left": cooling["seconds_left"]})
        elif cooling["active"]:
            # 冷却生效中，但还有没被拒绝的候选模型（例如 4.6 被拒、4.0 仍可用）：
            # 不要因为另一个模型的冷却把这次请求也拦掉。
            log_event({"stage": "cooldown_bypass", "model": model, "candidates": candidates,
                       "seconds_left": cooling["seconds_left"]})
        used_key = req_key
        try:
            ak, sk = load_creds()
            # 组图：一次任务最多出 15 张，count>1 时按需放开 force_single
            if count > 1:
                params["force_single"] = False
            result = None
            last_exc: Exception | None = None
            for index, key in enumerate(candidates):
                try:
                    # 实际打上游的 key（回退时会与请求的 req_key 不同），单独记一行，
                    # 这样一次请求的「试了谁、成了谁」在日志里是闭合的。
                    log_event({"stage": "attempt", "req_key": key, "index": index,
                               "of": len(candidates), "model": model})
                    result = generate(ak, sk, key, dict(params), refs)
                    used_key = key
                    clear_req_key_denied(key, "generate ok")
                    if key != req_key:
                        # 回退成功后必须解除全局冷却：note_access_denied() 是主模型拒绝时记下的，
                        # 它只说明那一个模型不可用，继续冷却会把能用的模型也一起拦掉。
                        clear_access_denied(f"fallback {req_key}->{key} ok")
                        log_event({"stage": "fallback_ok", "from": req_key, "to": key,
                                   "task_id": result.get("task_id")})
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    detail = f"{type(exc).__name__}: {exc}"
                    if is_access_denied_detail(detail):
                        mark_req_key_denied(key, detail)
                        if index + 1 < len(candidates):
                            log_event({"stage": "fallback", "from": key, "to": candidates[index + 1],
                                       "reason": detail[:200]})
                            continue
                    raise
            if result is None:
                raise last_exc or RuntimeError("所有候选 req_key 都未能出图")
            data_items: list[dict] = []
            for url in result["image_urls"][: max(1, count)]:
                saved = save_image(url, used_key.replace("/", "_"))
                item: dict = {"url": f"http://{self.headers.get('Host') or '127.0.0.1'}"
                                     f"/files/{saved.name}",
                              "revised_prompt": prompt[:200]}
                try:
                    item["b64_json"] = base64.b64encode(saved.read_bytes()).decode("ascii")
                except Exception:  # noqa: BLE001
                    pass
                data_items.append(item)
            log_event({"stage": "done", "task_id": result.get("task_id"), "req_key": used_key,
                       "images": [it["url"] for it in data_items]})
            self._send(200, {"created": int(time.time()), "data": data_items,
                             "model": model, "req_key": used_key,
                             "task_id": result.get("task_id")})
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            log_event({"stage": "error", "error": detail[:600]})
            if is_access_denied_detail(detail):
                # 额度/权限被拒：回 403（而不是 502），插件侧才会把它判成
                # provider_access_denied 而不是「上游临时失败」。
                cooling = access_denied_snapshot()
                self._send(403, {"error": {"message": f"jimeng upstream access denied: {detail[:400]}",
                                           "type": "upstream_access_denied",
                                           "retry_after": int(cooling["seconds_left"]) + 1}})
            else:
                self._send(502, {"error": {"message": f"jimeng upstream error: {detail[:400]}",
                                           "type": "upstream_error"}})

    def _parse_multipart(self, raw: bytes, ctype: str) -> dict:
        m = re.search(r"boundary=([^;]+)", ctype)
        if not m:
            return {}
        boundary = ("--" + m.group(1).strip('"')).encode()
        payload: dict = {}
        for part in raw.split(boundary):
            if b"\r\n\r\n" not in part:
                continue
            head, _, body = part.partition(b"\r\n\r\n")
            head_text = head.decode("utf-8", "replace")
            name_m = re.search(r'name="([^"]+)"', head_text)
            if not name_m:
                continue
            name = name_m.group(1)
            value = body.rstrip(b"\r\n-")
            if "filename=" in head_text:
                payload.setdefault("image", []).append(
                    f"data:{sniff_image_mime(value)};base64," + base64.b64encode(value).decode("ascii"))
            else:
                payload[name] = value.decode("utf-8", "replace")
        return payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    args = ap.parse_args()
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ak, _ = load_creds()
        print(f"[jimeng] AK {ak[:8]}… 已加载；输出目录 {FILES_DIR}")
    except Exception as exc:  # noqa: BLE001
        print(f"[jimeng] ⚠️ 读取 AK/SK 失败：{exc}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[jimeng] 监听 http://{args.host}:{args.port}/v1  "
          f"(POST /v1/images/generations)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[jimeng] 退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
