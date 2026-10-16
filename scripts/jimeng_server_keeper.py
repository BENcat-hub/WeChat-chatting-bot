"""Keep the local jimeng image server alive (unattended nights).

Why: the recovery experiments assume ``http://127.0.0.1:8790`` answers. A crashed
server used to be noticed only when a user request failed. This keeper polls
``/v1/status`` and restarts the server after two consecutive failures.

Design notes (deliberately conservative):

* read-only by default except for a restart, which is bounded by ``--max-restarts``;
* it never touches anything but ``jimeng_image_server.py`` processes;
* every event is one JSON line, so ``tail_jimeng_log.py``-style greps keep working;
* ``logs/pids.json`` is rewritten after a restart so other tools see the live pid.

Usage::

    & astrbot\\.venv\\Scripts\\python.exe -X utf8 scripts\\jimeng_server_keeper.py --max-hours 3.2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = "http://127.0.0.1:8790"
PIDS = ROOT / "logs" / "pids.json"
PY = ROOT / "astrbot" / ".venv" / "Scripts" / "python.exe"
HEARTBEAT_EVERY = 10  # checks between heartbeat lines (~10 min at --every 60)


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def emit(**fields) -> None:
    fields.setdefault("ts", now())
    print(json.dumps(fields, ensure_ascii=False), flush=True)


def status(timeout: float = 10.0) -> dict | None:
    try:
        with urllib.request.urlopen(SERVER + "/v1/status", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - unreachable is a normal branch here
        return None


def server_processes() -> list[int]:
    """PIDs whose command line runs jimeng_image_server (venv python + uv child)."""
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'jimeng_image_server' } | "
                "ForEach-Object { $_.ProcessId }",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        emit(event="ps_failed", detail=f"{type(exc).__name__}: {exc}")
        return []
    return [int(line) for line in out.split() if line.strip().isdigit()]


def kill_servers() -> list[int]:
    killed = []
    for pid in server_processes():
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=30)
            killed.append(pid)
        except Exception as exc:  # noqa: BLE001
            emit(event="kill_failed", pid=pid, detail=f"{type(exc).__name__}: {exc}")
    return killed


def restart() -> int | None:
    killed = kill_servers()
    if killed:
        emit(event="killed", pids=killed)
        time.sleep(2)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = ROOT / "logs" / f"jimeng-{stamp}.log"
    err = ROOT / "logs" / f"jimeng-{stamp}.err.log"
    proc = subprocess.Popen(
        [str(PY), "-X", "utf8", "scripts/jimeng_image_server.py", "--port", "8790"],
        cwd=str(ROOT),
        stdout=log.open("ab"),
        stderr=err.open("ab"),
    )
    emit(event="restart", pid=proc.pid, log=str(log.relative_to(ROOT)).replace("\\", "/"))
    time.sleep(8)
    state = status()
    if not state:
        emit(event="restart_failed", pid=proc.pid)
        return None
    emit(event="restart_ok", pid=proc.pid, ok=state.get("ok"))
    try:
        data = json.loads(PIDS.read_text(encoding="utf-8")) if PIDS.exists() else {}
        section = data.setdefault("jimeng", {})
        section["pid"] = proc.pid
        section["log"] = str(log)
        section["err"] = str(err)
        section["workdir"] = str(ROOT)
        PIDS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(event="pids_updated", pid=proc.pid)
    except Exception as exc:  # noqa: BLE001
        emit(event="pids_failed", detail=f"{type(exc).__name__}: {exc}")
    return proc.pid


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=60.0)
    ap.add_argument("--max-hours", type=float, default=3.0)
    ap.add_argument("--max-restarts", type=int, default=5)
    ap.add_argument("--fail-threshold", type=int, default=2)
    args = ap.parse_args()

    deadline = time.time() + args.max_hours * 3600
    emit(event="keeper_start", every=args.every, max_hours=args.max_hours,
         max_restarts=args.max_restarts)
    failures = 0
    restarts = 0
    checks = 0
    while time.time() < deadline:
        checks += 1
        state = status()
        if state and state.get("ok"):
            if failures:
                emit(event="recovered", after_failures=failures)
            failures = 0
            if checks % HEARTBEAT_EVERY == 0:
                emit(event="heartbeat", checks=checks, restarts=restarts,
                     last_upstream_success_ts=state.get("last_upstream_success_ts", ""),
                     req_key_denied=state.get("req_key_denied", {}))
        else:
            failures += 1
            emit(event="unreachable", consecutive=failures, threshold=args.fail_threshold)
            if failures >= args.fail_threshold:
                if restarts >= args.max_restarts:
                    emit(event="keeper_done", reason="max restarts reached", restarts=restarts)
                    return 1
                restarts += 1
                if restart() is None:
                    failures = 0  # give the next cycle a fresh threshold
                else:
                    failures = 0
        time.sleep(args.every)
    emit(event="keeper_done", reason="max hours reached", checks=checks, restarts=restarts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
