# TTS 提供商源码（AstrBot）

这两个文件原本直接被塞进 AstrBot 的虚拟环境里：

```
astrbot/.venv/Lib/site-packages/astrbot/core/provider/sources/
```

放在这里是为了可复现。安装时（Windows）：

```powershell
$site = "astrbot/.venv/Lib/site-packages/astrbot/core/provider/sources"
Copy-Item providers/gl_tts_source.py  $site -Force
Copy-Item providers/bv2_tts_source.py $site -Force
```

然后还要在 `astrbot/core/provider/manager.py` 的 `match` 分支里注册适配器，并且
`astrbot/data/cmd_config.json` 里加 provider 条目（`cmd_config.json` 属于私人配置，
不入库；参考 `astrbot/data/cmd_config.json.example` 的 `provider` 列表结构）。

## gl_tts_source.py — 原神 16 角色 Bert-VITS2（本地，114000 步）

- 适配器类型：`gl_tts_api`，名称「原神 16 角色 Bert-VITS2（本地，114000 步）」。
- 权重：`guetLzy/Bert-vits2` 的 `G_114000.pth` + `config.json`（v1.x 结构，
  `spk2id` 里 16 个角色：钟离/申鹤/魈/胡桃/香菱/行秋/纳西妲/神里绫人/心海/烟绯/
  宵宫/丽莎/可莉/提纳里/神里绫华/绮良良）。放到 `bertvits2/pretrained/guetlzy/`。
- 推理脚本：`bertvits2/bv2_v1_say.py`（复用 Bert-VITS2 仓库 `oldVersion/V110/models.py`，
  `n_layers_trans_flow=4` 才与 checkpoint 对齐；ja_bert 走 768 维零张量）。
- 配置键：`gl_python / gl_script / gl_device / gl_sdp / gl_noise / gl_noise_w /
  gl_length / gl_sid / timeout`，默认 `gl_sid = 纳西妲`。
- 输出目录必须在 `astrbot/data/` 之下（陪伴插件会拒绝 data 目录外的语音路径），
  默认 `astrbot/data/temp/tts_gl/`。

## bv2_tts_source.py — 知更鸟 Bert-VITS2（早期方案，已不默认）

- 适配器类型：`bv2_tts_api`，权重 `Sprt98/Bert-VITS2_HSR_Robin` 的 `G_5800`。
- 推理脚本：`bertvits2/bv2_say.py`（含 `_dereverb` 去混响、`_shape_prosody` 语调整形、
  60 字以内整段一次合成）。
- 因为 5800 步的权重语调偏平、机械感重，线上已切到 `gl_tts`（纳西妲），
  这个适配器保留作 A/B 与回退用。
