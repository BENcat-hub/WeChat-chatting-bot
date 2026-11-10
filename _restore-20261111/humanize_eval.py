# -*- coding: utf-8 -*-
"""A/B 测试：让陪伴插件真实回几条消息，再用客观指标对比「真人语料」。

用法：
  python _restore-20261111/humanize_eval.py --measure          # 只测最近发出去的
  python _restore-20261111/humanize_eval.py --run 8            # 发 8 条入站，等回复，再测
  python _restore-20261111/humanize_eval.py --tag v2 --run 8   # 结果存 _restore-20261111/eval-v2.json

自检对象固定为「文件传输助手」（WeFlow talker=filehelper），不打扰任何人。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from humanize_metrics import metrics, weflow_token  # noqa: E402

BASE = "http://127.0.0.1:5031"
BRIDGE = "http://127.0.0.1:8766"
TALKER = "filehelper"

PROMPTS = [
    "今天好累啊",
    "我刚吃了一碗面",
    "你在干嘛",
    "周末要不要出去走走",
    "我今天被老师说了两句",
    "推荐首歌听听",
    "晚安",
    "你会想我吗",
    "外面下雨了",
    "今天心情有点差",
    "我买了一双新鞋",
    "好饿",
    "刚跑完步回来",
    "明天要早起",
    "手机快没电了",
    "我在等公交",
    "刚把作业写完",
    "午饭还没吃",
    "今天风好大",
    "刚洗完头",
    "在收拾房间",
    "下午开会开到现在",
    "刚看完一集剧",
    "有点想回家",
    "今天不想说话",
    "食堂排队排好久",
    "刚被夸了一句",
    "脚有点酸",
    "刚把衣服晾上",
    "明天要体检",
]


def pick_prompts(n: int, offset: int) -> list[str]:
    """每轮从不同位置取，避免机器人在历史里看到重复话题后先吐槽「你又说一遍」。"""
    start = offset % len(PROMPTS)
    ring = PROMPTS[start:] + PROMPTS[:start]
    return ring[:n]


def fetch_filehelper(limit: int = 120) -> list[dict]:
    q = urllib.parse.urlencode({"talker": TALKER, "limit": limit, "access_token": weflow_token()})
    with urllib.request.urlopen(f"{BASE}/api/v1/messages?{q}", timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        for key in ("data", "messages", "items", "list"):
            if isinstance(data.get(key), list):
                return data[key]
    return data if isinstance(data, list) else []


def send(text: str) -> bool:
    body = json.dumps({"target": "文件传输助手", "text": text, "nickname": "文件传输助手"}, ensure_ascii=False).encode()
    req = urllib.request.Request(BRIDGE + "/test_cmd", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            print(f"  → {text!r} : {resp.status}")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {text!r} 注入失败: {exc}")
        return False


def replies_since(start_ts: float) -> list[str]:
    out = []
    for row in fetch_filehelper(160):
        ts = row.get("createTime") or 0
        if not isinstance(ts, (int, float)) or ts < start_ts - 5:
            continue
        if not row.get("isSend"):
            continue
        text = str(row.get("content") or "").strip()
        if not text or text.startswith("<"):
            continue
        # 图片/表情/语音不是文字，别混进风格指标
        if text.startswith("[") and text.endswith("]") and len(text) <= 8:
            continue
        out.append(text)
    return out


def report(texts: list[str], human: dict) -> dict:
    m = metrics(texts)
    if not m:
        print("没有可评估的回复")
        return {}
    print(f"\n== 机器人本次（{m['样本']} 条）")
    for k, v in m.items():
        print(f"   {k:<10} {v}")
    print("\n== 与真人语料对比（机器人 - 真人）")
    deltas = {}
    for key, val in m.items():
        if key == "样本":
            continue
        base = human.get(key)
        try:
            d = round(float(val) - float(base), 3)
        except (TypeError, ValueError):
            continue
        deltas[key] = d
        print(f"   {key:<10} {d:+.3f}")
    return {"metrics": m, "deltas": deltas, "texts": texts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, default=0, help="发几条入站触发回复")
    ap.add_argument("--gap", type=float, default=25.0, help="每条之间等多久（秒）")
    ap.add_argument("--tag", default="")
    ap.add_argument("--offset", type=int, default=0, help="从第几条提示词开始取（避免重复话题）")
    ap.add_argument("--measure", action="store_true", help="只测最近 30 分钟内发出的")
    args = ap.parse_args()

    base_path = os.path.join(ROOT, "_restore-20261111", "corpus-baseline.json")
    human = metrics(json.load(io.open(base_path, encoding="utf-8"))["human"])

    start = time.time()
    if args.run:
        for text in pick_prompts(args.run, args.offset):
            send(text)
            time.sleep(args.gap)
    elif not args.measure:
        start -= 1800

    texts = replies_since(start)
    result = report(texts, human)
    if args.tag and result:
        out = os.path.join(ROOT, "_restore-20261111", f"eval-{args.tag}.json")
        io.open(out, "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False, indent=1))
        print(f"\n结果已存 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
