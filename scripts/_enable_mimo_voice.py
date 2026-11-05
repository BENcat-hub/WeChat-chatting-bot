"""恢复语音链路：把 TTS（和可选 STT）provider 写进 AstrBot 配置。

背景：11-03 的误删把 `data/cmd_config.json` 里原来的 provider 全带走了，只剩
DeepSeek 一个。AstrBot 4.29.1 自带 `edge_tts`（免费、不需 key）和 `mimo_tts_api`
/ `mimo_stt_api` 三个 adapter，所以语音可以「先能用、再换成原音色」。

用法：
    python scripts/_enable_voice.py --engine edge          # 免 key，立刻可用
    python scripts/_enable_voice.py --engine mimo          # 需要 key
    python scripts/_enable_voice.py --engine mimo --dry-run

key 来源（mimo）：环境变量 MIMO_API_KEY，或 secrets/mimo_api_key.txt 第一行。
没有 key 时脚本以退出码 2 结束，绝不改配置。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "astrbot" / "data" / "cmd_config.json"
KEY_FILE = ROOT / "secrets" / "mimo_api_key.txt"
MIMO_BASE = "https://api.xiaomimimo.com/v1"

EDGE_ENTRY = {
    "id": "edge_tts",
    "provider": "microsoft",
    "type": "edge_tts",
    "provider_type": "text_to_speech",
    "enable": True,
    "edge-tts-voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+0%",
    "volume": "+0%",
    "pitch": "+0Hz",
    "timeout": 20,
}


def read_mimo_key() -> str:
    env = (os.environ.get("MIMO_API_KEY") or "").strip()
    if env:
        return env
    if KEY_FILE.is_file():
        for line in KEY_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return ""


def probe(key: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        MIMO_BASE + "/models",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return True, resp.read(200).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 探活只需错误原文
        return False, f"{type(exc).__name__}: {exc}"


def upsert(cfg: dict, entry: dict) -> bool:
    provs = cfg.setdefault("provider", [])
    for i, p in enumerate(provs):
        if p.get("id") == entry["id"]:
            merged = {**p, **entry}
            if merged != p:
                provs[i] = merged
                return True
            return False
    provs.append(entry)
    return True


def set_settings(cfg: dict, key: str, provider_id: str, extra: dict | None = None) -> bool:
    block = cfg.setdefault(key, {})
    changed = False
    for field, value in {"enable": True, "provider_id": provider_id, **(extra or {})}.items():
        if block.get(field) != value:
            block[field] = value
            changed = True
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["edge", "mimo"], default="edge")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-probe", action="store_true")
    args = ap.parse_args()

    raw = CFG.read_text(encoding="utf-8-sig")
    cfg = json.loads(raw)
    changed = False

    if args.engine == "edge":
        changed |= upsert(cfg, dict(EDGE_ENTRY))
        changed |= set_settings(cfg, "provider_tts_settings", "edge_tts")
        summary = "edge_tts（免 key，zh-CN-XiaoxiaoNeural）"
    else:
        key = read_mimo_key()
        if not key:
            print(f"[voice] 需要 MiMo key：写进 {KEY_FILE} 或设 MIMO_API_KEY", file=sys.stderr)
            return 2
        if not args.skip_probe:
            ok, detail = probe(key)
            print(f"[voice] mimo probe {'ok' if ok else 'failed'}: {detail}")
            if not ok:
                print("[voice] 探活失败，未改动配置（--skip-probe 可强制写入）", file=sys.stderr)
                return 1
        changed |= upsert(cfg, {
            "id": "mimo_tts", "provider": "mimo", "type": "mimo_tts_api",
            "provider_type": "text_to_speech", "enable": True, "api_key": key,
            "api_base": MIMO_BASE, "model": "mimo-v2.5-tts",
            "mimo-tts-voice": "mimo_default", "mimo-tts-format": "wav",
            "mimo-tts-style-prompt": "", "mimo-tts-dialect": "",
            "mimo-tts-seed-text": "Hello, MiMo, have you had lunch?",
            "timeout": 20, "proxy": "",
        })
        changed |= upsert(cfg, {
            "id": "mimo_stt", "provider": "mimo", "type": "mimo_stt_api",
            "provider_type": "speech_to_text", "enable": True, "api_key": key,
            "api_base": MIMO_BASE, "model": "mimo-v2.5-asr", "timeout": 20, "proxy": "",
        })
        changed |= set_settings(cfg, "provider_tts_settings", "mimo_tts")
        changed |= set_settings(cfg, "provider_stt_settings", "mimo_stt")
        summary = "mimo_tts + mimo_stt"

    if not changed:
        print(f"[voice] 已是目标状态：{summary}")
        return 0
    if args.dry_run:
        print(f"[voice] dry-run：将启用 {summary}")
        return 0

    backup = CFG.with_suffix(".json.bak-voice")
    if not backup.exists():
        backup.write_text(raw, encoding="utf-8-sig")
        print(f"[voice] 备份 -> {backup.name}")
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
    print(f"[voice] 已启用 {summary}；重启 AstrBot 生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
