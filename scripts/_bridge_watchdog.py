# -*- coding: utf-8 -*-
"""服务看门狗：端口掉了就把那个服务拉回来（2026-11-03 误删后重写）。

原来是「bridge 看门狗」，只盯着 8766；现在统一盯整条本地链路，
因为 11-03 那次停电 + 误删之后，bridge 活着但 AstrBot/生图/语音/点歌
全死的情况也能发生，只盯一个端口等于没盯。

- 只重启「端口不在听」的服务，不碰微信/WeFlow（那是 GUI，起不来要人扫码）。
- 每个服务每小时最多重启 3 次，避免死循环刷进程。
- 单实例：logs/_watchdog.lock 里的 pid 还活着就不再起第二个。
- 日志：logs/bridge-watchdog.log
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
LOGS.mkdir(parents=True, exist_ok=True)
LOG = LOGS / "bridge-watchdog.log"
LOCK = LOGS / "_watchdog.lock"

AST = ROOT / "astrbot"
AST_PY = AST / ".venv" / "Scripts" / "python.exe"
# 2026-11-04：astrbot.exe 是 console 脚本，拉起时必建一个黑控制台窗口，
# 改用 GUI 子系统的 pythonw + 入口脚本（实测 conhost 计数 +1 → +0）。
AST_PYW = AST / ".venv" / "Scripts" / "pythonw.exe"
AST_ENTRY = ROOT / "scripts" / "_astrbot_entry.py"
BRIDGE = ROOT / "akasha-bridge"
BR_PY = BRIDGE / ".venv" / "Scripts" / "python.exe"
RVC = ROOT / "rvc-backend"
RVC_PY = RVC / ".venv" / "Scripts" / "python.exe"
# 2026-11-05：RapidOCR 常驻服务（.venv-ocr，命名管道）。没有它 wxui 只能退回
# Windows 自带 OCR —— 微信侧栏 12px 的会话名它读不出来，发送会一路重试到超时。
OCR_PY = ROOT / ".venv-ocr" / "Scripts" / "python.exe"
OCR_SERVER = ROOT / "scripts" / "ocr_server.py"
OCR_PIPE = r"\\.\pipe\wxui-ocr"

INTERVAL = int(os.environ.get("WATCHDOG_INTERVAL", "45"))
SPAWN_GRACE = int(os.environ.get("WATCHDOG_SPAWN_GRACE", "150"))  # 冷启动宽限（秒）
MAX_RESTART_PER_HOUR = 3
# bridge 允许「端口在听但还没连上 AstrBot」的宽限（秒）；超过就当作半死重启。
BRIDGE_LINK_GRACE = int(os.environ.get("WATCHDOG_LINK_GRACE", "150"))
MAX_BRIDGE_RESTART_PER_HOUR = 6


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        if LOG.is_file() and LOG.stat().st_size > 2_000_000:
            back = LOG.with_suffix(".log.1")
            if back.is_file():
                back.unlink()
            LOG.rename(back)
    except OSError:
        pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _netstat_rows() -> list[list[str]]:
    """netstat -ano 的行；拿不到就返回空（等于「不知道」，不触发重启）。"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=15).stdout
    except Exception:  # noqa: BLE001
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP":
            rows.append(parts)  # [TCP, local, remote, state, pid]
    return rows


def pids_on_port(port: int) -> set[int]:
    pids: set[int] = set()
    for parts in _netstat_rows():
        local, state, pid = parts[1], parts[3], parts[4]
        if state.upper() == "LISTENING" and local.rsplit(":", 1)[-1] == str(port):
            try:
                pids.add(int(pid))
            except ValueError:
                pass
    return pids


def linked_to(port: int) -> bool:
    """有没有任意进程跟 <port> 建立了 ESTABLISHED（bridge 连 AstrBot 的凭据）。"""
    for parts in _netstat_rows():
        remote, state = parts[2], parts[3]
        if state.upper() == "ESTABLISHED" and remote.rsplit(":", 1)[-1] == str(port):
            return True
    return False


def _env(**extra: str) -> dict:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.update(extra)
    return env


def _spawn(name: str, cmd: list[str], cwd: Path, env: dict) -> None:
    out = LOGS / f"{name}.out.log"
    err = LOGS / f"{name}.err.log"
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    with out.open("ab") as fo, err.open("ab") as fe:
        subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=fo, stderr=fe,
                         stdin=subprocess.DEVNULL, creationflags=flags,
                         close_fds=True)
    log(f"已拉起 {name}: {' '.join(cmd[0:2])}")


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def _spawn_elevated(name: str, cmd: list[str], cwd: Path) -> None:
    """以 High 完整性拉起进程（本机 UAC 是「不提示直接提升」，不会弹框）。

    为什么必须提权：2026-11-05 实测，微信是 High 完整性进程，而 DSH 桌面拉起的
    进程是 **Low** 完整性 ⇒ UIPI 直接拦掉键鼠注入（SetCursorPos 返回 0、
    ShowWindow 报 ACCESS_DENIED），表现就是 bridge 里成片「send 超时（45s）」。
    提权后同一段代码 SetCursorPos 返回 1、光标真的动。
    """
    arg_list = ",".join(_ps_quote(a) for a in cmd[1:])
    ps = (
        "Start-Process -FilePath {exe} -ArgumentList @({args}) "
        "-WorkingDirectory {cwd} -WindowStyle Hidden -Verb RunAs"
    ).format(exe=_ps_quote(cmd[0]), args=arg_list, cwd=_ps_quote(cwd))
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    # 必须是 CREATE_NO_WINDOW，不能是 DETACHED_PROCESS：2026-11-07 实测，
    # 用 DETACHED_PROCESS 起 powershell 时 `Start-Process -Verb RunAs` 会**静默不做事**
    # （rc=0、无输出、无进程），watchdog 连着两次「已提权拉起」但 8766 一直没回来；
    # 换成 CREATE_NO_WINDOW 后同一段命令立刻生效（拿到子进程 pid，端口 8 秒内起来）。
    elev_log = open(LOGS / "watchdog-elevate.log", "a", encoding="utf-8", errors="replace")
    elev_log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} spawn {name}: {ps}\n")
    elev_log.flush()
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                     cwd=str(cwd), stdout=elev_log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    log(f"已提权拉起 {name}: {' '.join(cmd[0:2])}")


def pipe_exists(name: str = OCR_PIPE) -> bool:
    try:
        import glob
        return bool(glob.glob(name))
    except Exception:
        return False


SERVICES = [
    dict(name="astrbot", port=11229, cwd=AST,
         cmd=[str(AST_PYW), "-X", "utf8", str(AST_ENTRY), "run"],
         env=lambda: _env(ASTRBOT_RELOAD="1",
                          DEEPSEEK_API_KEY=os.environ.get("DEEPSEEK_API_KEY", ""))),
    dict(name="bridge", port=8766, cwd=BRIDGE, elevated=True,
         cmd=[str(BR_PY), "-X", "utf8", "main.py"], env=lambda: _env()),
    dict(name="jimeng", port=8790, cwd=ROOT,
         cmd=[str(AST_PY), "-X", "utf8", str(ROOT / "scripts" / "jimeng_image_server.py"),
              "--port", "8790"],
         env=lambda: _env(JIMENG_BACKEND="sf")),
    dict(name="nahida-tts", port=8795, cwd=ROOT,
         cmd=[str(RVC_PY), "-X", "utf8", str(ROOT / "scripts" / "nahida_tts_server.py"),
              "--port", "8795"], env=lambda: _env()),
    # 2026-11-06：成品要落桌面「翻唱A-B试听」，只有 High 完整性进程写得进去
    # （Medium 的 python 被沙箱拒）。所以走提权路径，和 bridge 一样。
    dict(name="cover", port=3333, cwd=RVC, elevated=True,
         cmd=[str(RVC_PY), "-X", "utf8", "server.py", "--port", "3333"], env=lambda: _env()),
]


def alive(pid: int) -> bool:
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def main() -> int:
    if LOCK.is_file():
        try:
            pid = int(LOCK.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid != os.getpid() and alive(pid):
            log(f"看门狗已在运行 pid={pid}，本次退出")
            return 0
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    log(f"看门狗启动 pid={os.getpid()} interval={INTERVAL}s 监控 "
        + ", ".join(f"{s['name']}:{s['port']}" for s in SERVICES))

    history: dict[str, list[float]] = {s["name"]: [] for s in SERVICES}
    spawned_at: dict[str, float] = {}
    unlinked_since: dict[str, float] = {}
    while True:
        try:
            for svc in SERVICES:
                name, port = svc["name"], svc["port"]
                up = port_open(port)
                half_dead = False
                if up and name == "bridge":
                    # 8766 在听不等于它还在工作：要看到它跟 11229 有 ESTABLISHED。
                    if linked_to(11229):
                        unlinked_since.pop(name, None)
                    else:
                        since = unlinked_since.setdefault(name, time.time())
                        waited = time.time() - since
                        if waited >= BRIDGE_LINK_GRACE:
                            half_dead = True
                            log(f"bridge:8766 在听但 {int(waited)}s 没连上 11229（半死）-> 重启")
                else:
                    unlinked_since.pop(name, None)
                if up and not half_dead:
                    continue
                now = time.time()
                hist = [t for t in history[name] if now - t < 3600]
                history[name] = hist
                cap = MAX_BRIDGE_RESTART_PER_HOUR if name == "bridge" else MAX_RESTART_PER_HOUR
                if len(hist) >= cap:
                    log(f"{name}:{port} 一小时内已重启 {len(hist)} 次，先不再试")
                    continue
                # 冷启动宽限：AstrBot 从 pythonw 起到 11229 真正监听要 50~70 秒，
                # 而 INTERVAL 只有 45 秒 ⇒ 上一版会在启动途中再拉一个实例，第二个
                # 撞 `Error: Cannot acquire lock file` 白退（2026-11-07 01:44 实测）。
                last = spawned_at.get(name, 0.0)
                if now - last < SPAWN_GRACE:
                    log(f"{name}:{port} 刚拉过 {int(now - last)}s，还在冷启动，先等")
                    continue
                if not half_dead:
                    log(f"{name}:{port} 不在听 -> 重启")
                history[name].append(now)
                spawned_at[name] = now
                unlinked_since.pop(name, None)
                if half_dead:
                    for pid in pids_on_port(port):
                        try:
                            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                                           capture_output=True, timeout=15)
                        except Exception:  # noqa: BLE001
                            pass
                    time.sleep(2)
                try:
                    if svc.get("elevated"):
                        _spawn_elevated(name, svc["cmd"], svc["cwd"])
                    else:
                        _spawn(name, svc["cmd"], svc["cwd"], svc["env"]())
                except Exception as exc:  # noqa: BLE001
                    log(f"{name} 启动失败: {exc}")
                time.sleep(3)
            # OCR 常驻服务：没有它 wxui 读不出会话名（发送会一路超时）
            if OCR_PY.is_file() and OCR_SERVER.is_file() and not pipe_exists():
                now = time.time()
                hist = [t for t in history.setdefault("ocr", []) if now - t < 3600]
                history["ocr"] = hist
                if len(hist) < MAX_RESTART_PER_HOUR:
                    history["ocr"].append(now)
                    log("OCR 命名管道不在 -> 提权拉起 ocr_server.py --serve")
                    try:
                        _spawn_elevated("ocr",
                                        [str(OCR_PY), "-X", "utf8", str(OCR_SERVER), "--serve", "--idle", "0"],
                                        ROOT)
                    except Exception as exc:  # noqa: BLE001
                        log(f"ocr 启动失败: {exc}")
                    time.sleep(3)
            if not port_open(5031):
                pass  # WeFlow 是 GUI，等人开；这里只保持安静
        except Exception as exc:  # noqa: BLE001
            log(f"主循环异常: {exc}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
