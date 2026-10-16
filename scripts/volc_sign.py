"""火山引擎 V4 签名（HMAC-SHA256）——即梦图片生成走这套鉴权。

参考：火山引擎「签名方法 v4」。即梦（智能视觉平台）用：
    HOST    = visual.volcengineapi.com
    SERVICE = cv
    REGION  = cn-north-1
    Action  = CVSync2AsyncSubmitTask / CVSync2AsyncGetResult
    Version = 2022-08-31
    body    = {"req_key": "jimeng_t2i_v40", ...}

用法：
    url, headers, payload = sign_request(ak, sk, action="CVSync2AsyncSubmitTask",
                                         version="2022-08-31", body={"req_key": ...})
    urllib.request.Request(url, data=payload, headers=headers, method="POST")
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime, timezone

ALGORITHM = "HMAC-SHA256"
HOST = "visual.volcengineapi.com"
SERVICE = "cv"
REGION = "cn-north-1"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(value: str) -> str:
    return urllib.parse.quote(str(value), safe="-_.~")


def secret_key_variants(sk: str) -> list[tuple[str, str]]:
    """火山控制台里复制的 SK 有时是 base64 过的，这里给出候选顺序。"""
    out = [("as-given", sk)]
    cur = sk
    for depth in (1, 2):
        try:
            cur = base64.b64decode(cur + "=" * (-len(cur) % 4)).decode("utf-8")
        except Exception:  # noqa: BLE001
            break
        if not cur or cur in {v for _, v in out}:
            break
        out.append((f"base64x{depth}", cur))
    return out


def sign_request(
    ak: str,
    sk: str,
    *,
    action: str,
    version: str,
    body: dict,
    host: str = HOST,
    region: str = REGION,
    service: str = SERVICE,
    now: datetime | None = None,
) -> tuple[str, dict, bytes]:
    """返回 (url, headers, payload_bytes)。query 只放 Action/Version，其余参数进 body。"""
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    date = ts[:8]
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_hash = _sha256_hex(payload)

    query = {"Action": action, "Version": version}
    canonical_query = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items()))

    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        "content-type:application/json\n"
        f"host:{host}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{ts}\n"
    )
    canonical_request = "\n".join(
        ["POST", "/", canonical_query, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date}/{region}/{service}/request"
    string_to_sign = f"{ALGORITHM}\n{ts}\n{scope}\n{_sha256_hex(canonical_request.encode('utf-8'))}"

    k_date = _hmac(sk.encode("utf-8"), date)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"{ALGORITHM} Credential={ak}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    headers = {
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": ts,
        "X-Content-Sha256": payload_hash,
        "Authorization": authorization,
    }
    return f"https://{host}/?{canonical_query}", headers, payload


# 即梦 req_key（4.0 已由官方文档确认；4.6 待实测确认）
REQ_KEY_JIMENG_40 = "jimeng_t2i_v40"
REQ_KEY_JIMENG_46 = "jimeng_seedream46_cvtob"
