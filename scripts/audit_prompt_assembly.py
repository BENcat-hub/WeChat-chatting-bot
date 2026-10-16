"""离线审计：提示词拼装契约（不需要上游能出图，也不需要参考图真的存在）。

动机：整夜的核心改动都在「拼装」这一层（预算化、尾部关键要求、两层去重），
但它平时只能靠“上游出图后看结果”验证，上游一旦被封就没法测。这个脚本复用插件
自己的可靠性测试 harness（`_PhotoReliabilityHarness` 实现了 `_generate_photo_image`
的最小依赖），把拼装结果写进 prompt 调试 JSON，再对契约做断言：

1. `natural_language` 格式最终提示词 **≤ 800 字符**（即本地服务的硬上限）；
2. `natural_language` 必须是三段式，含 `\\n\\nAvoid `（曾被预算重构弄坏的那条契约）；
3. 比例句 `about 7 heads tall` 最多出现一次（第 21 节的去重）；
4. 水印负面词在尾部（含 `watermark`）；
5. 自拍/人设类请求必含身份锚（`whale`）；
6. 姿势轮换：同一 harness 连跑 20 次，`Pose override: framing - X; pose - Y.` 组合数 ≥ 6。

用法：
    python scripts/audit_prompt_assembly.py                # 全量审计
    python scripts/audit_prompt_assembly.py --json         # 只输出 JSON
结果：logs/jimeng-out/eval/prompt-audit.json（明细）+ 终端表格（摘要）
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import importlib
import json
import pathlib
import random
import re
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

STACK = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = STACK / "astrbot" / "data" / "plugins" / "astrbot_plugin_image_companion"
for _path in (str(PLUGIN.parent), str(PLUGIN / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_mod = importlib.import_module(
    "astrbot_plugin_image_companion.tests.test_migrated_photo_prompt_reliability"
)
Harness = _mod._PhotoReliabilityHarness

NATURAL_LIMIT = 800
POSE_RE = re.compile(
    r"Pose override:\s*framing\s*-\s*([^;,.]{1,60})[;,.]?\s*pose\s*-\s*([^.]{1,80})",
    re.IGNORECASE,
)
POSE_INCOMPLETE_RE = re.compile(r"pose override(?!:\s*framing)", re.IGNORECASE)


def _cases() -> list[tuple[str, str, dict]]:
    """(标签, 格式, _generate_photo_image 关键字)。"""
    long_request = "在海边散步，风有点大，头发被吹乱了，想留一张看起来安静的侧脸近景。" * 6
    return [
        ("t2i-橘猫", "natural_language", dict(workflow_kind="text2img", prompt_text="画一只在窗台上睡觉的橘猫，阳光洒进来")),
        ("t2i-长文本", "natural_language", dict(workflow_kind="text2img", prompt_text="超长场景描述：" + "晚霞里的鲸鱼尾巴，柔和逆光。" * 20)),
        ("selfie-短", "natural_language", dict(workflow_kind="selfie", prompt_text="今天心情不错，来张自拍")),
        ("selfie-长请求", "natural_language", dict(workflow_kind="selfie", prompt_text=long_request)),
        ("selfie-场景预设", "natural_language", dict(workflow_kind="selfie", prompt_text="来张自拍", requested_scene_preset="居家睡衣")),
        ("selfie-礼服预设", "natural_language", dict(workflow_kind="selfie", prompt_text="拍一张正式一点的", requested_scene_preset="礼服人像")),
        ("explicit-自拍", "natural_language", dict(workflow_kind="explicit_selfie", prompt_text="给我一张全身自拍")),
        ("edit-改图", "natural_language", dict(workflow_kind="edit", prompt_text="把背景换成海边")),
        ("t2i-橘猫-传统", "traditional", dict(workflow_kind="text2img", prompt_text="画一只在窗台上睡觉的橘猫，阳光洒进来")),
        ("selfie-长请求-传统", "traditional", dict(workflow_kind="selfie", prompt_text=long_request)),
    ]


def _reference(root: pathlib.Path) -> dict:
    path = root / "persona.png"
    path.write_bytes(b"reference image")
    return {
        "id": "persona",
        "path": str(path),
        "source": str(path),
        "label": "人设参考图",
        "reference_roles": ["identity"],
    }


async def _run(root: pathlib.Path, fmt: str, kwargs: dict, reference: dict | None) -> dict:
    harness = Harness(root, reference)
    harness.photo_generation_prompt_format = fmt
    call = dict(kwargs)
    call.setdefault("session_key", "default:FriendMessage:10001")
    await harness._generate_photo_image(**call)
    record = harness.data["recent_photo_generations"][0]
    payload = json.loads(pathlib.Path(record["prompt_path"]).read_text(encoding="utf-8"))
    prompt = payload.get("final_prompt") or payload.get("prompt") or ""
    return {
        "prompt": prompt,
        "prompt_len": len(prompt),
        "prompt_format": payload.get("prompt_format", fmt),
        "presets": record.get("presets", []),
        "wardrobe_mode": record.get("wardrobe_mode", ""),
        "reference_used": bool(record.get("reference_used")),
        "sections": payload.get("prompt_sections_after", {}),
    }


async def audit() -> dict:
    results: list[dict] = []
    failures: list[str] = []
    notes: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        reference = _reference(root)

        for label, fmt, kwargs in _cases():
            with_ref = "t2i" not in label
            outcome = await _run(root, fmt, kwargs, reference if with_ref else None)
            prompt = outcome["prompt"]
            lower = prompt.lower()
            checks: list[tuple[str, bool, str]] = []

            if fmt == "natural_language":
                checks.append(("≤800 字符", outcome["prompt_len"] <= NATURAL_LIMIT, str(outcome["prompt_len"])))
                checks.append(("含 \\n\\nAvoid 三段式", "\n\navoid " in lower, ""))
                checks.append(("含水印负面词", "watermark" in lower, ""))
                checks.append((
                    "比例句不重复",
                    len(re.findall(r"about 7 heads tall", lower)) <= 1,
                    str(len(re.findall(r"about 7 heads tall", lower))),
                ))
                if with_ref:
                    checks.append(("含身份锚(whale)", "whale" in lower, ""))
                    checks.append(("含姿势覆盖覆盖", "pose override" in lower, ""))

            for name, ok, extra in checks:
                if not ok:
                    failures.append(f"{label}/{fmt}: {name} 失败 {extra}".strip())

            # 已知问题（不是契约失败，只提示）：纯文生图没有参考图，提示词却仍然写
            # "Keep the reference face …"（悬空引用）。详见 docs/IMAGE-QUALITY-FINDINGS.md 第 25 节。
            if not with_ref and "reference" in lower:
                notes.append(
                    f"{label}/{fmt}: 无参考图但提示词含 reference（悬空引用，已知；见 findings 第 25 节）"
                )

            results.append({
                "label": label,
                "format": fmt,
                "with_reference": with_ref,
                "prompt_len": outcome["prompt_len"],
                "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
                "head": prompt[:120],
                "tail": prompt[-180:],
                "full_prompt": prompt,
                "presets": outcome["presets"],
                "wardrobe_mode": outcome["wardrobe_mode"],
            })

        # 姿势轮换：同一 harness 连跑 20 次
        harness = Harness(root, reference)
        harness.photo_generation_prompt_format = "natural_language"
        poses: list[str] = []
        incomplete: list[int] = []
        for index in range(20):
            await harness._generate_photo_image(
                workflow_kind="selfie",
                prompt_text="来张自拍",
                session_key=f"default:FriendMessage:1000{index}",
            )
        for index, record in enumerate(harness.data["recent_photo_generations"]):
            payload = json.loads(pathlib.Path(record["prompt_path"]).read_text(encoding="utf-8"))
            prompt = payload.get("final_prompt") or payload.get("prompt") or ""
            match = POSE_RE.search(prompt)
            if match:
                poses.append(f"{match.group(1).strip()} / {match.group(2).strip()}")
            elif POSE_INCOMPLETE_RE.search(prompt):
                # 头部被裁成半截 `Pose override:`，尾部那条完整的又被去重跳过 —— 最终提示词
                # 里的姿势要求等于没有内容可说。
                incomplete.append(index)
        distinct_poses = len(set(poses))
        if distinct_poses < 6:
            failures.append(f"姿势轮换：20 次只出现 {distinct_poses} 种组合")
        if len(poses) < 20:
            failures.append(
                f"姿势完整性：20 次里只有 {len(poses)} 次含完整 `framing - …; pose - …`，"
                f"另有 {len(incomplete)} 次只剩半截 Pose override（行号 {incomplete}）"
            )

    return {
        "cases": results,
        "rotation": {
            "runs": 20,
            "with_pose": len(poses),
            "incomplete_pose": incomplete,
            "distinct_poses": distinct_poses,
            "sample": sorted(set(poses))[:8],
        },
        "failures": failures,
        "notes": notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="离线审计提示词拼装契约")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    parser.add_argument("--out", default="logs/jimeng-out/eval/prompt-audit.json")
    parser.add_argument("--seed", type=int, default=20261016, help="固定随机种子，让姿势/机位与断言可复现")
    parser.add_argument("--rounds", type=int, default=1, help="重复几轮（每轮换种子），用来发现随机抽样引起的契约失败")
    args = parser.parse_args()

    failures: list[str] = []
    reports: list[dict] = []
    for round_index in range(max(1, args.rounds)):
        random.seed(args.seed + round_index)
        report = asyncio.run(audit())
        report["seed"] = args.seed + round_index
        reports.append(report)
        failures.extend(f"seed={report['seed']} {item}" for item in report["failures"])

    report = reports[-1]
    report["rounds"] = len(reports)
    report["all_failures"] = failures
    out_path = STACK / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'用例':<18}{'格式':<18}{'长度':>6}  检查")
        for case in report["cases"]:
            marks = " ".join(f"{'✓' if c['ok'] else '✗'}{c['name']}" for c in case["checks"]) or "-"
            print(f"{case['label']:<18}{case['format']:<18}{case['prompt_len']:>6}  {marks}")
        rotation = report["rotation"]
        print(f"\n姿势轮换：20 次里 {rotation['with_pose']} 次含完整 `framing - …; pose - …`，"
              f"不同组合 {rotation['distinct_poses']} 种，"
              f"半截 {len(rotation['incomplete_pose'])} 次")
        for sample in rotation["sample"]:
            print(f"  - {sample}")
        print(f"\n明细：{out_path}")

    notes: list[str] = []
    for item in reports:
        notes.extend(f"seed={item['seed']} {note}" for note in item.get("notes", []))
    if notes:
        print(f"\n已知问题 {len(notes)} 条（不算契约失败）：")
        for item in notes:
            print(f"  ⚠ {item}")

    if report["failures"]:
        print(f"\n{len(report['failures'])} 项契约失败：")
        for item in report["failures"]:
            print(f"  ✗ {item}")
        return 1
    print("\n全部契约通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
