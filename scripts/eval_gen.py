"""Generate the same scene at several sizes/params and record comparable artifacts.

Purpose: every later tuning change (prompt shape, size, reference framing) must be
measurable. Each run appends one JSON line to logs/image-eval.jsonl and writes the
PNG under logs/jimeng-out/eval/.

Example:
  python scripts/eval_gen.py --tag size --arm 1024x1024 --arm 2048x2048
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = os.environ.get("JIMENG_SERVER", "http://127.0.0.1:8790")
OUT_DIR = ROOT / "logs" / "jimeng-out" / "eval"
EVAL_LOG = ROOT / "logs" / "image-eval.jsonl"
# 服务端实际生效的 req_key → 对外模型名（回退链可能把请求的 4.6 落到 4.0，见 findings §26.1）
REQ_KEY_LABELS = {
    "jimeng_seedream46_cvtob": "jimeng-4.6",
    "jimeng_t2i_v40": "jimeng-4.0",
}
DEFAULT_REF = ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_private_companion" / "photo_reference_images" / "persona_yubao_reference.png"

STYLE = "日系二次元插画风, 人物与场景干净细腻, 保留生活感, 不要写实摄影质感"
SCENE = (
    "girl with long blue gradient hair, whale-fin ear ornaments and a whale tail, wearing an ivory knit sweater and pale lounge trousers, "
    "kneeling on the bedroom floor while reaching for a mug on the low table, morning sunlight through the window"
)
IDENTITY = (
    "Identity reference contract: the attached image defines the face, hairstyle, eye colour and signature features - preserve them exactly; "
    "only scene, clothing, pose, camera and lighting may change. Keep natural adult proportions, head-to-body about 1:6-1:7, normal head size, visible neck, correct number of limbs and fingers."
)
POSE = (
    "Pose override for this generation: camera framing - high-angle medium shot; body pose - leaning on the sofa arm. "
    "This pose and framing win over any other pose wording; never copy the reference image gesture, never raise a hand next to the cheek."
)
AVOID = (
    "Avoid chibi, super-deformed, oversized head, big head on a small body, shrunken body, short stubby limbs, doll-like body, "
    "malformed hands, extra fingers, missing fingers, photorealistic rendering, mixed 2d and 3d styles, text, watermark."
)


def build_prompt(scene: str, prepend: str = "", avoid_extra: str = "", pos_extra: str = "") -> str:
    body = scene + ((". " + pos_extra.strip()) if pos_extra.strip() else "")
    avoid = AVOID + ((", " + avoid_extra.strip()) if avoid_extra.strip() else "")
    core = "Create a single coherent image showing " + body + ". " + IDENTITY + " " + POSE + " " + STYLE + ". " + avoid
    return (prepend.strip() + " " + core).strip() if prepend.strip() else core


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def submit(model: str, prompt: str, size: str, ref: Path | None, timeout: int) -> dict:
    body: dict = {"model": model, "prompt": prompt, "size": size, "n": 1, "response_format": "b64_json"}
    if ref is not None:
        body["image"] = data_url(ref)
    req = urllib.request.Request(
        SERVER + "/v1/images/edits" if ref is not None else SERVER + "/v1/images/generations",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer jimeng-local"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:400]}", "elapsed": round(time.time() - started, 1)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "elapsed": round(time.time() - started, 1)}
    return {"ok": True, "elapsed": round(time.time() - started, 1), "payload": payload}


def save_image(payload: dict, name: str) -> tuple[Path | None, int]:
    data = (payload.get("data") or [{}])[0]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / name
    if data.get("b64_json"):
        target.write_bytes(base64.b64decode(data["b64_json"]))
    elif data.get("url"):
        url = str(data["url"])
        local = ROOT / "logs" / "jimeng-out" / Path(url).name
        if Path(url).scheme in ("http", "https") and url.startswith("http://127.0.0.1"):
            with urllib.request.urlopen(url, timeout=120) as resp:
                target.write_bytes(resp.read())
        elif local.exists():
            target.write_bytes(local.read_bytes())
        else:
            return None, 0
    else:
        return None, 0
    return target, target.stat().st_size


def png_size(path: Path) -> tuple[int, int]:
    b = path.read_bytes()[:26]
    return (
        int.from_bytes(b[16:20], "big"),
        int.from_bytes(b[20:24], "big"),
    )


def downscale(path: Path, target: str) -> Path:
    from PIL import Image

    w, h = (int(part) for part in target.lower().split("x"))
    im = Image.open(path).convert("RGB")
    out = path.with_name(path.stem + "-deliver.png")
    im.resize((w, h), Image.LANCZOS).save(out, optimize=True)
    return out


def main() -> int:
    # Windows consoles default to GBK; any non-GBK character in the JSON row would
    # otherwise raise UnicodeEncodeError and kill the run mid-arm (seen 2026-10-16).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="eval")
    ap.add_argument("--arm", action="append", default=[])
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--deliver", default="")
    ap.add_argument("--model", default="jimeng-4.6")
    ap.add_argument("--scene", default=SCENE)
    ap.add_argument("--prepend", default="")
    ap.add_argument("--avoid-extra", default="")
    ap.add_argument("--pos-extra", default="")
    ap.add_argument("--prompt", default="", help="full prompt override")
    ap.add_argument("--prompt-file", default="", help="read the full prompt from a file (UTF-8; avoids shell quoting/encoding of long Chinese prompts)")
    ap.add_argument("--ref", default=str(DEFAULT_REF) if DEFAULT_REF.exists() else "")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    arms = args.arm or ["1024x1024"]
    ref = Path(args.ref) if args.ref else None
    prompt = args.prompt or build_prompt(args.scene, args.prepend, args.avoid_extra, args.pos_extra)
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    print(f"prompt_chars={len(prompt)} ref={ref.name if ref else 'none'} arms={arms} repeat={args.repeat}")
    if len(prompt) > 800:
        # 服务端会在下发前把提示词 clamp 到 800（保住结尾要求句、压缩中间场景）。
        # 2026-10-16 踩过：默认提示词 1128 字符，于是整夜的评测臂都在「被截断的提示词」上跑，
        # 却以为量的是完整提示词（见 docs/IMAGE-QUALITY-FINDINGS.md 第 27/28 节）。
        print(f"[warn] prompt_chars={len(prompt)} > 800：服务端会截断后再发给上游，"
              f"本臂量的不是完整提示词（要量完整提示词请把提示词压到 ≤800）")

    results = []
    for arm in arms:
        for run in range(1, args.repeat + 1):
            name = f"{args.tag}-{arm}-r{run}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
            res = submit(args.model, prompt, arm, ref, args.timeout)
            row = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "tag": args.tag,
                "label": args.label,
                "arm": arm,
                "run": run,
                "model": args.model,
                "size": arm,
                "ref": ref.name if ref else "",
                "prompt_chars": len(prompt),
                "ok": res["ok"],
                "elapsed": res["elapsed"],
            }
            # 请求的模型名 != 实际生效的模型：回退链（见 findings §26.1）会把
            # jimeng_seedream46_cvtob 落到 jimeng_t2i_v40，只记 `model` 会让评测数据说谎。
            req_key = str((res.get("payload") or {}).get("req_key") or "")
            row["req_key"] = req_key
            row["model_effective"] = REQ_KEY_LABELS.get(req_key, args.model)
            if res["ok"]:
                path, size = save_image(res["payload"], name)
                if path is None:
                    row["ok"] = False
                    row["error"] = "no image in payload: " + json.dumps(res["payload"], ensure_ascii=False)[:300]
                else:
                    row["file"] = str(path.relative_to(ROOT)).replace("\\", "/")
                    row["bytes"] = size
                    row["w"], row["h"] = png_size(path)
                    if args.deliver:
                        delivered = downscale(path, args.deliver)
                        row["deliver"] = str(delivered.relative_to(ROOT)).replace("\\", "/")
                        row["deliver_bytes"] = delivered.stat().st_size
                        row["deliver_w"], row["deliver_h"] = png_size(delivered)
            else:
                row["error"] = res["error"]
            EVAL_LOG.parent.mkdir(parents=True, exist_ok=True)
            with EVAL_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False))
            results.append(row)
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
