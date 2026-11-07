# 翻唱后端教程（Demucs / RoFormer 分离 → so-vits-svc 换声 → 后处理 → 混音）

本文是一次「重写翻唱后端」的完整记录：接口契约、依赖、模型落位、四类坑、参数与验收指标。
所有命令都在 Windows + PowerShell 环境下可复现。

---

## 1. 整体形状

```
歌曲音频
  ├─ ① 人声分离（二选一）
  │    ├─ Demucs（htdemucs，本地自带，稳）
  │    └─ audio-separator + BS-RoFormer / mel-band-RoFormer（质量更好，需下权重）
  │         → vocals.wav / no_vocals.wav（伴奏）
  ├─ ② 换声 so-vits-svc（进程内 Svc.infer_silence，f0=harvest）
  ├─ ③ 后处理 postproc：去噪 → 音色补偿 → 门限 → 动态跟随 → 去齿音
  └─ ④ 混音（人声 + 伴奏，软限幅 -1 dBFS）→ wav / mp3(320k)
```

服务是 Gradio 应用，端口默认 `3333`，对外只暴露 6 个 API：

| api | 作用 | 返回 |
|---|---|---|
| `/convert` | 全流程 | `(输出文件绝对路径, "true"/"false")` |
| `/show_model` | 可用模型名 | `list[str]` |
| `/list_voice_profiles` | 语音档位 | `list[dict]` |
| `/cache_info` | 缓存占用 | `{files, bytes, output_dir, models}` |
| `/clear_cache` | 只清 cache | `str` |
| `/show_msst_models` | 分离模型清单 | `list[str]` |

> Gradio 6 的调用路径是 `/gradio_api/call/<api>`，`POST /<api>` 会 404。

### 1.1 `/convert` 的参数顺序（调用方按位置传，错一位就全乱）

```
song_name_src, key_shift, vocal_vol, inst_vol, model_dropdown,
reverb_intensity, delay_intensity,
uvr5_agg, uvr5_tta, uvr5_postprocess, uvr5_window_size, uvr5_high_end_process,
msst_batch_size, msst_num_overlap, msst_normalize,
vocal_postprocess, shift_accompaniment,
f0_method, index_rate, filter_radius
```

最小可用调用：

```python
from gradio_client import Client
c = Client("http://127.0.0.1:3333/")
out, ok = c.predict("D:/song.mp3", 0, 0, 0, "孙燕姿",
                    0, 0, 8, 1.0, True, 512, True,
                    4, 0.25, True, True, True,
                    "harvest", 0.5, 3, api_name="/convert")
```

---

## 2. 依赖：三件事

```bash
# 1) torch（12 GB 显卡够用 cu128；cpu 版会把 svc 从 30 s 拖到 3 分钟）
uv pip install --python rvc-backend/.venv/Scripts/python.exe \
  torch torchaudio --index-url https://download.pytorch.org/whl/cu128

# 2) svc 引擎
uv pip install --python rvc-backend/.venv/Scripts/python.exe so-vits-svc-fork

# 3) 可选：更好的分离器
uv pip install --python rvc-backend/.venv/Scripts/python.exe audio-separator
```

`audio-separator` 还依赖 `onnxruntime audioread ffmpeg-python pydub`，缺哪个装哪个；
它默认只有 CPU provider，GPU 分离要走 ONNX-GPU 版本，CPU 也能跑（RTF ≈ 0.3）。

**ffmpeg 必须能找到**：把 `tools/ffmpeg.exe` 所在目录加进 `PATH`，否则
`audio-separator` 直接抛 `FFmpeg is not installed`。

---

## 3. 模型落位（以及它们的两个怪癖）

```
rvc-backend/
├── models/孙燕姿/{G_27200.pth, config.json, kmeans_10000.pt}      唱歌换声
├── models_voice/纳西妲/{G_40000.pth, config.json, kmeans.pt}      说话换声
└── models_sep/
    ├── msst/model_bs_roformer_ep_317_sdr_12.9755.ckpt  + config_bs_roformer_ep_317_sdr_12.9755.yaml
    └── soulx/mel_band_roformer_karaoke_becruily.ckpt   + config_karaoke_becruily.yaml
```

### 3.1 怪癖一：它要一个远程清单文件

`audio-separator` 加载模型前会拉
`https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json`。
直连不通时：

```bash
curl -L -o models_sep/msst/download_checks.json \
  https://gh-proxy.com/https://raw.githubusercontent.com/TRvlvr/application_data/main/filelists/download_checks.json
# 每个模型目录都要有一份，文件名必须叫 download_checks.json
```

### 3.2 怪癖二：yaml 必须叫它期望的名字

| 模型 | 期望的文件名 |
|---|---|
| BS-RoFormer ep317 | `config_bs_roformer_ep_317_sdr_12.9755.yaml` |
| mel-band-RoFormer karaoke | `config_mel_band_roformer_karaoke_becruily.yaml` |

karaoke 那份必须用官方 release 里的版本（sha256 `cd37b0dcc285fc22d88090415722ac7127ee1d9ea2f3346c3b8d8fcc61e0c74b`），
版本不对会 `SHA-256 mismatch` 然后强制重下再失败。

加载成功标志：`LOAD OK MDXCSeparator`。

---

## 4. 五个坑（按踩到的先后）

### 4.1 `torchaudio.save` 要 torchcodec，而 Windows 没有轮子

现象：日志里 `demucs 不可用，回退单轨模式: TorchCodec is required for save_with_torchcodec`，
时间线悄悄变成「只有一条音轨」，伴奏永远分不出来。

```text
uv pip install torchcodec --index-url https://download.pytorch.org/whl/cu128
→ no wheels with a matching platform tag (win_amd64)
```

改法：绕过 `torchaudio.save`，直接写文件。

```python
data = tensor.detach().cpu().numpy().astype(np.float32)
sf.write(str(path), data.T, sr)     # soundfile 写 [frames, channels]
```

### 4.2 libsndfile 打不开中文路径

现象：`Error opening '...\Desktop\翻唱A-B试听\cover-xxx.wav': System error.`

改法：先写到纯 ASCII 的 cache 目录，再 `shutil.move` 到目标位置：

```python
try:
    sf.write(str(final), y, sr)
except Exception:
    tmp = cache_dir / f"mix-{tag}.wav"
    sf.write(str(tmp), y, sr)
    shutil.move(str(tmp), str(final))
```

### 4.3 `-k` 参数会让 torch 直接拒绝加载

现象：`WeightsUnpickler error: Unsupported global: GLOBAL numpy.core.multiarray._reconstruct`

原因：`svc.exe infer -k kmeans.pt` 要反序列化 numpy pickle，torch ≥2.6 的 `weights_only=True` 不允许。
改法：**不给 `-k`**（cluster 只在音色微调时才需要），或者改成进程内调用并显式放开。

### 4.4 `svc.exe` CLI 把整首歌的音高推高了约 2.9 个半音

现象：换声后整体偏高、副歌发紧；同一份参数换成**进程内推理**就正常。客观测量（40–85 s 窗口，与原唱逐帧比）：

| 路径 | 音分中位数 | >100 音分占比 |
|---|---|---|
| 旧 CLI（harvest） | 12.9 | 28.1% |
| CLI（parselmouth） | 299.2 | — |
| **进程内 `Svc.infer_silence`** | **5.2** | **4.7%** |

改法：常驻模型 + 进程内推理：

```python
from so_vits_svc_fork.inference.core import Svc
svc = Svc(net_g_path=pth, config_path=cfg, device="cuda")
y = svc.infer_silence(x, speaker=0, transpose=0,
                      auto_predict_f0=False, noise_scale=0.30,
                      f0_method="harvest", db_thresh=-20,
                      pad_seconds=0.5, chunk_seconds=0.5, max_chunk_seconds=40)
```

顺带收益：单曲换声 124.6 s → **34.5 s**（模型只加载一次）。

### 4.5 `sep.separate()` 返回的是相对文件名

现象：第二次跑 `ffmpeg` 报 `exit 4294967294`；日志里路径少了一截。

改法：

```python
def _abs(p):
    return p if os.path.isabs(p) else os.path.join(outdir, os.path.basename(p))
```

---

## 5. 后处理：为什么需要它，以及四个旋钮

svc 的输出是「音色对了，但毛边、齿音重、清唱段漏气」。`postproc.py` 按顺序做四件事：

| 步骤 | 函数 | 作用 |
|---|---|---|
| 音色补偿 | `restore_timbre` | 用原唱做 LTAS 差，只补辅音帧（±6 dB 限幅） |
| 门限 | `apply_vocal_gate` | 只在**真长静音**处压 0，唱段保留气声 |
| 动态跟随 | `match_dynamics` | 逐帧向原唱看齐，平滑 15 帧（否则 20–80 Hz 被调制） |
| 去齿音 | `clean_vocal_output` | 4.5–9 kHz 收，14 kHz 以上压 |

关键默认值（都可用 `BACKEND_*` 环境变量覆盖，括号内是「改之前的坑」）：

```
DENOISE_STRENGTH 0.0   (1.6：谱减是颗粒感主因)
TIMBRE_DYN       0     (1：逐帧音色补偿会磨掉气声)
TIMBRE_MAX_DB    3     (6)
DEESS_THR        2.6   (1.8)
DEESS_FLOOR      0.85  (0.65)
HF_CUT           1.0   (0.85：14k 以上被砍，听感"不通透")
DYN_MAX_DB       6     (10)
DYN_SMOOTH      15     (3)
```

门限的写法值得单独说：

```python
sung   = 0.30 + 0.70 * melody_mask(reference, n)      # 唱段基座
gate   = np.maximum(energy_gate, 0.5) * sung           # 不再硬切
gate[gap_mask(reference, n, max_gap_s=0.9)] = 0.0      # 只对 >0.9 s 的真静音归零
```

早期版本用「行外一律 0」，结果是**人声被切掉一部分 → 跑调、中气不足**；改后近乎静音帧只剩 0.03%。

---

## 6. 音高：最后一步用 WORLD 对齐

即使换了进程内推理，音分中位数仍有 5.2。做一次「只换 f0、不动音色」的重建能把误差压到 2.4：

```python
f0, t = pyworld.harvest(conv, sr, f0_floor=60, f0_ceiling=1100)
sp = pyworld.cheaptrick(conv, f0, t, sr)     # 频谱包络 = 音色
ap = pyworld.d4c(conv, f0, t, sr)            # 非周期成分 = 气声
target = smooth(clean_ref_f0)                # 目标 f0 来自原唱
y = pyworld.synthesize(target, sp, ap, sr)
```

- `clean_ref_f0` 要做三件事：中值滤波、逐帧八度折返、丢弃 <50 ms 的碎片；
- 只用 `sp/ap` 保留音色，避免「音准对了但人换了」；
- 最后统一 `tanh` 软膝限幅到 −1 dBFS，**别用「峰值超 0.99 就整体相除」**——那会把清唱段削平，
  也就是听感上的「破音」。

实测：音分中位数 5.17 → **2.40**；峰值 −1.00 dBFS；削波样本 0%。

---

## 7. 两种分离器的取舍（实测同一首歌）

| 指标 | BS-RoFormer ep317 | mel-band-RoFormer karaoke |
|---|---|---|
| 人声带 SIR | 5.88 dB | **6.80 dB** |
| 人声带伴奏泄漏（200–4000 Hz） | 79.46% | 82.70% |
| 人声宽度（侧/中） | 0.0338 | **0.0087**（更贴中置） |
| 伴奏宽度 | 0.2242 | **0.2571**（更宽） |
| 重建自洽（sum vs mix 相关） | **0.9975** | 0.9892 |
| 分离耗时（整曲） | 87.5 s | 66.8 s |
| 换声耗时（同参数） | 60.0 s | **44.2 s** |

结论：**翻唱默认用 karaoke 版**（人声干净、伴奏宽、下游更快）；需要「最小改动」时用 ep317。

---

## 8. 缓存与产物

- `cache/`：中间分轨、换声原始输出（可清）
- `outputs/`（或 `COVER_OUTPUT_DIR`）：成品 wav / mp3（不清）
- `/clear_cache` 只删 `cache/`；早期版本会连成品一起删，已改。

`COVER_OUTPUT_DIR` 支持中文路径，但**写中文目录的进程必须有对应权限**：
桌面目录在受限沙箱里会 `PermissionError`，表现为 libsndfile 的 `System error.` +
兜底 `shutil.move` 再报 `Errno 13`。要么给它更高权限运行，要么把输出目录指到工作区。

---

## 9. 自检清单

1. `GET /gradio_api/info` → 6 个 api 全在；
2. `/show_model` 返回模型名（不是空数组）；
3. 跑一次 `/convert`，看日志四段耗时：`demucs → svc → postproc → mix`；
4. 产物 `ffprobe` 检查：44.1 kHz、双声道、时长≈原曲；
5. 质量三指标：`sir_vocal_band_db`、`vocal_width`、`sum_vs_mix_corr`；
6. 音准：40–85 s 窗口对原唱 f0，音分中位数 < 5 视为通过。

---

## 10. 回滚

- 分离器切回 Demucs：`svcvc_target_separation="demucs"`（或直接删 `models_sep/`）；
- 关掉音高对齐：`BACKEND_PITCH_ALIGN=0`；
- 关掉后处理：`BACKEND_PRESET=off`（`postproc.run_chain` 直接透传）。
