# 合唱源翻唱：人声二次分离（2026-10-27）

## 问题

用户在《落叶归根》（王力宏、单依纯合唱）上连续反馈三件事：

1. **跑调** —— 分离出的 `vocals.wav` 是两个人的叠加，`auto_key_shift()` 用逐帧 f0 中位数判男女调，
   男声帧把 `low160` 顶到 0.283 > 0.28，误判成男调 → 硬加 +8 半音，女声段被推到 ~490Hz。
2. **音色怪** —— `restore_timbre()` 把参考（两个人的混合音色）的频谱逐带移植到单一音色上。
3. **破音** —— `match_dynamics()` 上行增益上限原为 +18dB，把 SVC 的噪声底一起放大；实测增益跨度 27~28dB。

## 实测数据（e552bcb4…，即该曲分离结果）

| 指标 | 数值 |
|---|---|
| f0 中位数 | 252.8 Hz |
| 男声帧 (<180Hz) | 29.2 % |
| 女声帧 (180–400Hz) | 56.1 % |
| >400Hz | 14.7 % |

## 方案

### 1. 双声源检测（`_pitch_profile()`）

```python
duet = male > 0.15 and (female + high) > 0.25
```

命中时 `auto_key_shift()` 直接 `return 0, med`（不再 +8），并打日志。
同时把男调判定从 `med < 200 or low160 > 0.28` 收紧为 `med < 190 and low160 > 0.45`。

### 2. 人声拆轨（`split_duet_vocals()`，server.py:1155）

逐帧 parselmouth f0 → 男性/女性帧判定 → 5 帧滑窗软化 → **谐波梳状掩码**
（`exp(-(dist/0.10)^2)`，只保留与该声部基频成整数倍的频点）→ 两轨功率归一到与原始等能量 → ISTFT。

实测分离纯度（同一首歌）：

| 轨 | f0 中位数 | 男声帧占比 | 女声帧占比 | 耗时 |
|---|---|---|---|---|
| 男声轨 | 146.3 Hz | 78.4 % | 19.3 % | — |
| 女声轨 | 297.2 Hz | 1.8 % | 85.6 % | 7.2 s |

### 3. 每轨单独转换（`convert_duet_tracks()`，server.py:1274）

每条轨各自 `auto_key_shift()`（男声轨 +8、女声轨 +0）→ 各自 SVC → 各自 `restore_timbre()`
（参考就是本轨，音色不再串味）→ 按 RMS 对齐后合并成一条演唱轨。

- `BACKEND_DUET_MODE=both`（默认）：两轨都唱，歌是完整的
- `BACKEND_DUET_MODE=single`：只唱能量占优那一轨，另一半静音（卡拉 OK 式对唱）
- `BACKEND_DUET_SPLIT=0` / `BACKEND_DUET_SAFE=0`：降级回保守模式（只关后处理、不变调）

### 4. 后处理限幅（针对「音色怪 / 破音」）

| 参数 | 改前 | 改后 |
|---|---|---|
| `TIMBRE_MAX_DB` | ±12 dB | ±6 dB |
| `TIMBRE_HF_CAP_DB`（新增，≥6k 单独上限） | — | +3 dB |
| 6–12k 逐帧包络 | ±6 dB | ±3 dB |
| `CONS_RATIO` | 1.0 | 0.6 |
| `DYN_MAX_DB` | ±18 dB | ±8 dB（上行最多 +6） |
| 帧增益绝对上限 | 12× | 4× |

另外 `restore_timbre()` 的两处增益现在**先减去中位数再限幅**：否则整轨电平差（例如拆分后退过降噪）
会让每条带都被算成同一个 -6dB 并截断，EQ 被压平等于没对齐。

## 验证

60 秒片段（120–180s）端到端：

```
[backend] 合唱分离: 男声帧 13% / 女声帧 40%，选中女声轨 duet_female.wav
[backend] 合唱分轨: male 轨 key_shift=8 中位 f0=144.4Hz rms=0.1178
[backend] 合唱分轨: female 轨 key_shift=0 中位 f0=242.3Hz rms=0.1228
[backend] 合唱合轨完成 converted_duet.wav（2 轨）
[backend] 强弱移植完成 增益跨度 14.0dB
[backend] 频谱对齐: 0.7k+2.0 … 14.0k+2.0
final dur 60.0 peak 0.888 TOTAL 40.1s
```

全曲（320s）预计 2.5~3 分钟（两遍 SVC + 分离）。

## 回滚

- 只想去掉拆分：`BACKEND_DUET_SPLIT=0`
- 想回到 10-24 的激进后处理：`BACKEND_TIMBRE_MAX_DB=12 BACKEND_DYN_MAX_DB=18 BACKEND_CONS_RATIO=1.0`
- 想彻底关掉音色还原 / 强弱移植：`BACKEND_TIMBRE=0` / `BACKEND_DYN=0`

## 6. 自动避开合唱版本（2026-10-27 用户要求）

用户反馈「还是有问题，这样吧，点歌的时候自动选择非合唱版本」，因此在两处加了过滤：

### 6.1 后端 rvc-backend/server.py
- 新增 `_DUET_WORDS`（合唱/对唱/重唱/duet/feat/live/伴奏/翻唱/片段…）、`_song_row_info()`、`_score_song_row()`、`_pick_best_song()`：
  搜索结果的合唱、对唱、多人合唱、Live、伴奏、翻唱片段一律降权（-3.0），歌手字段出现多人再降权（-2.5）。
- 新增 `_song_detail()`（`/api/song/detail` 带缓存 `temp/song_detail.json`）与 `_ensure_solo_song()`：
  拿到 id 后回查歌名/歌手，若歌手 ≥2 人或标题带合唱字样，就用「歌名 + 第一位歌手」重新搜索，
  优先取**标题一致、单人、非 Live/伴奏**的版本；找不到才保留原曲。开关 `BACKEND_SOLO_ONLY=0` 可关闭。
- `resolve_audio()` 在确定 song_id 之后统一过一遍 `_ensure_solo_song()`，因此无论插件传的是
  **歌名** 还是 **歌曲 id**，都会自动换成独唱版本。
- 搜索缓存键改为 `v2|关键词`，旧的（可能存着合唱版 id 的）缓存自动作废。

### 6.2 插件 astrbot_plugin_matsuko_cover/main.py
- `_smart_cover` 的相似度打分（`selected_song = songs[0]` 那段）新增两项惩罚：
  标题/歌手命中 `_duet_hints` → -3.0；歌手字段按 `、&/,+＋feat.` 切出 ≥2 人 → -2.5。
- 这样插件在「自动选第一首」时就不会再选到合唱版。

### 6.3 实测
```
_ensure_solo_song("2751025923")   # 落叶归根 - 王力宏、单依纯（合唱）
-> 2148807245                     # 落叶归根 - 李佳薇（单人 studio 版）
_song_detail("2751025923") = {'name': '落叶归根', 'artists': ['王力宏', '单依纯']}
resolve_audio("2751025923") -> temp/songs/2148807245.mp3 (10.5MB, 4.7s)
_ensure_solo_song("28793502")  -> 28793502（本来就是单人，保持不动）
```
注：网易云上王力宏本人的《落叶归根》只有 Live 版（25642234），studio 独唱只有李佳薇的翻唱版，
所以这里选的是李佳薇版；如果你更想要王力宏 Live 版，把 `_DUET_WORDS` 里的 `live` 去掉即可。
