"""把私聊会话加入 WeFlow 的「自动下载高清图」白名单。

背景：WeFlow 只对 `autoDownloadWhitelist` 里的会话自动下载入向图片高清版；
不在白名单里的私聊，收到的图片没有本地缓存，WeFlow API 不会给出 mediaUrl，
akasha-bridge 只能把图片降级成文字占位符（"（图片内容无法描述）"），机器人就"看不到图"。

用法（astrbot 虚拟环境的 python）：
  python weflow_add_autodownload.py            # 只打印将要写入的白名单
  python weflow_add_autodownload.py --apply    # 备份并写入

注意：WeFlow 退出时会把内存里的配置写回该文件，所以**先退出 WeFlow 再执行 --apply**，
再启动 WeFlow，白名单才会生效。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import urllib.parse
import urllib.request

TOKEN = "<FILL_ME>"
BASE = "http://127.0.0.1:5031"
CONFIG_PATH = os.path.join(os.environ.get("APPDATA", ""), "weflow", "WeFlow-config.json")

# 不参与"看图"的会话：自己、文件传输助手、公众号/服务号
SKIP_USERNAMES = {"filehelper"}
SKIP_PREFIXES = ("gh_",)


def list_private_talkers() -> list[str]:
    query = urllib.parse.urlencode({"access_token": TOKEN, "limit": 80})
    with urllib.request.urlopen(f"{BASE}/api/v1/sessions?{query}", timeout=20) as resp:
        data = json.load(resp)
    result: list[str] = []
    for item in data.get("sessions") or []:
        username = str(item.get("username") or item.get("talker") or "")
        if not username or "@chatroom" in username:
            continue
        if username in SKIP_USERNAMES or username.startswith(SKIP_PREFIXES):
            continue
        result.append(username)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写入配置文件")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="额外追加的会话 wxid（可重复）",
    )
    args = parser.parse_args()

    try:
        talkers = list_private_talkers()
        print(f"当前私聊会话（{len(talkers)}）: {talkers}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! 读取会话失败（WeFlow 未运行？）：{exc}")
        talkers = []

    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = json.load(handle)

    old = list(config.get("autoDownloadWhitelist") or [])
    merged: list[str] = []
    for item in [*old, *talkers, *args.extra]:
        if item and item not in merged:
            merged.append(item)

    print(f"autoDownloadHighRes = {config.get('autoDownloadHighRes')!r}")
    print(f"原白名单（{len(old)}）: {old}")
    print(f"新白名单（{len(merged)}）: {merged}")
    if merged == old:
        print("白名单无变化，无需写入。")
        return 0
    if not args.apply:
        print("（dry-run，未写入；加 --apply 生效）")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{CONFIG_PATH}.bak-{stamp}"
    shutil.copy2(CONFIG_PATH, backup)
    config["autoDownloadHighRes"] = True
    config["autoDownloadWhitelist"] = merged
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    print(f"已写入 {CONFIG_PATH}\n备份: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
