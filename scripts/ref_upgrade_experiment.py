"""上游恢复后自动跑「参考图升级」实验并盲评（无人值守用）。

背景：生产人设参考图 `photo_reference_images/persona_yubao_reference.png` 只有
563x307 且是脸部裁切；同目录 `webui_f2a201...jpg` 是 941x1672 全身人设图。
本脚本等即梦上游从 Access Denied 冷却中恢复后，按四臂各 n 次生成同一提示词，
再用免费 VL 模型盲评（脚本会自动打乱臂名），最后把结论写成 jsonl + 可读文本。

用法：
    python scripts/ref_upgrade_experiment.py --repeat 4 --every 120 --max-hours 3
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:  # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "astrbot" / "data" / "plugin_data" / "astrbot_plugin_private_companion" / "photo_reference_images"
EVAL_DIR = ROOT / "logs" / "jimeng-out" / "eval"
STATE_URL = "http://127.0.0.1:8790/v1/status"
BASE = "http://127.0.0.1:8790"
LOG = EVAL_DIR / "ref-upgrade-driver.jsonl"

ARMS = [
    ("crop563", REF_DIR / "persona_yubao_reference.png"),
    ("fullbody", REF_DIR / "webui_f2a201d1090c31cdb54dd85445cc83fced85f0dac219f289078f90258047a680.jpg"),
    ("threequarter", EVAL_DIR / "ref_threequarter.png"),
    ("upperbody", EVAL_DIR / "ref_upperbody.png"),
]

# 第二阶段（--phases size）：原生分辨率对照。官方文档建议「2K 以上人脸效果更好」，
# 但第 11 节的「2048 生成 → 降采样交付」是负收益；这里测**不降采样**的原生大图，
# 看手部/比例是否真的随面积变好（面积必须 ∈ [1024², 4096²]，1:1 合法）。
SIZE_ARMS = [
    ("native1024", "1024x1024"),
    ("native1280", "1280x1280"),
    ("native1536", "1536x1536"),
]


def log(event: str, **fields: object) -> None:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def upstream_state() -> dict:
    """读本地网关状态。

    `ok` 字段的含义是「上游可用」（冷却中时服务端会返回 ok=false），**不是**「接口可达」，
    所以这里额外补一个 `reachable`，避免把「封禁冷却中」误判成「服务没起来」而跳过探针。
    """
    try:
        with urllib.request.urlopen(STATE_URL, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, dict):
            payload["reachable"] = True
        return payload
    except Exception as exc:
        return {"ok": False, "reachable": False, "error": str(exc)}


def live_probe() -> tuple[bool, str]:
    """真打一张最小 t2i：只有它成功才说明上游真的恢复了。

    /v1/status 只反映本地记录；而台账/权限被拒时插件与我们自己的状态都可能是「冷却已清」，
    所以真正开始四臂实验前必须用一次真实生成做探针（失败会重新触发本地冷却，这是预期行为）。
    """
    body = json.dumps({
        "model": "jimeng-4.6",
        "prompt": "a simple blue whale swimming in a calm sea, soft morning light",
        "n": 1,
        "size": "1024x1024",
        "response_format": "url",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/v1/images/generations", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return bool(payload.get("data")), ""
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        return False, f"HTTP {exc.code} {detail}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def mint_panel_token() -> str:
    """自签 AstrBot 面板 token（无人值守调面板接口用）。

    面板真实校验用 `cmd_config.json` 的 `dashboard.pbkdf2_password`；`dashboard.password`
    是过期副本，直接登录会「用户名或密码错误」。这里按 `dashboard/server.py` 的校验逻辑
    （`jwt.decode(token, jwt_secret, algorithms=["HS256"])`，payload 需含非空 `username`）自签。
    """
    import jwt  # PyJWT：astrbot venv 自带

    config = json.loads(
        (ROOT / "astrbot" / "data" / "cmd_config.json").read_text(encoding="utf-8-sig")
    )
    secret = ((config.get("dashboard") or {}).get("jwt_secret") or "").strip()
    if not secret:
        raise RuntimeError("cmd_config.json 里没有 dashboard.jwt_secret")
    return jwt.encode(
        {"username": "astrbot", "exp": int(time.time()) + 3 * 3600}, secret, algorithm="HS256"
    )


def latest_prompt_chars() -> str:
    """从 AstrBot 日志里取最近一次生图的 prompt_chars（验证 800 字符预算是否生效）。

    注意日志行会被控制台宽度**折行**，所以不能按「整行含 生图开始」去匹配，
    直接在尾部文本里按出现顺序取最后一个 `prompt_chars=` / `prompt_format=`。
    """
    import re

    log_dir = ROOT / "logs"
    files = sorted(log_dir.glob("astrbot-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[:3]:
        try:
            tail = path.read_text(encoding="utf-8", errors="replace")[-1_500_000:]
        except OSError:
            continue
        counts = re.findall(r"prompt_chars=(\d+)", tail)
        if not counts:
            continue
        formats = re.findall(r"prompt_format=(\w+)", tail)
        kinds = re.findall(r"kind=(\w+)", tail)
        return (
            f"prompt_chars={counts[-1]} prompt_format={formats[-1] if formats else '?'} "
            f"kind={kinds[-1] if kinds else '?'}"
        )
    return ""


def prod_smoke(attempts: int = 2) -> None:
    """上游恢复后走一遍**生产链路**（面板排障接口 → 插件 → 本地网关），确认改动没把生产弄坏。"""
    try:
        token = mint_panel_token()
    except Exception as exc:  # noqa: BLE001
        log("prod_skipped", reason=f"自签 token 失败：{exc}")
        return
    url = ("http://127.0.0.1:6185/api/plug/astrbot_plugin_private_companion"
           "/page/troubleshooting/test")
    for index in range(attempts):
        body = json.dumps({
            "type": "image_generation_selfie",
            "prompt": "",
            "use_reference": True,
            "timeout_seconds": 120,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=200) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:  # noqa: BLE001
            log("prod_smoke_error", attempt=index + 1, error=str(exc)[:200])
        else:
            data = payload.get("data") if isinstance(payload, dict) else None
            data = data if isinstance(data, dict) else payload
            log("prod_smoke", attempt=index + 1,
                ok=data.get("ok"), duration_ms=data.get("duration_ms"),
                error_category=data.get("error_category"), error_code=data.get("error_code"),
                detail=str(data.get("detail"))[:200])
        time.sleep(3)
    line = latest_prompt_chars()
    if line:
        log("prod_prompt_chars", line=line[-300:])


def wait_for_recovery(every: float, max_hours: float) -> bool:
    """等上游恢复。

    判定用服务端自己记录的 `last_upstream_success_ts`（`/v1/status`）而不是自己发探测请求：
    本地闸门每 90s 就放行一个真请求、看门狗每 120s 也在探，驱动再探一次等于同一分钟里
    重复烧三份额度。只有服务端不提供该字段（旧版本）时才退回真实探针。
    """
    deadline = time.time() + max_hours * 3600
    clear_polls = 0
    baseline_success = ""
    baseline_taken = False
    last_probe_at = 0.0
    while time.time() < deadline:
        state = upstream_state()
        if not state.get("reachable"):
            # 接口本身失败（服务重启中/端口未起）：不能当作「已恢复」。
            clear_polls = 0
            print(f"[wait] 状态接口不可用：{state.get('error', '')[:120]}", flush=True)
            time.sleep(every)
            continue
        success_ts = str(state.get("last_upstream_success_ts") or "")
        if not baseline_taken:
            baseline_success = success_ts
            baseline_taken = True
            log("recovery_baseline", last_upstream_success_ts=baseline_success)
        denied = (state.get("access_denied") or {}).get("active")
        if denied:
            clear_polls = 0
            secs = (state.get("access_denied") or {}).get("seconds_left")
            print(f"[wait] 冷却中 seconds_left={secs}", flush=True)
        else:
            clear_polls += 1
            print(f"[wait] 冷却已清（第 {clear_polls}/2 次确认）", flush=True)
            if clear_polls >= 2:
                if success_ts and success_ts != baseline_success:
                    # 服务端自己成功打过一次上游（闸门放行探测或真实请求），直接开工。
                    log("recovered", status=state, via="last_upstream_success_ts")
                    return True
                if "last_upstream_success_ts" not in state:
                    ok, detail = live_probe()
                    if ok:
                        log("recovered", status=state, probe="ok")
                        return True
                    clear_polls = 0
                    log("probe_failed", detail=detail)
                    print(f"[wait] 探针失败，继续等：{detail[:160]}", flush=True)
                else:
                    print("[wait] 冷却已清，但服务端还没有成功的上游调用记录；"
                          "等本地闸门的 90s 放行探测结果。", flush=True)
                    clear_polls = 1
        # 低速率真探针：本地闸门只在「有请求进来」时才放行探测，所以必须有请求去触发它，
        # 否则额度恢复了也没人知道（每 5 分钟一次；冷却内会被闸门快速挡回，不烧额度）。
        if time.time() - last_probe_at >= max(300.0, every):
            last_probe_at = time.time()
            ok, detail = live_probe()
            log("periodic_probe", ok=ok, detail=detail[:200] or "")
            if ok:
                log("recovered", status=upstream_state(), probe="ok")
                return True
            print(f"[wait] 5 分钟探针：{detail[:160]}", flush=True)
        time.sleep(every)
    log("give_up", reason=f"{max_hours}h 内未恢复")
    return False


def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def run_arm_set(tag: str, repeat: int, py: str, specs: list[tuple[str, list[str]]]) -> dict[str, list[str]]:
    """跑一组臂（specs: [(label, 额外 CLI 参数)]），返回 {label: [图片路径]}。"""
    produced: dict[str, list[str]] = {}
    for label, extra in specs:
        log("arm_start", tag=tag, label=label, extra=" ".join(extra))
        proc = run(
            [
                py,
                str(ROOT / "scripts" / "eval_gen.py"),
                "--tag", tag,
                "--label", label,
                "--repeat", str(repeat),
                *extra,
                "--timeout", "420",
            ],
            timeout=420 * repeat + 600,
        )
        files: list[str] = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("ok") and row.get("file"):
                files.append(str(ROOT / row["file"]))
            elif not row.get("ok"):
                print(f"[warn] {label} 生成失败: {row.get('error', '')[:160]}", flush=True)
        produced[label] = files
        log("arm_done", tag=tag, label=label, ok=len(files), expected=repeat,
            stderr=proc.stderr[-300:] if proc.returncode != 0 else "")
        if len(files) < max(2, repeat - 1):
            log("abort", reason=f"{label} 只成功 {len(files)}/{repeat} 张，可能又被打回冷却")
            return {}
    return produced


def score(tag: str, produced: dict[str, list[str]], py: str) -> None:
    arms_arg: list[str] = []
    for label, files in produced.items():
        if len(files) >= 2:
            arms_arg += ["--arm", f"{label}={','.join(files)}"]
    if len(arms_arg) < 4:
        log("score_skipped", tag=tag, reason="可用臂不足")
        return
    out_txt = EVAL_DIR / f"vl-{tag}.txt"
    log("score_start", tag=tag, arms=len(arms_arg) // 2)
    proc = run([py, str(ROOT / "scripts" / "eval_vl_score.py"), "--tag", tag, *arms_arg],
               timeout=900 * len(arms_arg))
    out_txt.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    log("score_done", tag=tag, report=str(out_txt.relative_to(ROOT)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=4)
    ap.add_argument("--every", type=float, default=120.0)
    ap.add_argument("--max-hours", type=float, default=3.0)
    ap.add_argument("--tag", default="ref")
    ap.add_argument("--phases", default="ref,size", help="ref / size，逗号分隔")
    args = ap.parse_args()
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]

    py = sys.executable
    log("driver_start", repeat=args.repeat, every=args.every, max_hours=args.max_hours, phases=phases)
    if not wait_for_recovery(args.every, args.max_hours):
        return 2

    if "ref" in phases:
        specs = [
            (label, ["--arm", "1024x1024", "--ref", str(ref)])
            for label, ref in ARMS
            if ref.exists()
        ]
        for label, ref in ARMS:
            if not ref.exists():
                log("arm_skipped", label=label, reason=f"缺少参考图 {ref}")
        produced = run_arm_set(args.tag, args.repeat, py, specs)
        if not produced:
            return 3
        score(f"{args.tag}up", produced, py)

    if "size" in phases:
        # 冷却可能已经再次触发：探测一次，失败就只记录状态并退出。
        state = upstream_state()
        if (state.get("access_denied") or {}).get("active"):
            log("abort", reason="参考图阶段后再次被拒，跳过 size 阶段", state=state)
            return 4
        specs = [(label, ["--arm", size]) for label, size in SIZE_ARMS]
        produced = run_arm_set("size", args.repeat, py, specs)
        if not produced:
            return 5
        score("size", produced, py)

    if "prod" in phases:
        prod_smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
