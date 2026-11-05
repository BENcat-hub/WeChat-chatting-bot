#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 WeFlow 的 myWxid 对账到「微信此刻在写的号」。

2026-10-30 / 11-04 两次翻车都是同一件事：微信转了号（或重登到了另一个 wxid），
而 `%APPDATA%\\weflow\\WeFlow-config.json` 里的 `myWxid` 还停在旧号上。
WeFlow 于是读旧号的库 —— 消息真发出去了，回读永远找不到，重试就变成重复发送。

判据只有一条：`xwechat_files\\wxid_*\\db_storage\\message\\message_0.db` 谁还在长。

用法:
    python scripts/wxui_account_align.py            # 只看，不改
    python scripts/wxui_account_align.py --auto     # 对不上就改（改前备份）
    python scripts/wxui_account_align.py --auto --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
WEFLOW_CONFIG = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "weflow", "WeFlow-config.json")
WXUI_CONFIG = os.path.join(HERE, "wxui-config.json")


def load_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return default


def wechat_root() -> str:
    cfg = load_json(WXUI_CONFIG, {}) or {}
    return cfg.get("xwechat_files_root") or os.path.join(
        os.path.expanduser("~"), "Documents", "xwechat_files"
    )


def active_account(root: str) -> tuple[str, float]:
    """返回 (wxid, message_0.db 的 mtime)；分不出就 ('', 0)。"""
    best, best_ts = "", 0.0
    try:
        names = [d for d in os.listdir(root) if d.lower().startswith("wxid_")]
    except OSError:
        return "", 0.0
    for name in names:
        db = os.path.join(root, name, "db_storage", "message", "message_0.db")
        try:
            ts = os.path.getmtime(db)
        except OSError:
            continue
        if ts > best_ts:
            best, best_ts = name, ts
    return best, best_ts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="对账 WeFlow myWxid 与微信在写的号")
    ap.add_argument("--auto", action="store_true", help="对不上就写回 WeFlow-config.json（先备份）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--fresh-min", type=float, default=10.0,
                    help="活动号的库要在这个分钟数内动过，否则不算活动号（默认 10）")
    args = ap.parse_args(argv)

    root = wechat_root()
    active, mtime = active_account(root)
    cfg = load_json(WEFLOW_CONFIG, None)
    report = {
        "wechat_root": root,
        "active": active,
        "active_age_min": round((time.time() - mtime) / 60, 1) if mtime else None,
        "weflow_config": WEFLOW_CONFIG,
        "weflow_myWxid": (cfg or {}).get("myWxid", ""),
        "changed": False,
        "action": "none",
    }

    if cfg is None:
        report["action"] = "weflow-config-missing"
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
              "✗ 找不到 %s（WeFlow 没跑过？）" % WEFLOW_CONFIG)
        return 2
    if not active:
        report["action"] = "no-account-found"
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
              "✗ %s 下没有 wxid_* 目录" % root)
        return 2
    if report["active_age_min"] is not None and report["active_age_min"] > args.fresh_min:
        report["action"] = "stale-account"
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
              "✗ 最活跃的库也是 %.1f 分钟前的（微信可能没登录）：%s"
              % (report["active_age_min"], active))
        return 3
    if report["weflow_myWxid"] == active:
        report["action"] = "aligned"
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
              "✓ 已经对上：%s" % active)
        return 0

    known = (cfg.get("wxidConfigs") or {})
    report["decrypt_key_present"] = bool((known.get(active) or {}).get("decryptKey"))
    if not args.auto:
        report["action"] = "mismatch"
        msg = "≠ 对不上：WeFlow=%s / 微信在写=%s%s" % (
            report["weflow_myWxid"] or "?", active,
            "（缺 decryptKey，WeFlow 需要重新扫这个号）" if not report["decrypt_key_present"] else "")
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else "⚠ " + msg)
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = WEFLOW_CONFIG + ".bak-accalign-" + stamp
    try:
        shutil.copy2(WEFLOW_CONFIG, backup)
    except OSError as exc:
        print("✗ 备份失败，未改动：%s" % exc)
        return 4
    cfg["myWxid"] = active
    tmp = WEFLOW_CONFIG + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:          # 无 BOM：WeFlow 用 JSON.parse 读
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, WEFLOW_CONFIG)
    except OSError as exc:
        print("✗ 写入失败（备份在 %s）：%s" % (backup, exc))
        return 4
    report.update(changed=True, action="realigned", backup=backup)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else
          "✓ 已对账：myWxid %s → %s（备份 %s）\n  让 WeFlow 重开一次才会按新号读库"
          % (report["weflow_myWxid"] or "?", active, os.path.basename(backup)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
