# -*- coding: utf-8 -*-
"""人类聊天 vs 机器人消息的风格对照（客观指标，不靠感觉）。

数据源：WeFlow 本地库 `GET http://127.0.0.1:5031/api/v1/messages?talker=…&limit=…&offset=…`
  isSend=0 -> 真人发来的；isSend=1 -> 我们（bot）发出去的。

指标分五组，都是「人味儿」的客观代理：
  长度    平均字数、中位数、1~6 字占比、>30 字占比、单条句数均值
  标点    句末句号率、逗号密度、省略号/感叹号/问号率、无标点占比
  口语    语气词覆盖（啊呀嘿哎嗯唔哦诶呗嘛啦）、叠词、单字应答占比
  书面    书面连接词/套话命中率（首先|其次|然而|因此|总之|综上|值得注意|希望对你有帮助|建议你…）
  结构    反问收尾率、每条多句占比、emoji 使用率、括号/引号使用率

用法：
  python scripts/humanize_metrics.py                     # 默认取样若干会话
  python scripts/humanize_metrics.py --persist talks.json # 存原始样本供后续 A/B
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request

BASE = os.environ.get("WEFLOW_BASE", "http://127.0.0.1:5031")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "secrets", "weflow-token.txt")


def weflow_token() -> str:
    tok = os.environ.get("WEFLOW_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with io.open(TOKEN_FILE, encoding="utf-8-sig") as fh:
            return fh.read().strip()
    except OSError:
        return ""


DEFAULT_TALKERS = [
    ("桃桃真可爱", "wxid_jzjp7bpw8vd422"),
    ("不曾为世界整除", "wxid_qvj04bh9wqhl22"),
    ("陈奥琦", "wxid_ml4l1e98nlyn22"),
    ("黎栀", "wxid_n4ljpzuu3zxh22"),
    ("天文学家", "wxid_iwfcw218p6pq22"),
]

FILLERS = "啊呀嘿哎嗯唔哦诶呗嘛啦哈咦呦哇"
WRITTEN = [
    "首先", "其次", "再者", "然而", "因此", "总之", "综上", "总的来说", "换句话说",
    "值得注意的是", "需要指出的是", "希望对你有帮助", "建议你", "不妨", "值得一提的是",
    "由此可见", "一方面", "另一方面", "与此同时", "从而", "也就是说",
]
SENT_END = "。！？!?…~～"
PUNCT_ALL = "，。！？、；：“”‘’（）…—～"


def fetch(talker: str, limit: int = 300, offset: int = 0) -> list[dict]:
    q = urllib.parse.urlencode({"talker": talker, "limit": limit, "offset": offset,
                                "access_token": weflow_token()})
    url = f"{BASE}/api/v1/messages?{q}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        for key in ("data", "messages", "items", "list"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def clean(text: str) -> str:
    t = str(text or "")
    t = re.sub(r"<[^>]{1,40}>", "", t)
    t = re.sub(r"\[(?:图片|表情|语音|视频|文件|链接|动画表情|聊天记录)\]", "", t)
    return t.strip()


def is_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[\[【(（]?[^\u4e00-\u9fff\w]{0,8}[\]】)）]?", t):
        return True
    sysmsg = ("sysmsg", "撤回", "xml version", "该消息类型暂不支持")
    return any(s in t for s in sysmsg)


def sentences(text: str) -> list[str]:
    parts = [p for p in re.split(r"[。！？!?…\n]+", text) if p.strip()]
    return parts or [text]


def metrics(texts: list[str]) -> dict:
    texts = [t for t in texts if t]
    if not texts:
        return {}
    lens = [len(t) for t in texts]
    n = len(texts)
    sent_counts = [len(sentences(t)) for t in texts]
    has_end_punct = sum(1 for t in texts if t and t[-1] in SENT_END)
    no_punct = sum(1 for t in texts if not any(c in PUNCT_ALL for c in t))
    comma_density = sum(t.count("，") + t.count(",") for t in texts) / n
    ellipsis = sum(1 for t in texts if "…" in t or "..." in t or "。。" in t or ".." in t) / n
    exclaim = sum(1 for t in texts if "！" in t or "!" in t) / n
    question = sum(1 for t in texts if t.rstrip()[-1:] in "？?") / n
    filler = sum(1 for t in texts if any(f in t for f in FILLERS)) / n
    redup = sum(1 for t in texts if re.search(r"([\u4e00-\u9fff])\1", t)) / n
    short_ack = sum(1 for t in texts if len(t) <= 3) / n
    written = sum(1 for t in texts if any(w in t for w in WRITTEN)) / n
    emoji = sum(1 for t in texts if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", t)) / n
    brackets = sum(1 for t in texts if re.search(r"[（(][^）)]{1,20}[）)]", t)) / n
    multi_sent = sum(1 for c in sent_counts if c >= 3) / n
    return {
        "样本": n,
        "平均字数": round(statistics.mean(lens), 1),
        "中位字数": round(statistics.median(lens), 1),
        "≤6字占比": round(sum(1 for x in lens if x <= 6) / n, 3),
        ">30字占比": round(sum(1 for x in lens if x > 30) / n, 3),
        "句末标点率": round(has_end_punct / n, 3),
        "无标点占比": round(no_punct / n, 3),
        "逗号密度": round(comma_density, 2),
        "省略号率": round(ellipsis, 3),
        "感叹号率": round(exclaim, 3),
        "问号收尾率": round(question, 3),
        "语气词率": round(filler, 3),
        "叠词率": round(redup, 3),
        "≤3字应答": round(short_ack, 3),
        "书面套话率": round(written, 3),
        "emoji率": round(emoji, 3),
        "括号补充率": round(brackets, 3),
        "3句以上占比": round(multi_sent, 3),
    }


def collect(talkers, per_talker: int) -> tuple[list[str], list[str]]:
    human: list[str] = []
    bot: list[str] = []
    for _name, wxid in talkers:
        try:
            rows = fetch(wxid, limit=per_talker)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {_name}: 拉取失败 {exc}")
            continue
        got_h = got_b = 0
        for row in rows:
            text = clean(row.get("content"))
            if is_noise(text):
                continue
            if row.get("isSend"):
                bot.append(text)
                got_b += 1
            else:
                human.append(text)
                got_h += 1
        print(f"  {_name:<8} 真人 {got_h:>3} 条 / 机器人 {got_b:>3} 条")
    return human, bot


def show(title: str, stats: dict) -> None:
    print(f"\n== {title}")
    for key, val in stats.items():
        print(f"   {key:<10} {val}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-talker", type=int, default=300)
    ap.add_argument("--persist", default="")
    args = ap.parse_args()
    print(f"== 取样本（{BASE}，每人 {args.per_talker} 条）")
    human, bot = collect(DEFAULT_TALKERS, args.per_talker)
    if not human and not bot:
        print("没有取到样本，WeFlow 是否在 5031 上跑着？")
        return 2
    mh, mb = metrics(human), metrics(bot)
    show("真人（isSend=0）", mh)
    show("机器人（isSend=1）", mb)
    print("\n== 差距（机器人 - 真人，绝对值越大越不像人）")
    for key in mh:
        if key == "样本":
            continue
        try:
            print(f"   {key:<10} {float(mb.get(key, 0)) - float(mh.get(key, 0)):+.3f}")
        except (TypeError, ValueError):
            continue
    if args.persist:
        io.open(args.persist, "w", encoding="utf-8").write(json.dumps(
            {"human": human, "bot": bot}, ensure_ascii=False, indent=1))
        print(f"\n样本已存 {args.persist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
