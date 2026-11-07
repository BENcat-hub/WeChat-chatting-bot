# Bert-VITS2 接入教程（v1.1.1，中文单说话人）

本文记录「把一个 2023 年的 Bert-VITS2 权重重新跑起来，并接进本项目的 TTS 服务」的全过程。
只讲现象、原因与代码改动，命令可直接复制。

---

## 1. 为什么会有这一步

项目早期的语音走的是 **Bert-VITS2**（原神 16 角色，`G_114000.pth`，44.1 kHz）。
一次事故把 `bertvits2/` 整个目录（引擎 + venv + 推理脚本）删掉，只剩权重也一起没了；
于是语音改成了「edge-tts 合成 → so-vits-svc 换音色」的两段式方案：

```
文本 --edge-tts--> 44.1k wav --svc(纳西妲)--> 语音气泡
```

两段式能出声，但有三个固有代价：

1. 多一次重采样与换声，齿音和气息被磨平，语气偏平；
2. 常驻 svc 也要吃 1.2 GB 显存，冷启动 6~19 s；
3. 一旦上游 TTS 把内部标记（`<pc_tts>`）念出来，会直接进音频。

所以权重一旦找回，就应该把单段式接回来。**结论：原版权重可以在 HF 镜像上找到，文件名为 `G_114000.pth`（701,632,539 B），配置里 `n_speakers=256`、`sampling_rate=44100`、`spk2id` 里带「纳西妲」等角色名。**

---

## 2. 文件从哪来

| 东西 | 来源 | 说明 |
|---|---|---|
| 权重 `G_114000.pth` | HF 仓库 `guetLzy/Bert-vits2`（经 `hf-mirror.com`） | 669 MB，断点续传用 `curl -C -` |
| 权重同目录 `config.json` | 同上 | 必须与权重配套，不能自己写 |
| 引擎源码 | GitHub `fishaudio/Bert-VITS2` tag **`1.1.1`**（commit `f046571a`） | 只取推理需要的 17 个文件 |

### 2.1 源码为什么不能整包下载

`gh-proxy.com` 能代理 `raw.githubusercontent.com`，但**代理不了 `codeload.github.com` / `github.com/.../archive/*.zip`**（返回 404）。
可行的两条路：

```bash
# 路 A：GitHub API 拿树，再逐文件取（本机实测最稳）
curl -s https://api.github.com/repos/fishaudio/Bert-VITS2/tags | head
# → 1.1.1 = f046571a

BASE='https://gh-proxy.com/https://raw.githubusercontent.com/fishaudio/Bert-VITS2/f046571a'
for f in models.py attentions.py commons.py mel_processing.py transforms.py utils.py \
         modules.py data_utils.py monotonic_align/__init__.py monotonic_align/core.py \
         text/__init__.py text/symbols.py text/chinese.py text/chinese_bert.py \
         text/cleaner.py text/japanese.py text/japanese_bert.py text/tone_sandhi.py \
         text/english.py text/english_bert_mock.py text/opencpop-strict.txt ; do
  mkdir -p "bertvits2/$(dirname $f)"
  curl -L --retry 3 -o "bertvits2/$f" "$BASE/$f"
done
```

### 2.2 目录长这样

```
bertvits2/
├── models.py attentions.py commons.py modules.py monotonic_align/
├── mel_processing.py transforms.py utils.py data_utils.py
├── text/{__init__,symbols,chinese,chinese_bert,cleaner,japanese,tone_sandhi,...}.py
├── bert/chinese-roberta-wwm-ext-large/   ← 分词器文件 + pytorch_model.bin（1.3 GB）
├── pretrained/纳西妲/{G_114000.pth, config.json}
└── bv2_say.py                            ← 本项目加的推理封装
```

`bert/chinese-roberta-wwm-ext-large/` 里 `config.json`、`tokenizer.json`、`vocab.txt` 等小文件从 v1.1.1 仓库取；
`pytorch_model.bin` 需要另外下（v1.1.1 仓库里没有权重）：

```bash
curl -L -C - -o bertvits2/bert/chinese-roberta-wwm-ext-large/pytorch_model.bin \
  https://hf-mirror.com/hfl/chinese-roberta-wwm-ext-large/resolve/main/pytorch_model.bin
```

---

## 3. 依赖：三个能装、一个装不上

装到**已有的 so-vits-svc venv**（`rvc-backend/.venv`）里，省得再拉一份 torch：

```bash
uv pip install --python rvc-backend/.venv/Scripts/python.exe cn2an pypinyin jieba
```

| 包 | 结果 | 备注 |
|---|---|---|
| `cn2an` `pypinyin` `jieba` | ✅ | 中文 g2p 必需 |
| `pyopenjtalk` | ❌ 失败 | 无 Windows wheel，`pip` 会去 `cmake ..` 编译，直接 `CalledProcessError` |

**绕法：不装日文模块，改成按需导入。** v1.1.1 的 `text/cleaner.py` 在模块顶层 `from text import chinese, japanese`，
只要 import 就会连带 `pyopenjtalk`；把它改成惰性加载即可：

```python
# bertvits2/text/cleaner.py
from text import chinese, cleaned_text_to_sequence

_JP = None

def _jp():
    global _JP
    if _JP is None:
        from text import japanese as _m
        _JP = _m
    return _JP

language_module_map = {"ZH": chinese}

def _module(language):
    if language == "JP":
        return _jp()
    return language_module_map[language]
```

`text/__init__.py::get_bert` 也是顶层 import 了 `japanese_bert`，同样思路处理（只走 ZH 时不会触发）。

---

## 4. 推理链路（v1.1.1 的实际形状）

```
文本
 └─ text.cleaner.clean_text(text, "ZH")        → (norm_text, phones, tones, word2ph)
     ├─ chinese.text_normalize                （数字、标点归一）
     └─ chinese.g2p                           （jieba 分词 → 拼音 → 声调 → 音素）
 └─ text.cleaned_text_to_sequence             → (phone_ids, tone_ids, lang_ids)
 └─ text.get_bert(norm_text, word2ph, "ZH", device)   → (1024, N) 语义特征
 └─ models.SynthesizerTrn.infer(x, x_len, sid, tone, lang, bert, ja_bert, sdp_ratio=…)
 └─ soundfile.write(44.1 kHz)
```

三个容易踩的点：

1. **`add_blank=true` 时必须做交织**，否则长度对不上 BERT：

   ```python
   phone = commons.intersperse(phone, 0)
   tone = commons.intersperse(tone, 0)
   lang_ids = commons.intersperse(lang_ids, 0)
   for i in range(len(word2ph)):
       word2ph[i] *= 2
   word2ph[0] += 1
   ```

2. **BERT 的列数必须等于音素数**，否则 `assert bert.shape[-1] == len(phone)` 直接炸；
   极端标点会让它错位，兜底成 `torch.zeros(1024, len(phone))`（能出声，韵律略平）。

3. **`JA` 分支才用 `ja_bert`**，中文时是 `torch.zeros(768, N)`；反过来 `bert` 才是 1024 维。

### 4.1 `bv2_say.py`（本项目封装）

```python
gl_say(text, out_wav, sid="纳西妲", sdp=0.2, noise=0.6, noise_w=0.8, length=1.0)
```

- 进程内缓存 `(net, hps)`，第一次调用加载权重，之后单句只跑推理；
- 构造：`SynthesizerTrn(len(symbols), hps.data.filter_length // 2 + 1,
  hps.train.segment_size // hps.data.hop_length, n_speakers=hps.data.n_speakers, **hps.model)`；
- 载权重：`utils.load_checkpoint(pth, net, None, skip_optimizer=True)`；
- `torch.load` 在新版本默认 `weights_only=True`，老 checkpoint 里带 numpy 全局对象，必须先
  `torch.serialization.add_safe_globals(...)` 或让 `load_checkpoint` 走非受限路径；
- **`chinese_bert.py` 用的是相对路径 `./bert/chinese-roberta-wwm-ext-large`**，
  所以 `bv2_say.py` 在 import 文本模块前要 `os.chdir(ROOT)`，否则 `OSError: Incorrect path_or_model_id`。

---

## 5. 常用报错对照表

| 现象 | 原因 | 处理 |
|---|---|---|
| `CalledProcessError: Command '['cmake', '..']'` | `pyopenjtalk` 要编译 | 不装，改惰性 import |
| `OSError: Incorrect path_or_model_id: './bert/...'` | CWD 不在 `bertvits2/` | `os.chdir(ROOT)` |
| `OSError: We couldn't connect to 'https://huggingface.co'` | 直连被拦 | `export HF_ENDPOINT=https://hf-mirror.com` |
| `WeightsUnpickler error: Unsupported global: GLOBAL numpy.core.multiarray._reconstruct` | torch ≥2.6 默认 `weights_only=True` | `add_safe_globals` / 关闭该限制 |
| `AssertionError: Bert seq len != len(phone)` | 未做 `add_blank` 交织，或标点错位 | 见 §4 第 1、2 点 |
| `KeyError: 纳西妲` | `spk2id` 里没有该名字 | 读 `config.json` 的 `spk2id` 逐个对 |
| `RuntimeError: CUDA out of memory` | 与 svc / demucs 抢显存 | 先释放另一侧模型，或 `BV2_DEVICE=cpu` |

---

## 6. 接进 TTS 服务（单端口、双引擎）

`scripts/nahida_tts_server.py` 的 `synthesize()` 已经是按 `NAHIDA_ENGINE` 分支的结构，加一档即可：

```python
if engine == "bv2":                       # 单段式：直接出 44.1k
    import bv2_say                        # sys.path 里要有 bertvits2/
    info = bv2_say.gl_say(spoken, tmp_wav, sid=SPEAKER,
                          sdp=0.2, noise=0.6, noise_w=0.8, length=1.0)
    src = tmp_wav                          # 不再过 svc
else:                                      # edge / mimo → 仍需 svc 换音色
    ...
```

对外 HTTP 契约不变：

```
POST /v1/audio/speech   {"input": "...", "voice": "...", "skip_svc": true|false}
GET  /health            {"ok": true, "speaker": "纳西妲", "engine": "bv2"}
```

**验收看三样**：`/health` 里的 `engine`、单句 `total` 耗时、以及 44.1k 单声道 wav 的实际时长。

---

## 7. 实测数据（RTX 5070 12 GB，fp32）

| 项目 | 数值 |
|---|---|
| checkpoint 加载 | 1.3 ~ 1.45 s |
| 进程内首次调用（含 BERT 1.3 GB 权重落盘读取 + jieba 建词表） | 18.6 ~ 20.8 s |
| 首次推理（cudnn autotune 冷态） | 3.0 ~ 3.2 s |
| **常驻后单句（17~19 字）** | **text 0.02 s + infer 0.08~0.13 s = 0.10~0.15 s** |
| HTTP `/v1/audio/speech` 端到端 | 首次 176~241 ms，常驻 156 ms |
| 输出 | 44.1 kHz 单声道，22~19 字 ≈ 2.9~3.6 s，RMS 0.35~0.45，峰值 0.99 |
| 可懂度回环（把合成音频再喂 ASR） | 与输入文本逐字一致（个别标点/虚词被听成近音字） |

结论：**「2~3 秒出音」的目标实际落在 0.2 秒级**，前提是进程常驻（`preload()` 在启动时预热一次）。
冷启动那一笔 20 秒只在服务重启后第一条消息出现，与旧方案的 svc 冷启动同性质，但只付一次。

对照旧的两段式（edge + svc 常驻）：单句约 1.5~2.5 s，且多一次重采样与换声。


---

## 8. 回滚

- 只删 `bertvits2/` 不影响其它服务；TTS 服务把 `NAHIDA_ENGINE` 改回 `edge` 即恢复两段式。
- 权重目录已在 `.gitignore`（`bertvits2/pretrained/`），不会进仓库。
