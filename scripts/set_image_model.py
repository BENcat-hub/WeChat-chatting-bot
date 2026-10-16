"""一键切换 private_companion 的「在线生图」主/备模型配置。

用法（默认只预览，不改文件）：

    python scripts/set_image_model.py --platform doubao ^
        --base-url https://ark.cn-beijing.volces.com/api/v3 ^
        --model doubao-seedream-4-0-250828 --key-file secrets/jimeng_api_key.txt

    # 确认无误后加上 --apply 真正写入，--test 会顺手打一次生图接口
    python scripts/set_image_model.py ... --apply --test

说明：
- 只改插件配置 `astrbot/data/config/astrbot_plugin_private_companion_config.json`；
- 这些键在文件里出现两次（顶层 + 嵌套配置），脚本会把**所有**同名键一起改，避免面板与运行态不一致；
- 写入前自动备份到 `logs/backup-image-model-<时间戳>.json`；写入后校验 JSON 可解析；
- 改完需要让 AstrBot 重新加载插件（重启 AstrBot 最稳）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_CFG = REPO / "astrbot" / "data" / "config" / "astrbot_plugin_private_companion_config.json"

MAIN_KEYS = {
    "platform": "external_image_api_platform",
    "base_url": "EXTERNAL_IMAGE_API_BASE_URL",
    "key": "EXTERNAL_IMAGE_API_KEY",
    "model": "EXTERNAL_IMAGE_API_MODEL",
}
BACKUP_KEYS = {
    "platform": "backup_external_image_api_platform",
    "base_url": "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
    "key": "BACKUP_EXTERNAL_IMAGE_API_KEY",
    "model": "BACKUP_EXTERNAL_IMAGE_API_MODEL",
}


def walk_set(node: object, key: str, value: str, changes: list[tuple[str, object, str]]) -> None:
    """递归把 dict 里所有名为 key 的字段改成 value，并记录改动。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key and not isinstance(v, (dict, list)):
                if v != value:
                    changes.append((key, v, value))
                node[k] = value
            else:
                walk_set(v, key, value, changes)
    elif isinstance(node, list):
        for item in node:
            walk_set(item, key, value, changes)


def probe(base_url: str, key: str, model: str, size: str = "1024x1024") -> None:
    url = base_url.rstrip("/") + "/images/generations"
    body = json.dumps({"model": model, "prompt": "一只橘猫坐在窗台上", "size": size,
                       "response_format": "url"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + key, "Content-Type": "application/json"})
    print(f"[test] POST {url}  model={model}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print(f"[test] HTTP {resp.status}: {resp.read(400).decode('utf-8', 'replace')}")
    except urllib.error.HTTPError as exc:
        print(f"[test] HTTP {exc.code}: {exc.read(400).decode('utf-8', 'replace')}")
    except Exception as exc:  # noqa: BLE001
        print(f"[test] ERR {type(exc).__name__}: {str(exc)[:200]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", help="主生图平台，如 doubao / openai")
    ap.add_argument("--base-url", help="主生图 API 地址")
    ap.add_argument("--model", help="主生图模型 ID")
    ap.add_argument("--key", help="主生图 API Key（明文）")
    ap.add_argument("--key-file", help="从文件读取 API Key（一行）")
    ap.add_argument("--backup-platform")
    ap.add_argument("--backup-base-url")
    ap.add_argument("--backup-model")
    ap.add_argument("--backup-key")
    ap.add_argument("--backup-key-file")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预览）")
    ap.add_argument("--test", action="store_true", help="写入/预览后打一次生图接口")
    args = ap.parse_args()

    def read_key(inline: str | None, path: str | None) -> str | None:
        if inline:
            return inline.strip()
        if path:
            return (REPO / path).read_text(encoding="utf-8-sig").strip()
        return None

    main_key = read_key(args.key, args.key_file)
    backup_key = read_key(args.backup_key, args.backup_key_file)

    raw_bytes = PLUGIN_CFG.read_bytes()
    has_bom = raw_bytes.startswith(b"\xef\xbb\xbf")  # 插件配置带 BOM，写回时要保持原样
    raw = raw_bytes.decode("utf-8-sig")
    config = json.loads(raw)

    changes: list[tuple[str, object, str]] = []
    wanted_main = {"platform": args.platform, "base_url": args.base_url, "model": args.model, "key": main_key}
    wanted_backup = {"platform": args.backup_platform, "base_url": args.backup_base_url,
                     "model": args.backup_model, "key": backup_key}
    for slot, value in wanted_main.items():
        if value:
            walk_set(config, MAIN_KEYS[slot], value, changes)
    for slot, value in wanted_backup.items():
        if value:
            walk_set(config, BACKUP_KEYS[slot], value, changes)

    if not changes:
        print("没有任何字段需要改动（参数为空或值已一致）。")
    for key, old, new in changes:
        shown_old = "…" if key.endswith("_KEY") else old
        shown_new = "…" if key.endswith("_KEY") else new
        print(f"  {key}: {shown_old!r} -> {shown_new!r}")

    if not args.apply:
        print("dry-run：未写入任何文件。加 --apply 生效。")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = REPO / "logs" / f"backup-image-model-{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PLUGIN_CFG, backup)
        text = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
        PLUGIN_CFG.write_text(text, encoding="utf-8")
        json.loads(PLUGIN_CFG.read_text(encoding="utf-8"))  # 写后自检
        print(f"已写入 {PLUGIN_CFG}（备份：{backup}）；记得重启 AstrBot 让插件重新加载。")

    if args.test and args.base_url and main_key and args.model:
        probe(args.base_url, main_key, args.model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
