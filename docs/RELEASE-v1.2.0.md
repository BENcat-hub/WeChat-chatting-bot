## v1.2.0 — 微信语音气泡 / 原神 16 角色 TTS / 纯场景写实 / 发送防刷屏

### 新能力

- **微信语音气泡**：`scripts/voice_send.py` 把 TTS 或翻唱音频经 VB-Cable 虚拟声卡喂给微信录音条，
  发成真正的语音消息（`localType=34`），并回读校验时长与音量。私聊 `localId=251/258/261`、
  群聊 `46` 全部验证通过。
- **原神 16 角色 Bert-VITS2（纳西妲默认）**：`providers/gl_tts_source.py` + `bertvits2/bv2_v1_say.py`，
  权重 `guetLzy/Bert-vits2` 的 **G_114000**（16 个角色可选）。替换掉 5800 步、语调偏平的知更鸟。
- **语音/文字互斥**：`akasha-bridge/ui_lock.py`，避免 mic 与发送点击落到别的会话。

### 修复

- **生图纯场景被塞人设**：`image_runtime._strip_persona_for_pure_scene()` 在调 API 前剥掉
  「7 头身少女 / Keep the reference face / Pose override / Hands with five clear fingers」，
  橘猫不再长人类头发和双腿；自拍等人像请求保持原样。
- **翻唱/消息重复刷屏**：`scripts/wxui.py` 时间窗校验 + 晚到补验 + 180s 同文本去重 + `api()` 重试；
  `akasha-bridge/wxui_sender.py` 队列按「时间窗 + 内容」预检、`attempts >= 2` 放弃，
  杜绝同一条消息连发十几遍。
- **`ffmpeg not found`**：`scripts/astrbot-start.ps1` 把 `tools/ffmpeg` 加进 PATH；
  超过 60s 的翻唱直接发文件，不做语音尝试。
- **折叠置顶聊天定位失败**：`wxui._expand_pinned_section()` 先展开「折叠置顶聊天」再扫描。
- **点歌后端一键启动**：`start-cover-backend.ps1` 改 HTTP 探活 + 假死清理；`start-all.ps1`
  去掉 `Test-Port 3333` 短路。
- **群聊阶段性总结死循环**：`summarizer._auto_refs_for_fact()` 在模型给错 refs 时按内容回捞
  timeline id，修复 `evidence_gate_rejected` 重试 20 次的问题。
- **生图后端**：火山图生图 401/50400 → 切换 SiliconFlow（`JIMENG_BACKEND=sf`）。
- **每日穿搭图** `reference_unavailable` 修复。

### 文档

- `docs/CHANGELOG-20261022.md`（详细背景、根因与实测数据）
- `providers/README.md`（TTS 提供商安装与配置键）

**升级提示**：`providers/` 下两个文件需要手动复制到
`astrbot/.venv/Lib/site-packages/astrbot/core/provider/sources/`，并在
`astrbot/core/provider/manager.py` 注册 `gl_tts_api` / `bv2_tts_api`。
