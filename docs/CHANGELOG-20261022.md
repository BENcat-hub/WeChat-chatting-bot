# 2026-10-22 批次：语音气泡 / 原神 TTS / 生图写实 / 发送可靠性

这一批改动集中在四件事：**让机器人能发真正的微信语音气泡**、**把 TTS 换成训练步数更多的
角色模型**、**让「画面里没有人」的请求真的画成写实照片**、**把「一直重发、刷屏」的投递链路
焊死**。

## 1. 微信语音气泡（新能力）

微信 4.x 发语音不是「按住说话」，而是：点右下圆形麦克风 → 胶囊开始录（最长 60s）→ 点绿色 ↑。
做法是把生成的音频**当成麦克风输入**喂给微信：

- 虚拟声卡：VB-Audio Virtual Cable（Pack45 的 `vbMmeCable64_win10.inf`，
  `pnputil /add-driver ... /install`），出现 `CABLE In 16 Ch`（播放侧）与
  `CABLE Output`（采集侧）。
- `scripts/set-default-audio.ps1`：PowerShell + C# 内联 IPolicyConfig，把默认录音设备切到
  `CABLE Output`，发完再切回真麦克风。
- `scripts/voice_send.py`：`TTS/翻唱 wav → 重采样 48k → 切默认麦克风 → 点 mic → 播放 →
  等胶囊出现 → 点 ↑ → WeFlow 回读 localType=34 校验`，并输出 `heard{dur,rms,peak,first_sound_s}`
  与 `overrun_ms`（气泡时长 - 音频时长，>4000ms 判失败）。
- **坑**：CABLE In 的 sounddevice 设备索引会随默认设备切换而变（24 → 8），写死索引会得到
  一条 45 秒的全静音气泡（`rms 0`）。现在按名字找 `cable in`（`play_device()`）。
- 群聊/私聊都验证过：`localId=251/258/261` 私聊、`46` 群聊，回读全是 `localType=34`。

## 2. TTS：知更鸟 → 原神 16 角色（纳西妲）

公开可下载的知更鸟 Bert-VITS2 只有 `Sprt98/Bert-VITS2_HSR_Robin` 的 5800 步一档，
语调平、机械感重。按用户要求「换个角色，找训练步数最多的」，选定
`guetLzy/Bert-vits2`（**G_114000**，16 个原神角色），默认音色 **纳西妲**：

- `bertvits2/bv2_v1_say.py`：v1.x 结构推理（`n_layers_trans_flow=4`；`oldVersion/V110/models.py`
  直接 import 会连带拉起 MeCab，用空 parent package 绕开 `__init__`）。
- `providers/gl_tts_source.py`：AstrBot `gl_tts_api` 适配器 → `provider_tts_settings.provider_id = gl_tts`，
  陪伴插件的 `tts_provider_id_zh` 同步切过去。
- 真机：`localId=289`，纳西妲 4.24s 气泡，`rms 0.092`。

## 3. 生图：纯场景不再被塞人设

早上「画一只橘色小猫」被画成二次元、而且猫长着人类头发和人类双腿。根因不是风格开关，
而是提示词里被注入了一句人设比例：

```
Positive prompt: [User image request]
Petite slender teen girl, small head, about 7 heads tall, long legs, orange tabby cat, …
```

`astrbot_plugin_image_companion/image_runtime.py` 新增
`_strip_persona_for_pure_scene()`，在真正调图片 API 前把「7 头身少女 / Keep the reference
face… / Pose override… / Hands with five clear fingers.」这几句剥掉，判据是**剥完之后**
再看请求里还有没有人物（人物场景/自拍照旧保留人设）。真机验证：橘猫图变成写实照片，
自拍请求仍然带人设。

## 4. 发送可靠性：四道防刷屏防线

现象：翻唱 mp3 没发出去，同一条「已成功使用 SVC 音色…」在群里连出 12 条（`localId=452…463`）。

- `scripts/wxui.py`
  - `api()` 加 3 次重试（WeFlow `/api/v1/messages` 偶发 500，旧代码抛异常 → 被判「没送到」）。
  - `verify(..., since_time=None)`：时间窗始终生效；长文本退化匹配（emoji 变体选择符会被微信改写）。
  - `_assemble()`：判失败前再给 25 秒宽容窗口（晚到回读）。
  - `send()`：180s 内同文本直接跳过（`dedup_recent`）。
- `akasha-bridge/wxui_sender.py`：入队记 `since_time`；队列预检用「时间窗 + 内容」而不是
  `since_local_id`；预检拿不到结论不重发；`attempts >= 2` 直接放弃。
- `scripts/astrbot-start.ps1`：把 `tools/ffmpeg` 加进 PATH，插件转 silk 时不再 `ffmpeg not found`。
- `astrbot_plugin_matsuko_cover/main.py`：>1.15MB（≈60s 以上）的翻唱直接发文件，不做语音尝试。
- `akasha-bridge/ui_lock.py`：语音气泡与文字发送共用一把 UI 锁，避免 mic/发送点击落到别的会话。

## 5. 其它修复

- **折叠置顶聊天**：目标被折进侧栏「折叠置顶聊天」时定位必然失败 →
  `wxui._expand_pinned_section()` 先点开展开再重扫。
- **点歌后端一键启动**：`start-cover-backend.ps1` 由「端口在听就跳过」改为 HTTP 探活 +
  假死进程清理重启；`start-all.ps1` 去掉 `Test-Port 3333` 短路。
- **群聊阶段性总结一直报错**：`summary_failures` 里 `retry_count=20`、
  `last_error=evidence_gate_rejected`。根因是模型给的 `refs` 落不到 timeline 行上，
  key_facts 全被过滤 → 质量判 low → 无限重试。`core/summarizer.py` 新增
  `_auto_refs_for_fact()`，refs 不合格时按内容回捞 timeline id；
  单测：假 refs → 回捞成 `tl_xxx` → `summary_quality == "normal"`。
- **生图后端**：火山图生图 `jimeng_seedream46_cvtob` 对本账号 401/50400，改用 SiliconFlow
  （`Qwen/Qwen-Image` 13s、`Qwen/Qwen-Image-Edit-2509` 56s），
  `scripts/jimeng_image_server.py` 加 `JIMENG_BACKEND=sf`。
- **每日穿搭图**：`reference_unavailable` 修复后真机出图正常。
