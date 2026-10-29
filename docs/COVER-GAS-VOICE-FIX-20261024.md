# 气声歌翻唱三宗罪修复（《我不难过》孙燕姿自翻唱）— 2026-10-24

用户原始反馈（上一会话 goal-c6a2aeb6）：

1. 没有人唱的声音强弱有变化的感觉；
2. 「第一句歌词之前不加人声」的规则又失效，开头出现意义不明的哼唱；
3. 咬字非常差。人声比较实的歌表现较好，气声多的歌尤其差。

样本：`rvc-backend/temp/results/bc97f743c2baa84c.mp3`（2026-10-23 20:04，320.4s，孙燕姿模型唱孙燕姿《我不难过》= 自翻唱，原始人声可直接当理想参考）。
参数：`f0=crepe gate=lyric5@1 key_shift=0 (auto, f0=351.3Hz) noise=0.32 cluster=0.45 clean=True`，歌词约束 95 行覆盖 84.5%。

---

## 一、根因（都在 `rvc-backend/server.py`）

### ① 开头哼唱 = 门控的「兜底增益」

原 `apply_vocal_gate()` 末尾：

```python
gate = np.clip(np.maximum(env, GATE_FLOOR * 0.5), 0.0, 1.0)
```

`GATE_FLOOR=0.12`，所以不唱歌的帧（前奏 / 间奏 / 尾奏）也被抬到 **0.06 ≈ -24 dB**，永远不可能真静音。
SVC 会把前奏的钢琴旋律当成旋律唱出来（`melody_mask` 只在有 pitch 时才拦），于是这 6% 的「哼唱」全程可见。这首歌第一句在 **31.42s**，前奏整整 31 秒，问题最明显。函数里自己的注释还写着「长空档完全静音 —— 用户要求间奏尾奏一点人声都别有」，代码与注释直接矛盾。

### ② 没有强弱 = SVC 本身不建模响度

`so_vits_svc_fork/modules/synthesizers.py:202` 的 `infer(c, f0, uv, g, noice_scale, predict_f0)` 只有内容特征 + 音高 + 音素级 speaker，**没有任何音量/响度条件输入**。所以强弱只能靠后处理补，而原来的后处理只有：

- `energy_gate = clip((db - (ref_level - 40)) / 10, 0.12, 1.0)` —— 比峰值低 30dB 以内全是 1.0，几乎不塑形；
- `mix_and_export()` 只做峰值归一 `mix *= 0.89/peak`，没有 RMS 压缩。

结论：全曲被拉成一条平的电平线，用户听到的「没有强弱」既包括电平，也包括**音色不随力度变化**（该亮该炸的地方还是那口气声）。

### ③ 咬字差 = 高频被砍两道

实测（按参考响度对齐后的频段能量差）：

| 频段 | SVC 直出 | 再过 `clean_vocal_output` | 参考 |
|---|---|---|---|
| 2–4k | -4.7 dB | -5.0 dB | 0 |
| 6–9k | -5.2 dB | **-8.4 dB** | 0 |
| 12–16k | -5.0 dB | -5.4 dB | 0 |
| HNR（谐噪比，越高越「干净无气声」） | 21.15 | 21.50 | **17.15** |

- 直出就已经暗 4~6 dB（辅音/齿音能量不足 → 咬字糊）；
- `clean_vocal_output()` 的齿音压制 `thr = p85*1.4; gain = clip(thr/be, 0.45, 1.0)` 再砍 2~3 dB，`>16kHz *= 0.5` 雪上加霜；
- HNR 反而比原唱高 4 dB —— **气声/呼吸噪声被门限抹掉了**，所以「气声多的歌尤其难听」：这类歌的表达力几乎全在高频气声和辅音瞬态上。
- 已排除采样率因素：`models/孙燕姿/config.json` 是 `sampling_rate=44100 / mel_fmax=22050`，不是 22k 模型。

---

## 二、改动（`rvc-backend/server.py`，已 py_compile 通过）

新增四个可回退的开关（环境变量，默认全开）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `BACKEND_TIMBRE` | 1 | 频谱/咬字还原主开关 |
| `BACKEND_TIMBRE_STATIC` / `BACKEND_TIMBRE_DYN` | 1 / 1 | 静态长时谱 / 逐帧谱包络 |
| `BACKEND_TIMBRE_MAX_DB` | 12 | 静态谱增益上限 |
| `BACKEND_CONS` / `BACKEND_CONS_RATIO` | 1 / 1.0 | 辅音（4k–16k）从参考移植 |
| `BACKEND_DYN` / `BACKEND_DYN_MAX_DB` | 1 / 18 | 强弱（响度包络）移植 |
| `BACKEND_GATE_HARD` | 1 | 歌词外真静音 |
| `BACKEND_DEESS_THR` / `BACKEND_DEESS_FLOOR` | 1.8 / 0.65 | 去齿音阈值/下限（原 1.4 / 0.45） |
| `BACKEND_HF_CUT` | 0.85 | >16kHz 衰减（原 0.5） |

### 1. 真静音（`apply_vocal_gate`，server.py:686）

```python
if GATE_HARD:
    gate = np.where(inside, np.clip(env, 0.0, 1.0), 0.0)
```

`inside = allowed > 0.001`（歌词行内 + 短间隙 + 有旋律）。行首仍保留 0.2s 淡入、行尾 220ms 收尾，不会切掉辅音。

### 2. 强弱移植（`match_dynamics`，server.py:553）

逐帧（20ms）比较参考人声与成品人声的 dB 曲线：

```python
gain_db = (dbr - median(dbr[act])) - (dbc - median(dbc[act]))
gain_db = np.clip(gain_db, -18, 18)   # 3 帧平滑
```

参考是自翻唱的原始人声，所以这是「把原唱的力度曲线贴回去」，而不是套一条通用压缩曲线；峰值归一后仍有 +6dB 头room。位置：门控之后、去齿音之前。

### 3. 音色/咬字还原（`restore_timbre`，server.py:603）

三步都在 STFT 域，只搬运参考的**频谱包络与瞬态**，不注入音色：

1. **静态长时谱**：9 个频段（500/900/1500/2500/4000/6000/9000/12000/16000），只用「参考真有能量的帧」计算，clamp ±12dB，三点平滑成曲线；
2. **逐帧谱包络**：(2–6k) 与 (6–12k) 两带，逐帧 ±6dB，3 帧平滑（防泵动）；
3. **辅音移植**：把参考的 4k–16k 谐波/噪声结构按帧比例搬过来，6–16k 权重为 1、1–6k 斜坡过渡，只作用于参考有辅音瞬态的帧。

### 4. 去齿音放宽（`clean_vocal_output`，server.py:1102）

`thr = p85*1.8`，`clip(thr/be, 0.65, 1.0)`，>16k 阻尼 0.5 → 0.85。

### 5. 缓存键

结果缓存 payload 增加 `"fx": "tm111c1d1gh1"`（各开关状态的指纹）。**改了后处理却不换 key 会拿到旧 mp3**，这正是最初改完没反应的原因。

---

## 三、验证

### A. 干声级 A/B（`_tune_gascover.py`，只看歌词行内 & 原唱真有能量的帧）

| 指标 | 参考 | 修复前 | 修复后 | 期望 |
|---|---|---|---|---|
| 前奏电平 intro_db | — | **-42.4 dB** | **-140.0 dB** | 越低越好（-140 = 数值真静音） |
| 逐帧包络相关 env_corr | 1.0 | 0.884 | **0.987** | → 1 |
| 电平跨度 spread | 24.7 | 24.2 | 24.7 | = 参考 |
| 逐行电平误差 std | 0 | 2.40 dB | **0.09 dB** | → 0 |
| HNR（气声含量） | 17.15 | 21.50 | **18.70** | → 参考 |
| HF(4–10k) 相对能量 | -12.57 | -16.09 | **-12.07** | → 参考 |
| 谱质心 cent | 4180 | 3354 | **4208** | → 参考 |
| 谱通量 flux | 1.40 | 1.555 | 1.183 | 偏平一点点，可接受 |

### B. 成品级对比（`_diag_intro3.py` + `_diag_floor.py`）

成品人声电平靠 `mix - s*伴奏` 反推，必须先补 **MP3 编码器延迟 1104 样本（25ms）**，否则相关系数只有 -0.02 的假象。

| 指标（相对演唱段中位电平） | 旧成品 bc97f743 | 新成品 9a08ff07 | 纯伴奏对照地板 |
|---|---|---|---|
| 前奏 p90 | -23.4 dB | **-28.8 dB** | **-24.7 dB** |
| 尾奏 p90 | -33.7 dB | **-41.0 dB** | -38.1 dB |
| 包络相关 | 0.801 | **0.858** | — |
| 9–12k 相对能量 | -27.3 dB | **-21.3 dB** | — |
| 12–16k 相对能量 | -32.8 dB | **-28.9 dB** | — |

「纯伴奏对照」= 把人声置零、只留伴奏并按同样 320k 编码走一遍后测得的抵消误差地板。新成品的前奏/尾奏残差**低于该地板**，说明那里确实没有人声，剩下的只是钢琴的编码抵消误差。

---

## 四、当前状态与运维

- 修复已在 **3333 生产的点歌后端**生效（新 PID 7720，`GET http://127.0.0.1:3333/` → HTTP 200）。
- 重启方法（沙箱外）：停 3333 旧进程 → `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-cover-backend.ps1`。
  注意 `start-cover-backend.ps1` 用 `Get-NetTCPConnection` 判断端口占用，**在受限环境下会误判为空**，然后 gradio 报 `Cannot find empty port in range: 3333-3333`；判断一律用 `netstat -ano`。
- 回滚：`BACKEND_TIMBRE=0 BACKEND_DYN=0 BACKEND_GATE_HARD=0 BACKEND_DEESS_THR=1.4 BACKEND_DEESS_FLOOR=0.45 BACKEND_HF_CUT=0.5` 即可逐项退回到原行为；原始代码备份是 `rvc-backend/server.py.bak-tune`（2026-10-21，修复前）。
- 调参台：`_tune_gascover.py --tag base|fix --no-convert --force`（秒级，复用 `temp/debug/gascover/converted_raw.wav`）。
- 一次性探针：`_diag_gascover.py` / `_diag_bands.py` / `_diag_intro3.py` / `_diag_floor.py` / `_e2e_cover.py` / `_diag_e2e.py`，都可以删或留作回归。

## 五、还能继续做的（未做）

- `BACKEND_NOISE_SCALE 0.32 → 0.22`、`BACKEND_CLUSTER_RATIO 0.45 → 0.55` 的听感 A/B：气声歌过低 noise 会更「实」，但可能丢气声，需要人耳定。
- 动态移植目前是「参考 dB 曲线贴回」，若以后翻唱非自翻唱（音域/母带不同），需要先做整体响度对齐再算曲线差分（现按 dB 差值算，已自带对齐，但极端音域仍建议先 RMS 匹配）。
- 咬字在极快词句上仍靠 `flux` 偏低，可考虑对辅音帧做 2–4k 瞬态提升而不是单纯频段增益。
