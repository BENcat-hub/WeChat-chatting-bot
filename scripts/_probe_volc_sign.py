"""探测火山 AK/SK 是否可用：用 V4 签名打即梦（智能视觉）接口。

    python scripts/_probe_volc_sign.py

会依次尝试 SK 的几种形态（控制台复制出来的有时是 base64），
对每种形态打印 HTTP 状态与响应片段——只要不是 InvalidCredential/SignatureDoesNotMatch，
就说明签名这关过了。
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from volc_sign import (  # noqa: E402
    REQ_KEY_JIMENG_40,
    REQ_KEY_JIMENG_46,
    secret_key_variants,
    sign_request,
)

SECRETS = REPO / "secrets" / "volcengine_ak_sk.txt"


def load_creds() -> tuple[str, str]:
    lines = [ln.strip() for ln in SECRETS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[0], lines[1]


def call(ak: str, sk: str, req_key: str, extra: dict | None = None) -> tuple[int, str]:
    body = {
        "req_key": req_key,
        "prompt": "一只胖橘猫坐在木窗台上，午后的柔光",
        "return_url": True,
        "width": 1024,
        "height": 1024,
        "force_single": True,
    }
    if extra:
        body.update(extra)
    url, headers, payload = sign_request(
        ak, sk, action="CVSync2AsyncSubmitTask", version="2022-08-31", body=body
    )
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(600).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(600).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {str(exc)[:300]}"


def main() -> int:
    ak, sk_raw = load_creds()
    print(f"AK = {ak[:8]}…{ak[-4:]}  (len={len(ak)})")
    print(f"SK = {sk_raw[:8]}…{sk_raw[-4:]}  (len={len(sk_raw)})")

    ak_variants = [("as-given", ak)]
    if ak.startswith("AKLT") and len(ak) > 4:
        ak_variants.append(("no-AKLT-prefix", ak[4:]))
        try:
            dec = base64.b64decode(ak[4:] + "=" * (-len(ak[4:]) % 4)).decode("utf-8")
            ak_variants.append(("base64-of-suffix", dec))
        except Exception:  # noqa: BLE001
            pass

    sk_variants = secret_key_variants(sk_raw)
    print(f"AK 候选 {[n for n, _ in ak_variants]}；SK 候选 {[n for n, _ in sk_variants]}\n")
    for _, sk in sk_variants:
        print(f"  SK[{len(sk)}] = {sk[:10]}…{sk[-4:]}")

    ok_variant: tuple[str, str, str] | None = None
    for ak_name, ak_v in ak_variants:
        for sk_name, sk_v in sk_variants:
            code, text = call(ak_v, sk_v, REQ_KEY_JIMENG_40)
            tag = "OK?" if ("InvalidCredential" not in text and "SignatureDoesNotMatch" not in text
                            and "InvalidAuthorization" not in text) else "bad-sign"
            print(f"\n[4.0] AK={ak_name} SK={sk_name} -> HTTP {code} [{tag}]\n{text}")
            if tag == "OK?" and ok_variant is None:
                ok_variant = (ak_name, sk_name, sk_v)
    if ok_variant is None:
        print("\n== 所有 AK/SK 形态签名均被拒 ==")
        return 1

    ak_name, sk_name, sk_v = ok_variant
    print(f"\n== 签名可用：AK={ak_name} / SK={sk_name}；继续试 req_key ==")
    ak_v = dict(ak_variants)[ak_name]
    for req_key in [REQ_KEY_JIMENG_46, "jimeng_t2i_v46", "jimeng_high_aes_general_v30",
                    "jimeng_image2image_dream", "jimeng_seedream4_t2i"]:
        code, text = call(ak_v, sk_v, req_key)
        print(f"\n[req_key={req_key}] HTTP {code}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
