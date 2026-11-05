# 语音（纳西妲）与翻唱（孙燕姿）分离 — 2026-11-04 重建

误删事故后这两个功能是重新搭起来的，和 10 月那份 `HANDOVER-NEXT-DEEPSEEK.md` 的写法有出入，以此文档为准。

## 一句话结构

| 用途 | 入口 | 模型目录 | 引擎 |
| --- | --- | --- | --- |
| 唱歌（点歌/翻唱） | Gradio `:3333` `rvc-backend/server.py` | `rvc-backend/models/<名字>/` | so-vits-svc-fork + Demucs |
| 说话（微信语音） | `:8795` `scripts/nahida_tts_server.py` | `rvc-backend/models_voice/纳西妲/` | mimo TTS → so-vits-svc 换音色 |

两个目录刻意分开：`/show_model` 只扫 `models/`，所以唱歌的模型下拉里只会有孙燕姿，纳西妲不会串进去。

## 唱歌

- 模型：`models/孙燕姿/G_27200.pth`（542MB）+ `config.json` + `kmeans_10000.pt`，来自 `YeQing/sunyanzi`。全 HF 上孙燕姿 so-vits-svc 只有 6215 步和 27200 步两档，27200 是步数最多的（`onereal/sunyanzi`、`tommybell/SUNYANZI` 是同一权重）。
- 调用契约（`astrbot_plugin_matsuko_cover/main.py:1005` 起，20 个参数，顺序敏感）：`song_name_src, key_shift, vocal_vol, inst_vol, model_dropdown, reverb_intensity, delay_intensity, uvr5_agg, uvr5_tta, uvr5_postprocess, uvr5_window_size, uvr5_high_end_process, msst_batch_size, msst_num_overlap, msst_normalize, vocal_postprocess, shift_accompaniment, f0_method, index_rate, filter_radius`；返回 `(本地绝对路径, "true"/"false")`。
- 冒烟：`_restore-20261103/smoke_convert.py <song.mp3>` → 35 秒样本出 `outputs/cover-*.wav`，mean_volume −15.7dB。

## 说话

- 流程：`mimo-v2.5-tts`（token-plan，`secrets/mimo_api_key.txt` + `mimo_api_base.txt`）→ ffmpeg 44.1k 单声道 → `svc infer -m models_voice/纳西妲/G_40000.pth -c config.json -fm crepe -t 0`。
- 对外是 OpenAI 兼容：`GET /health`、`POST /v1/audio/speech`（`input`/`text` + `voice`）。AstrBot 侧注册成 `openai_tts_api` 类型 provider，`provider_tts_settings.provider_id=nahida_tts` 即生效，没改 AstrBot 源码。
- 实测：一句 30 字中文 → 5.9 秒 522KB wav；OpenAI SDK 路径（`ProviderOpenAITTSAPI` 同一段代码）也验过 366KB。
- `private_companion.enable_voice_action=True` 才会真的把语音发出去（默认是 `false`）。

## 两个必踩的坑

1. **`svc infer` 不给 `-k`**。torch 2.6+ 默认 `weights_only=True`，`kmeans_*.pt` 是 numpy pickle，直接抛 `WeightsUnpickler error: Unsupported global: GLOBAL numpy.core.multiarray._reconstruct`。
2. **必须 `HF_ENDPOINT=https://hf-mirror.com`**。svc 首次运行会从 `huggingface.co` 拉 `lengyue23/content-vec-best`，本机 github/hf 被 Watt Toolkit 中间人签发证书，直连必挂。`nahida_tts_server.py` 里已经 `env.setdefault` 处理。

## 重启

`scripts/start-all.ps1` 已加 `3.6 纳西妲语音` 段（判端口 → 判模型 → 判 mimo key），文件带 UTF-8 BOM（Windows PowerShell 5.1 不认无 BOM 的中文）。
