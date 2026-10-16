# -*- coding: utf-8 -*-
"""演示：按语义从 atri-meme-pack 里挑表情包（复刻插件的检索路径）。

流程 = 硅基流动 BAAI/bge-m3 把查询向量化 → 与插件落盘的 faiss 索引做内积检索
       → 用 semantic_metadata.json 把命中回填成图片路径与语义描述。
"""
import json, os, sys, urllib.request

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "astrbot", "data", "plugin_data", "meme_manager", "packs", "atri-meme-pack")
IDX = os.path.join(ROOT, "astrbot", "data", "plugin_data", "meme_manager", "semantic_indexes", "atri-meme-pack")
CFG = os.path.join(ROOT, "astrbot", "data", "cmd_config.json")

# ---- 取硅基流动 key（与插件里配的 embedding 提供方一致）
cfg = json.load(open(CFG, encoding="utf-8-sig"))
src = next(s for s in cfg["provider_sources"] if s.get("id") == "siliconflow_embedding")
key = src["embedding_api_key"]
if isinstance(key, list):
    key = key[0].lstrip("$")
    key = os.environ.get(key, "")
base = src.get("embedding_api_base", "https://api.siliconflow.cn/v1").rstrip("/")
model = src.get("embedding_model", "BAAI/bge-m3")

meta = json.load(open(os.path.join(PACK, "semantic_metadata.json"), encoding="utf-8-sig"))
images = meta["images"]
by_sha = {v.get("content_sha256"): v for v in images.values()}
by_entry = {k: v for k, v in images.items()}
man = json.load(open(os.path.join(IDX, "index_manifest.json"), encoding="utf-8-sig"))
id2entry = {}
for h, v in man["items"].items():
    e = by_sha.get(h) or by_entry.get(h)
    if e:
        id2entry[v["faiss_id"]] = e
print(f"索引 {man['index_file']} | 模型 {man['embedding_model']} | {man['item_count']} 张 | 已映射 {len(id2entry)}")

import faiss
import numpy as np

index = faiss.read_index(os.path.join(IDX, man["index_file"]))
print("faiss ntotal =", index.ntotal, "| 维度", index.d)


def embed(text):
    body = json.dumps({"model": model, "input": [text], "encoding_format": "float"}).encode()
    r = urllib.request.Request(base + "/embeddings", data=body, method="POST",
                               headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(r, timeout=60))
    v = np.array([d["data"][0]["embedding"]], dtype="float32")
    faiss.normalize_L2(v)
    return v


def text_of(e):
    for k in ("caption", "description", "semantic_text", "summary", "text", "visual_description", "tags"):
        v = e.get(k)
        if isinstance(v, str) and v:
            return k, v
        if isinstance(v, list) and v:
            return k, "、".join(map(str, v))
    return "(无)", ""


for q in ("安慰对方，哄一哄", "很生气，想骂人", "困了想睡觉"):
    D, I = index.search(embed(q), 5)
    print(f"\n🔎 查询：{q}")
    for score, fid in zip(D[0], I[0]):
        e = id2entry.get(int(fid))
        if not e:
            continue
        field, desc = text_of(e)
        print(f"  {score:.3f}  [{e['category']}] {e['relative_path']}")
        print(f"          {field}: {desc[:110]}")
