#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""下传前提示词处理（flatten + clamp）的离线检查：真实日志回放 + 生产形态合成用例。

背景（2026-10-16 04:4x，见 docs/IMAGE-QUALITY-FINDINGS.md 第 27 节）：

- `scripts/jimeng_image_server.py` 发给火山之前两步：`flatten_prompt()`（去掉 `[小标题]`、
  压平换行）→ `clamp_prompt()`（收进上游 800 字符硬限制，801 就 400）；
- 日志 `logs/jimeng-server-requests.jsonl` 的 `request` 条目里 `prompt` **只存前 400 字符**，
  `prompt_len` 是「压平后、截断前」的长度 —— 所以日志里那段文本不是完整提示词，
  不能直接拿去回放 clamp（第一版检查脚本就是这么写错的）；
- 真正发出去的文本在 `stage=prompt_clamped` 的 `sent` 字段（本次新加）。

本脚本检查：

A. 合成用例（按生产提示词的真实结构：场景描写 + 风格句 + 身份锚 + `Pose override:` + `Avoid`）：
   ① 短提示词原样返回；② 876/1200/4669 字符的提示词截断后 **必须仍然含 `Pose override`、
   身份锚句与 `Avoid`**；③ 超长负面词走老兜底且 ≤800；④ 幂等、无损前缀、结尾不切半截词。
B. 日志回放：`prompt_len <= 400` 的真实提示词（完整可得）必须原样返回；
   `prompt_clamped` 条目断言截断后 ≤800、`sent` 与 `chars[1]` 一致且是幂等不动点。

用法：`python scripts/check_prompt_clamp.py [--log PATH] [--limit N] [--show 2]`
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "scripts" / "jimeng_image_server.py"
DEFAULT_LOG = ROOT / "logs" / "jimeng-server-requests.jsonl"

SCENE_CN = (
    "在一个铺着浅色木地板的日式房间里，午后的阳光从落地窗斜射进来，窗台上摆着一盆绿植，"
    "靠墙的木架上放着几本摊开的杂志和一只白色的马克杯，地板上铺着一条米色针织毯，"
    "空气中飘着细小的灰尘颗粒，整体色调偏暖，阴影边缘柔和，她正蹲在窗台边伸手去够那盆绿植，"
    "另一只手扶着窗框，身体重心微微前倾。"
)
HEAD = "Create a single coherent image showing Petite slender teen girl, small head, about 7 heads tall, long legs, "
TAIL_REQUIREMENTS = (
    "Long blue hair with light-blue gradient tips, blue eyes. "
    "Keep the reference face, hair colour, whale-fin ears and whale tail; preserve them exactly; "
    "only scene, clothing, pose, camera and lighting may change. "
    "Pose override: framing - three-quarter shot from the side; "
    "pose - crouching beside a windowsill reaching for a potted plant. "
    "Avoid chibi, oversized head, malformed hands, extra fingers, photorealistic rendering, "
    "mixed 2d and 3d styles, text, watermark."
)

DANGLING = {
    "and", "or", "but", "with", "without", "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "from", "by", "as", "into", "onto", "over", "under", "near", "above", "below", "behind",
    "beside", "between", "her", "his", "its", "their", "she", "he", "is", "are", "was", "were",
    "very", "more", "less", "than", "then", "that", "this", "framing", "pose", "override", "shot",
}


def load_server_module():
    spec = importlib.util.spec_from_file_location("jimeng_image_server_for_clamp", SERVER)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"无法加载 {SERVER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tail_problems(clamped: str, limit: int) -> list[str]:
    problems: list[str] = []
    if len(clamped) > limit:
        problems.append(f"超过上限 {len(clamped)}>{limit}")
    if clamped != clamped.rstrip():
        problems.append("结尾有多余空白")
    # 句号/问号/叹号（中英文）都是合法结尾；逗号/分号/冒号/破折号说明切在半句上。
    if re.search(r"[,;:：，；\-]\s*$", clamped):
        problems.append(f"结尾是标点：...{clamped[-24:]!r}")
    tokens = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]+", clamped[-40:])
    if tokens and tokens[-1].lower() in DANGLING:
        problems.append(f"结尾是半截词：...{clamped[-32:]!r}")
    return problems


def build_prompt(scene_repeat: int) -> str:
    return HEAD + (SCENE_CN * scene_repeat) + ", 自然室内光, 画面干净清晰, 日系二次元插画风 " + TAIL_REQUIREMENTS


def legacy_clamp(prompt: str, limit: int = 800) -> str:
    """修复前 `clamp_prompt` 的行为（只留作 A/B 基线，不再用于生产）。

    旧实现：把结尾的否定句摘出来，头部按 `limit - len(tail)` 硬切（切在句子中间也不管），
    于是「切点之外的整句要求」会被整段丢掉 —— 在本仓库的真实提示词里就是风格句与
    `Pose override:`。见 docs/IMAGE-QUALITY-FINDINGS.md 第 27 节。
    """
    match = re.search(r"(?is)(?:^|\s)((?:avoid|negative\s+prompt)\b.*)$", prompt)
    tail = match.group(1).strip() if match else ""
    budget = max(120, limit - len(tail) - 1)
    head = prompt[:budget]
    for sep in (". ", "。", "! ", "！", "? ", "？", "; ", "；", ", ", "，", " "):
        idx = head.rfind(sep)
        if idx >= int(budget * 0.6):
            head = head[: idx + len(sep)]
            break
    else:
        head = head.rstrip()
    return (head.strip() + (" " + tail if tail else "")).strip()


def eval_default_prompt() -> str:
    """评测脚手架的默认提示词（1128 字符），用于造「修复前 vs 现在」的 A/B 基线。"""
    try:
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("eval_gen_for_clamp", ROOT / "scripts" / "eval_gen.py")
        if spec and spec.loader:
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.build_prompt(mod.SCENE)
    except Exception:  # noqa: BLE001 - 缺 PIL/依赖时退回合成提示词
        pass
    return build_prompt(2)


def synthetic_cases(limit: int) -> list[tuple[str, str, list[str]]]:
    """返回 [(用例名, 提示词, 期望必须保留的片段), ...]。"""
    cases: list[tuple[str, str, list[str]]] = [
        ("短提示词(不截断)", HEAD + SCENE_CN + " " + TAIL_REQUIREMENTS, []),
        ("生产形态 876 字符", build_prompt(1), ["Pose override", "whale-fin ears", "Avoid "]),
        ("超长 1200 字符", build_prompt(3), ["Pose override", "whale-fin ears", "Avoid "]),
        ("极长 4669 字符", build_prompt(12), ["Pose override", "whale-fin ears", "Avoid "]),
    ]
    huge_avoid = "Avoid " + ", ".join(f"bad thing {i}" for i in range(60)) + "."
    cases.append(("超长负面词(老兜底)", HEAD + SCENE_CN * 3 + " " + huge_avoid, []))
    cases.append(("无句读长文本", "scene word " * 200, []))
    cases.append(("只有中文长文本", SCENE_CN * 40 + "。Avoid 变形, 水印.", []))
    return cases


def check_synthetic(module, limit: int, show: int) -> int:
    failures = 0
    shown = 0
    for name, prompt, must_keep in synthetic_cases(limit):
        flat = module.flatten_prompt(prompt)
        clamped = module.clamp_prompt(flat)
        problems = tail_problems(clamped, limit)
        if len(flat) > limit:
            for needle in must_keep:
                if needle not in clamped:
                    problems.append(f"截断后丢了要求片段 {needle!r}")
            if module.clamp_prompt(clamped) != clamped:
                problems.append("不幂等")
            head = flat[:60].strip()
            if head and head not in clamped:
                problems.append("头部丢失/重排")
        else:
            if clamped != flat:
                problems.append("未超限却被改写")
        if show and len(flat) > limit and shown < show:
            shown += 1
            print(f"--- {name}: {len(flat)} → {len(clamped)}")
            print(f"    保住的结尾：...{clamped[-140:]}")
        if problems:
            failures += 1
            print(f"[FAIL] {name}: {'；'.join(problems)}")
            print(f"       尾部：...{clamped[-120:]!r}")
    print(f"合成用例：{len(synthetic_cases(limit))} 条，失败 {failures} 条")
    return failures


def check_log_replay(module, log_path: Path, limit: int, max_rows: int) -> int:
    if not log_path.exists():
        print(f"（跳过日志回放：找不到 {log_path}）")
        return 0
    short_prompts: list[str] = []
    clamped_rows: list[dict] = []
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            stage = record.get("stage")
            if stage == "request":
                prompt = record.get("prompt")
                if isinstance(prompt, str) and prompt and record.get("prompt_len", 0) <= 400:
                    # 400 是日志截断长度：length ≤400 时这条文本就是完整提示词
                    if len(prompt) == record.get("prompt_len"):
                        short_prompts.append(prompt)
            elif stage == "prompt_clamped":
                clamped_rows.append(record)
    failures = 0
    for prompt in short_prompts[-max_rows:] if max_rows else short_prompts:
        if module.clamp_prompt(prompt) != prompt:
            failures += 1
            print(f"[FAIL] 日志回放：{len(prompt)} 字符的完整提示词被改写")
    with_sent = [row for row in clamped_rows if isinstance(row.get("sent"), str)]
    # 没有 `sent` 的行是**修复前**的历史记录（当时只记 head/tail）；它们里的 823 字符超限
    # 正是促成这次修复的证据，所以只统计、不判失败。
    historical_over = [row for row in clamped_rows
                       if not isinstance(row.get("sent"), str) and (row.get("chars") or [0, 0])[1] > limit]
    for row in with_sent:
        sent = row["sent"]
        if len(sent) > limit:
            failures += 1
            print(f"[FAIL] 日志回放：sent {len(sent)} > {limit}")
        if len(sent) != (row.get("chars") or [0, 0])[1]:
            failures += 1
            print(f"[FAIL] 日志回放：sent 长度 {len(sent)} != chars {row.get('chars')}")
        if module.clamp_prompt(sent) != sent:
            failures += 1
            print(f"[FAIL] 日志回放：sent 不是幂等不动点（{len(sent)} 字符）")
        for problem in tail_problems(sent, limit):
            failures += 1
            print(f"[FAIL] 日志回放：sent {problem}")
    kept_pose = sum(1 for row in with_sent if "Pose override" in row["sent"])
    print(f"日志回放：完整短提示词 {len(short_prompts)} 条、prompt_clamped {len(clamped_rows)} 条"
          f"（其中带 sent 的 {len(with_sent)} 条、含 `Pose override` 的 {kept_pose} 条；"
          f"修复前超限历史行 {len(historical_over)} 条），失败 {failures} 条")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="下传前 flatten/clamp 的离线检查")
    parser.add_argument("--log", default=str(DEFAULT_LOG), help="请求日志路径")
    parser.add_argument("--limit", type=int, default=800, help="上游字符上限（默认 800）")
    parser.add_argument("--show", type=int, default=0, help="打印 N 条被截断的示例")
    parser.add_argument("--max-rows", type=int, default=0, help="日志回放最多取最后 N 条（0=全部）")
    parser.add_argument("--legacy-prompt-out", default="", help="把评测默认提示词的「修复前」截断版写到该文件（A/B 基线生成用）")
    args = parser.parse_args()

    if args.legacy_prompt_out:
        prompt = eval_default_prompt()
        legacy = legacy_clamp(prompt, args.limit)
        Path(args.legacy_prompt_out).write_text(legacy, encoding="utf-8")
        print(f"评测默认提示词 {len(prompt)} 字符 → 修复前截断 {len(legacy)} 字符，"
              f"含 `Pose override`：{'Pose override' in legacy}，"
              f"含风格句（日系二次元插画风）：{'日系二次元插画风' in legacy}")
        print(f"已写入 {args.legacy_prompt_out}")
        return 0

    module = load_server_module()
    failures = check_synthetic(module, args.limit, args.show)
    print()
    failures += check_log_replay(module, Path(args.log), args.limit, args.max_rows)
    print()
    if failures:
        print("结论：存在契约问题（见上）。")
        return 1
    print("结论：全部通过（≤800 / 要求句保留 / 幂等 / 无损前缀 / 不切半截）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
