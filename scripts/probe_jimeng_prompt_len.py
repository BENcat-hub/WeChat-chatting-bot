"""探测即梦(seedream46_cvtob / t2i_v40) prompt 长度上限。

直接打本地 jimeng_image_server（127.0.0.1:8790），提交不同长度的自然语言提示词，
打印 HTTP 状态与上游返回，用来决定服务端 prompt 截断上限。
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"

UNIT = (
    "a young woman with long wavy blue gradient hair stands in a sunlit living room, "
    "wearing a navy blue maid dress with white ruffled apron and a blue whale embroidery, "
    "white thigh-high stockings, black mary jane shoes, "
    "soft morning light comes through the tall window behind her, "
    "shallow depth of field, 50mm lens, warm color grading, "
)


def build_prompt(target_chars: int) -> str:
    text = "Create a single coherent image showing "
    while len(text) < target_chars:
        text += UNIT
    return text[:target_chars].rstrip(" ,") + "."


def submit(model: str, prompt: str) -> tuple[int, dict]:
    body = json.dumps(
        {"model": model, "prompt": prompt, "n": 1, "size": "1024x1024", "response_format": "url"},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/v1/images/generations",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as rsp:
            return rsp.status, json.loads(rsp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw[:600]}


def main() -> None:
    for chars in (800, 1200, 2000, 3000):
        prompt = build_prompt(chars)
        status, data = submit("jimeng-4.6", prompt)
        if status == 200:
            url = (data.get("data") or [{}])[0].get("url", "")
            print(f"[len={len(prompt):5d}] HTTP {status} OK  url={url}")
        else:
            print(f"[len={len(prompt):5d}] HTTP {status} ERR {json.dumps(data, ensure_ascii=False)[:400]}")


if __name__ == "__main__":
    main()
