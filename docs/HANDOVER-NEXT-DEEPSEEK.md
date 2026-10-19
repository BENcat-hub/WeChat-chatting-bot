# 微信 AI 伴侣系统 · 交接文档（给下一个 DeepSeek 对话）

> 生成时间：2026-10-18 21:35（Asia/Shanghai）
> 适用状态：AstrBot **4.29.1**，WeFlow **5.0.0**（打过 dll 补丁），微信小号「**用户E**」
> 本文只讲**现在真实在跑的东西**。历史事故与评测细节在 `docs/` 下各专题文档里，本文末尾给了索引。

---

## 0. 30 秒速读

这是一个「微信小号 = 有性格的 AI 女友」的完整工程：微信收发 + 长期记忆 + 主动发消息 + 自己拍照发图。
它由 **5 个进程**串成一条链，任何一环挂掉都会表现为「她不回消息 / 发不出图 / 照片不对」。

```
微信客户端 Weixin.exe（人工扫码登录）
   │  读：wcdb 数据库        │  发：Windows UI 自动化（wxui.py）
   ▼                        ▲
WeFlow 5.0.0 :5031  ────────┘（回读校验）
   │  SSE /api/v1/push/messages
   ▼
akasha-bridge :8766  ── OneBot v11 反向 WS ──▶  AstrBot 4.29.1 :11229
   │                                              │  面板 :6185
   │                                              ├─ 8 个插件（人设/记忆/生图/主动/表情包…）
   │                                              ├─ LLM：deepseek-flash（主对话）
   │                                              └─ 生图：本机 :8790（火山即梦签名包装）
   └─ 发送：调用 scripts/wxui.py 点微信窗口，并用 WeFlow 回读确认送达
```

**四个最常见的「坏了」**：5031 不监听（WeFlow 组件/版本）、8766 发送失败（微信窗口被最小化）、生图 8790 不通（本地图片服务）、面板「更新失败」（pip 安装不支持面板自更新）。

---

## 1. 进程 / 端口 / 日志 一览

| # | 服务 | 端口 | 启动方式 | 日志 |
|---|---|---|---|---|
| 1 | 微信 `Weixin.exe` | 无 | 手动/`start-all.ps1` | — |
| 2 | **WeFlow 5.0.0** | `5031` | `scripts\start-weflow50.cmd`（清 `ELECTRON_RUN_AS_NODE` 后跑 `node_modules\electron\dist\electron.exe <树>`） | `%APPDATA%\weflow` 下 wcdb.log；树在 `C:\Users\...\Documents\<WEFLOW_SRC>` |
| 3 | **AstrBot 4.29.1** | `6185` 面板 / `11229` OneBot WS | `.venv\Scripts\astrbot.exe run`（`start-all.ps1` 注入 `DEEPSEEK_API_KEY`） | `logs\astrbot-<ts>.log` + `.err.log` |
| 4 | **即梦生图本地服务** | `8790` | `python -X utf8 scripts\jimeng_image_server.py --port 8790` | `logs\jimeng-<ts>.log` |
| 5 | **akasha-bridge** | `8766` | `akasha-bridge\.venv\Scripts\python.exe main.py` | `akasha-bridge\bridge.log`（注意：不在 `logs\`） |
| — | WeeMessenger | `9877` | **故意不启动**，只有发表情包才需要 | — |

一键启停：

```powershell
cd C:\Users\<YOU>\Documents\<WORKSPACE>
pwsh -File wechat-ai-stack\scripts\start-all.ps1                 # 全套
pwsh -File wechat-ai-stack\scripts\start-all.ps1 -SkipWeChat -SkipWeFlow -SkipBridge -NoPreflight   # 只重启 AstrBot
pwsh -File wechat-ai-stack\scripts\stop-all.ps1                  # 按 logs\pids.json 停，再按端口兜底
```

> `start-all.ps1` 会：还原微信窗口（`show-wechat-window.py`）→ 检查/补 `ffmpeg-static\ffmpeg.exe` → 起 WeFlow → 起 AstrBot（`ASTRBOT_RELOAD=1` 热重载）→ 起 8790 → 起 bridge → 写 `logs\pids.json` → 端口自检。

---

## 2. 接口清单（这个项目用到的全部）

### 2.1 WeFlow REST/SSE（`:5031`，消息读取唯一来源）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/sessions?access_token=<TOKEN>` | 会话列表（bridge 启动时预填「会话 id ↔ 联系人」映射） |
| GET | `/api/v1/messages?access_token=<TOKEN>&...` | 拉历史/回读校验（发送后确认是否真送达） |
| GET | `/api/v1/push/messages?access_token=<TOKEN>` | **SSE 长连接**，微信新消息实时推送 |
| GET | `<media_url>?access_token=<TOKEN>` | 下载图片/语音等媒体（HEVC/wxgf 依赖 ffmpeg 解码） |

- TOKEN 在 `wechat-ai-stack\akasha-bridge\config.json` 的 `access_token`（**别外传**）。
- 调用实现：`wechat-ai-stack\akasha-bridge\bridge_core.py`（sessions 见 :57，SSE 见 :374，媒体下载见 :514）。
- 自测：`scripts\weflow_sessions.py`、`scripts\weflow_last_messages.py`。

### 2.2 OneBot v11 反向 WebSocket（AstrBot `:11229`）

- 地址：`ws://127.0.0.1:11229/ws`（`bridge\config.json` 的 `astrbot_ob_url`）
- AstrBot 侧适配器：`aiocqhttp`，platform id = `default`（`astrbot\data\cmd_config.json` → `platform`）
- 事件：`message/private`、`message/group`；动作：`send_msg`、`get_login_info` 等（实现对：`akasha-bridge\ob_protocol.py`、`ob_client.py`）
- 协议细节与 id 映射：`akasha-bridge\ob_id_map.json`

### 2.3 AstrBot 面板 API（`:6185`）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/auth/login` `{username,password}` | 换 JWT |
| GET | `/api/plugin/page/content/<插件名>/page/<页面>` | 插件页面数据（**401 = 没带 token，404 = 路径写错**） |
| POST | `/api/plugin/reload` `{"name":"<插件名>"}` | 热重载单个插件 |
| POST | `/api/plug/<插件名>/page/troubleshooting/test` | 插件自带排障测试（如 `{"type":"image_generation_selfie","prompt":"","use_reference":true,"timeout_seconds":120}`） |

自己签 token 的办法（不想在页面登录时）：
`astrbot\data\cmd_config.json` → `dashboard.jwt_secret`，用 HS256 自己签，再 `Authorization: Bearer <token>`（完整步骤见 `docs\IMAGE-QUALITY-FINDINGS.md`）。

### 2.4 即梦生图本地服务（`:8790`，OpenAI 兼容）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/images/generations` | 文生图。请求：`{"model":"jimeng-4.6","prompt":"...","size":"1024x1024","response_format":"b64_json"}`；带参考图时附 `image`（data URL/base64） |
| POST | `/v1/images/edits` | 改图（同结构） |
| GET | `/v1/models` / `/v1/status` | 探活（keeper 用它判断要不要拉起） |

- 实现：`wechat-ai-stack\scripts\jimeng_image_server.py`（火山引擎 V4 签名 → 即梦 4.6；备用 4.0）
- 密钥：`wechat-ai-stack\secrets\volcengine_ak_sk.txt`（**别外传**）
- 排障：`scripts\_probe_jimeng_api.py`、`scripts\probe_jimeng_size.py`、`scripts\jimeng_server_keeper.py`（连续 2 次探活失败才重启，最多 5 次）

### 2.5 akasha-bridge 控制 API（`:8766`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 可视化面板（状态/日志/配置/主动发送） |
| GET | `/status`、`/senders` | 运行状态 / 各发送通道可用性（`ok=false` 常见于「微信窗口没找到」） |
| GET/POST | `/api/config` | 读写 `config.json`（改完重启 bridge 生效） |
| POST | `/send` `{"target":"微信昵称或群名","text":"..."}` | 主动发文本 |
| POST | `/send_image` `{"target":"...","path":"绝对路径"}` | 主动发图片 |
| POST | `/send_sticker` `{"target":"...","path":"..."}` | 主动发表情包 |
| POST | `/start` `/stop` `/pause` `/resume` | 桥接开关（`/stop` 之后微信消息不再进 AstrBot） |
| POST | `/mode` `{"mode":"mention|all|batch"}` | 群聊回复模式 |

出站护栏（`config.json`）：`send_whitelist` / `send_blacklist` / `send_min_interval_*` / `send_dry_run` / `send_max_text_length`。**白名单为空 = 不限制回复对象**。

### 2.6 外部依赖

| 服务 | 用途 | 配置位置 |
|---|---|---|
| DeepSeek `https://api.deepseek.com` | 主对话模型 `deepseek-flash`；记忆整理/人设评委用 `deepseek-v4-pro` | 环境变量 `DEEPSEEK_API_KEY`（User 作用域，由 `start-all.ps1` 注入子进程） |
| SiliconFlow `https://api.siliconflow.cn/v1/embeddings` | 表情包语义检索 embedding | AstrBot provider `siliconflow-embed` |
| 小米 mimo（TTS/STT） | 语音与语音增强 | provider `mimo_tts` / `mimo_stt` |
| 火山引擎即梦 | 生图上游 | `secrets\volcengine_ak_sk.txt` |
| 微信 UI 自动化 | **发送唯一可靠通道**（点击会话行 + 回读验证） | `scripts\wxui.py`（66 KB），配置 `scripts\wxui-config.json` |

---

## 3. 关键目录与文件

```
wechat-ai-stack\
├─ astrbot\                       AstrBot 本体（.venv + data + 插件）
│  ├─ .venv\                      Python 3.12.15；备份 .venv-4.28.2-bak（回滚用）
│  ├─ data\cmd_config.json        核心配置（providers / platform / dashboard / persona）
│  ├─ data\config\*_config.json   各插件配置
│  ├─ data\plugins\               8 个插件（见 §4）
│  └─ data\plugin_data\<插件>\    插件运行时数据（人设、记忆、生成的图、状态库）
├─ akasha-bridge\                 中间桥（WeFlow ⇄ OneBot，发送调度）config.json / bridge.log
├─ scripts\                       200+ 运维与排障脚本（wxui.py / jimeng_image_server.py / start-all.ps1 …）
├─ docs\                          专题文档（本文索引见 §10）
├─ logs\                          astrbot-*.log / jimeng-*.log / pids.json / 各类证据图
└─ secrets\                       volcengine_ak_sk.txt 等（敏感）
```

插件数据里最需要认识的几个目录（`astrbot\data\plugin_data\astrbot_plugin_private_companion\`）：

| 路径 | 内容 |
|---|---|
| `generated_photos\` | 她生成并发出过的照片（`default_<session>_<时间戳>_<hash>.png`） |
| `photo_reference_images\` | 人设参考图（`persona_yubao_reference.png` 等，生图时挂给模型） |
| `persona_profiles\` | 人设卡（默认人设 + 备份） |
| `image_generation_state.json` | **生图取证的核心文件**：`owners.<owner_dir>.recent_photo_generations[]`，每条含 `prompt`、`prompt_sections`、`reference`、`scene_preset`、`elapsed_ms` |

---

## 4. 插件清单（`astrbot\data\plugins\`）

| 插件 | 版本 | 职责 |
|---|---|---|
| `astrbot_plugin_private_companion` | 无版本号（目录插件） | **主链**：人设、防抖、分段回复、记忆注入、TTS 预留、主动消息、生图场景构建、面板 |
| `astrbot_plugin_image_companion` | 0.4.5 | 生图/改图/参考图库/多后端（本机走 8790；引擎 `mode=legacy`，复用陪伴插件资产） |
| `astrbot_plugin_memory_companion` | 1.10.5 | 长期记忆、知识图谱、自我时间线、情绪漂移、开场悬而未决的事 |
| `astrbot_plugin_proactive_chat` | 1.2.6 | 主动消息调度（与主链计划器协同） |
| `astrbot_plugin_affection` | 1.3.1 | 双驱情绪模型（力比多/攻击性） |
| `meme_manager` | 5.1.0 | 表情包库（语义检索，embedding 用 SiliconFlow，工具 `search_memes`） |
| `astrbot_plugin_matsuko_cover` | 2.13.0 | AI 翻唱（RVC/SVC、QQ 音乐点歌、本地音频）。**2026-10-18 21:23 从面板新装**，依赖 `gradio-client`、`qqmusic-api-python` 已自动 pip 进 venv |
| `astrbot` / `builtin_commands` | 4.4.0 / 0.0.1 | AstrBot 自带内置插件（不必动） |

---

## 5. 端到端流程

### 5.1 被动回复（她回你一条消息）

1. 微信收到消息 → WeFlow 写进 wcdb → SSE `push/messages` 推送
2. `bridge_core.py` 收到：按 `bot_nicknames` 判断是不是找她的；图片按 `image_forward_mode=direct` 以 CQ 码直通；`buffer_seconds=0` 表示不缓冲
3. 组装 OneBot 事件 → `ws://127.0.0.1:11229/ws` → AstrBot event bus
4. `private_companion` 主链：智能防抖 → 静默判断 → 记忆注入（memory_companion）→ 被动状态提示词 → LLM `deepseek-flash` → 分段计划（≤3 句、间隔 1.5–3.5s）→ TTS 预留 → `decorating_result`
5. bridge 调 `wxui.py`：定位会话行 → 点击 → 粘贴/回车 → **用 WeFlow 回读校验是否送达**
6. 失败进 `wxui_pending.jsonl`，每 20s 重试（最多 5 次）；已经送达的绝不重发

### 5.2 主动消息

`proactive_chat` 定时问「要不要发」，`private_companion` 的计划器决定发什么（人设评委打分 ≥34 才发，每日上限），再走 5.1 的第 4–6 步。主动消息会「分享照片」：`proactive_share_probability=45`。

### 5.3 生图链路（最容易出问题，必须看懂）

```
用户「拍一张午饭」/主动分享
  → private_companion 场景构建  proactive_message.py:14367 _photo_scene_generation_prompt_document
        （场景预设、人物块、Pose override、负面约束；第 12/13 条规则约束「纯场景不得出现人」）
  → 风格决定              proactive_message.py:14790 _get_photo_style_instruction
        + :11964 _photo_style_prompt_en（二次元 / 真实 / 自定义）
        + :14521-14532 纯场景且 style=二次元 且 :11975 _photo_pure_scene_realistic() → 强制切「真实」实拍风
  → 提示词拼装            image_companion\image_runtime.py:11118 _apply_photo_generation_prompt_format
        · :11049 _photo_prompt_mentions_person（先剥掉否定句再判「有没有人」）
        · :11106 _photo_prompt_wants_photographic_style（纯场景 + 写实措辞 → 写实开头/质感尾句）
  → 尾部硬约束            image_companion\photo_prompt_context.py:731 _critical_requirements_tail
        （:762 二次元尾句 / :767 实拍质感尾句；画面里有人 → 二次元，没人且要写实 → 实拍）
  → 参考图                private_companion\command_handlers.py:3541+（photo_reference_images / persona_profiles）
  → 提交                  POST http://127.0.0.1:8790/v1/images/generations
        model=jimeng-4.6（备用 4.0） size=1024x1024 timeout=180s
  → 落盘                  generated_photos\default_<session>_<ts>_<hash>.png
  → 取证                  image_generation_state.json → recent_photo_generations[0]
  → 发送                  bridge /send_image（或 AstrBot 媒体投递）
```

关键日志串（生图一次完整走完）：

```
[图像陪伴桥接] 生图前置耗时: reference_paths=... read_refs=...
生图开始 ... reference=True/False
在线图片 API 生图提交: endpoint=http://127.0.0.1:8790/v1/images/generations model=jimeng-4.6 size=1024x1024 prompt_chars=783
生图响应 status=200
生图结束 ok=True elapsed=16138ms reference_used=False
```

---

## 6. 常见问题速查（症状 → 原因 → 处置）

**Q1 面板点「更新」失败：`You are running AstrBot via CLI`**
AstrBot 面板自更新只支持桌面打包版；pip/venv 安装必须手动升级。
处置：`cd astrbot; .\.venv\Scripts\python.exe -m pip install -U astrbot==<版本>`，装前先备份 `.venv`（本次升级留了 `.venv-4.28.2-bak`）。

**Q2 WeFlow `5031` 不监听 / 报 `-101`**
`C:\Program Files\WeFlow` 6.3.2 的 `wcdb_api.dll` 有「构建日+15 天」硬时限且混淆，不可修（改时钟、改 PE 时间戳、NOP 跳转都试过）。
处置：只能用 5.0.0 树（`C:\Users\...\Documents\<WEFLOW_SRC>`），它的 dll 只打了两处 4 字节补丁。**不要同时开 6.3.2 和 5.0.0**（共用 `%APPDATA%\weflow` 和同一个微信 DB 句柄）。详见 `docs\WEFLOW-101-FINDINGS.md`。

**Q3 WeFlow 启动即退（`ELECTRON_RUN_AS_NODE`）**
用 `scripts\start-weflow50.cmd` 启动（它会清掉该环境变量），不要直接双击 electron.exe。

**Q4 发不出消息 / `/senders` 显示 `ok=false`、`WeChat window not found`**
微信窗口被最小化或收进托盘。处置：`python -X utf8 scripts\show-wechat-window.py`，或重跑 `start-all.ps1`（它启动时会无条件还原一次）。

**Q5 她「看不了图」**
WeFlow 5.0.0 树的 `node_modules\ffmpeg-static\ffmpeg.exe` 缺失（npm 只装了 JS）。`start-all.ps1` 会自动从 6.3.2 安装目录补；手动见 `docs\WEFLOW-IMAGE-FFMPEG-FIX.md`。

**Q6 生图「凭空长出一个陌生人」**
两层原因，都已修：
① 提示词里有否定句（`do not insert an unrequested person`），旧代码把这种句子也算成「画面里有人」→ 现在先剥否定句再判（`image_runtime.py:11049`）。
② 人设句/姿势句在纯场景里被注入 → 现在 `character_present` 门控（`image_runtime.py:11106`、`photo_prompt_context.py:731`）。
若复发：看 `image_generation_state.json` 里该条的 `prompt` 和 `character_present` 相关的 `prompt_sections`。

**Q7 让她拍午饭，结果拍出她自己**
同 Q6 的②；另外检查场景规则（`proactive_message.py:14367` 的 doc 第 12/13 条）是否被改动。

**Q8 照片「质感不对」，像平涂插画**
风格配置是 `二次元`（两处：`private_companion` 的 `photo_action_config.photo_generation_style`、`image_companion` 的 `image.photo_generation_style`）。
处置：纯场景已自动切实拍（`photo_pure_scene_realistic`，默认 true）；人物照片仍二次元。要全写实就把风格改成「真实」——但人设画风会一起变。

**Q9 人物「不像她」**
参考图没挂上：看状态文件里 `reference=False` / `reference_used=False` 和 `reference_plan.selection_reason`。参考图在 `photo_reference_images\`，人设为 `persona_yubao_reference.png`。另外确认 `enable_photo_reference_image=true`。

**Q10 生图 400/500**
先 `curl http://127.0.0.1:8790/v1/status` 探活；再看 `logs\jimeng-*.log`。火山上游拒绝要分层判：只发签名头判签名；逐 `req_key` 判哪个模型被拒（脚本 `scripts\probe_volc_reqkeys.py`）。

**Q11 LLM 报 `Missing credentials` / provider 不可用**
`DEEPSEEK_API_KEY` 没进进程环境。必须用 `start-all.ps1` 启动（它从 User 作用域读入后再起子进程），不要手动 `astrbot run`。

**Q12 面板插件页 `401` / `404`**
`401` = 没带 token（正常）；`404` = 路径错。正确路径：`/api/plugin/page/content/<插件名>/page/<页面名>`。

**Q13 日志里出现 `[主链] 已修复热更新残留回调绑定: handlers=113`**
正常现象：热重载后清掉重复注册的回调。若 handlers 数量异常增长，说明反复热重载，整进程重启即可。

**Q14 重复回复 / 串台**
检查 `bridge\config.json` 的 `buffer_seconds`（0 = 不缓冲）、`smart_message_debounce_*`、以及 wxui 的回读重试队列（已送达不重发）。

**Q15 WeeMessenger 发文本失败 / 发到了「搜索网络结果」**
微信 4.x 下它的搜索通道不可靠。文本一律走 `sender_backend=wxui`；WeeMessenger 只在发表情包时用。

**Q16 重启后端口还被旧进程占用**
`Get-NetTCPConnection -LocalPort <端口> -State Listen` 找 PID → `Stop-Process -Id <PID> -Force`；残留的 `python.exe` 可能挂着同端口。

**Q17 升级 AstrBot 后插件不加载 / 报错**
看 `logs\astrbot-*.err.log`；回滚：`Rename-Item .venv .venv-new; Rename-Item .venv-4.28.2-bak .venv`，或 `pip install astrbot==4.28.2` 后按 `logs\pip-freeze-4.28.2.txt` 恢复依赖。

---

## 7. 排查技巧（真正省时间的）

### 7.1 日志三件套

```powershell
$log = Get-ChildItem wechat-ai-stack\logs\astrbot-*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1
Select-String -Path $log.FullName -Pattern '生图开始|生图结束|Traceback|\[ERRO\]' | Select-Object -Last 20
```
- `astrbot-*.log`：主日志（插件用的 `logging_util` 会带 `[插件名]` 前缀）
- `astrbot-*.err.log`：**崩溃/异常只在这里**（只有几百字节时说明没崩）
- `akasha-bridge\bridge.log`：收发、SSE 重连、wxui 失败重试

### 7.2 端口/进程一眼看清

```powershell
Get-NetTCPConnection -State Listen |
  Where-Object { $_.LocalPort -in 5031,6185,8766,8790,11229,9877 } |
  ForEach-Object { "{0} PID {1} {2}" -f $_.LocalPort, $_.OwningProcess, (Get-Process -Id $_.OwningProcess).ProcessName }
```

### 7.3 生图取证（不用重跑就能看到当时发了什么）

```powershell
$s = Get-Content wechat-ai-stack\astrbot\data\plugin_data\astrbot_plugin_private_companion\image_generation_state.json -Raw | ConvertFrom-Json
$e = $s.owners.PSObject.Properties.Value.recent_photo_generations[0]
$e | Select-Object ts, kind, prompt_format, reference, reference_used, scene_preset, submitted_prompt_chars
$e.prompt
```

### 7.4 生图链路「后端烟测」（不经过微信）

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8790/v1/images/generations -Method Post -ContentType 'application/json' `
  -Body (@{ model='jimeng-4.6'; prompt='一碗白米饭和红烧肉，手机随手拍'; size='1024x1024'; response_format='b64_json' } | ConvertTo-Json)
```
出图后存到 `wechat-ai-stack\logs\scene-fix-proof\` 对照（那里已有 4 张历史验证图）。

### 7.5 改完代码怎么验证

**关键前提：必须先把 `data\plugins` 加进 PYTHONPATH**，否则陪伴插件整包 import 失败，会看到成百上千个 `_FailedTest`（纯环境噪声，不是回归）：

```powershell
$env:PYTHONPATH = "C:\...\wechat-ai-stack\astrbot\data\plugins"
cd wechat-ai-stack\astrbot\data\plugins\astrbot_plugin_private_companion
..\..\..\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -t tests -q
```

- **`image_companion` 基线**：131 个用例，固定 **4 failures + 7 errors**（动图/ComfyUI 历史问题，与生图提示词无关）。
- **`private_companion` 基线**：单跑 photo 相关子模块（`tests.test_photo_generation*` 等，506 用例）时只剩 4 errors——2 个 `ModuleNotFoundError: No module named 'pytest'`（缺 pytest，不是代码问题）、1 个同类、1 个真实 harness 缺方法 `AttributeError: '_DeferredPhotoRenderHarness' object has no attribute '_apply_outbound_wechat_style'`（`proactive_message.py:19213`，测试桩过时，与提示词改动无关）。
- **别跑全量**（`discover -s tests`，4565 用例）：里面有一批会真打网络/TTS/模型接口的用例，实测会卡住十几分钟不结束（本次就挂在 `[TTS增强]` 那批上）。只跑你改的文件对应的子模块：
  ```powershell
  ..\..\..\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_photo_generation tests.test_photo_prompt_context -q
  ```
- 也可以 `python wechat-ai-stack\scripts\run_plugin_tests.py`（带兼容层）。
- **改动生效**：`ASTRBOT_RELOAD=1` 下多数插件会热重载；但改了模块级结构/新增文件时，整进程重启最稳。重启命令：
  ```powershell
  .\wechat-ai-stack\scripts\start-all.ps1 -SkipWeChat -SkipWeFlow -SkipBridge -NoPreflight
  ```
  注意会先杀 6185/11229 上的旧进程，拉起新 PID，日志名 `logs\astrbot-<时间戳>.log`。

### 7.6 面板 API 取数（不点网页）

登录 `POST /api/auth/login` 拿 token，或按 §2.3 用 `dashboard.jwt_secret` 自签。

---

## 8. 坑与红线（改代码前必读）

1. **两个 `photo_prompt_context.py` 副本**：`image_companion\`（47.5 KB，含尾句画风逻辑，:762/:767）与 `private_companion\`（44 KB，结构更旧）。实际出图走的是 image_companion 那份；改尾句/负面词只改一份会「改了没效果」。
2. **6.3.2 与 5.0.0 的 WeFlow 绝不能同时跑**（共用 `%APPDATA%\weflow` 和微信 DB 句柄）。
3. **JSON 配置是 UTF-8 BOM**（`cmd_config.json`）：用 Python 读要 `encoding="utf-8-sig"`，写要保留 BOM，否则 AstrBot 起不来。
4. **人设输出约束**：简体中文、口语、每次至多 3 句、不承认自己是 AI（`basic_config.default_style` / `reply_style_prompt`）。改提示词别破坏这些。
5. **生图负面词模式 `safe_default`** 不要随意改，改了容易出水印/畸形。
6. **微信登录态无法自动**：必须人工扫码；没登录时 WeFlow 读不到、wxui 发不出。
7. **`send_whitelist` 为空 = 谁都能回**，生产环境要收紧。
8. **`secrets\`、`bridge\config.json`、`cmd_config.json` 里都有密钥**：交接/截图/发文档时不要带明文。
9. **`start-all.ps1` 启动的 AstrBot 跑在后台 pwsh 作业里**：如果会话被回收，AstrBot 可能一起退出——重启用 `start-all.ps1` 即可。
10. **面板装插件会往 venv 里 pip 装依赖**（2026-10-18 装 matsuko_cover 时自动装了 `gradio-client`/`qqmusic-api-python`/`niquests` 等十余个包）。所以 `.venv-4.28.2-bak` 这份备份**不含**这些后装依赖：回滚 venv 后要重新 `pip install -r data\plugins\astrbot_plugin_matsuko_cover\requirements.txt`，否则该插件起不来。

---

## 9. 今天的改动（2026-10-18，供接手时对齐）

| 时间 | 改动 | 文件 |
|---|---|---|
| 下午 | 纯场景不得出现人物（否定句误判修复） | `image_runtime.py:11049`、`photo_prompt_context.py:731` |
| 傍晚 | 生图场景规则新增第 12/13 条 | `proactive_message.py:14367` |
| 20:50 | 纯场景自动切写实风（食物/物品/风景），人物维持二次元 | `proactive_message.py:11975/14521`、`image_runtime.py:11106`、`photo_prompt_context.py:762-767` |
| 21:20 | AstrBot 4.28.2 → **4.29.1**（面板更新按设计不支持 pip 安装） | `.venv`（备份 `.venv-4.28.2-bak`） |

**未决**：
- 自拍/人物生活照是否也切写实（用户尚未决定；现在是二次元）
- `private_companion` 里还有 25 个 failure 未逐一确认是否全部环境性（建议只跑改动相关用例）
- 面板「更新」按钮长期不可用，未来升级都需 pip 手动执行

---

## 10. 已有文档索引（深挖时看这些，不要重复造轮子）

| 文档 | 内容 |
|---|---|
| `docs\PROJECT-BOOK.zh-CN.md` | 项目书：架构/组件/时序/配置总表/故障排查/隐私边界 |
| `docs\SETUP.zh-CN.md` | 从零搭建手册（装微信/WeFlow/AstrBot/桥接/第一次跑通） |
| `docs\PLUGINS.zh-CN.md` | 插件配置与验证 |
| `docs\VERIFICATION.md` | 独立验证报告（各链路实测结论） |
| `docs\WEFLOW-101-FINDINGS.md` | WeFlow -101/-102/-103 完整分析（为什么 6.3.2 不可修） |
| `docs\WEFLOW-IMAGE-FFMPEG-FIX.md` | 「看不了图」根因与修复 |
| `docs\IMAGE-QUALITY-FINDINGS.md` | 生图质量客观评测、面板 token 自签、排障接口 |
| `docs\IMAGE-OPTIMIZATION-REPORT-20261016.md` | 生图优化夜班报告（A/B、六轴盲评、身份保真） |
| `docs\JIMENG-API-FINDINGS.md` | 即梦接口实测结论 |
| `docs\BRIDGE-BUFFER-STALE-IMAGE-FIX.md` | 桥接缓冲导致「AI 看到旧图」事故 |
| `docs\WXUI-SEND-SPEED-FIX.md` | 分段发送提速 8.5s → 2.0s |

---

## 11. 给下一个对话的开工清单

1. 先跑一遍 §7.2 的端口检查，确认 5 个服务都在；缺哪个按 §1 起。
2. 用户说「她不回消息」→ 先看 `bridge.log` 有没有 inbound，再看 `astrbot-*.log` 有没有 LLM 调用，最后看 `/senders` 与 wxui 回读。
   若日志里是 `could not locate target row by OCR`：多半是微信里改了备注（见 §12.1），先跑
   `<bridge venv python> scripts\wxui.py rows` 看 OCR 真正读到什么名字，再在 `contact_aliases` 里补一行。
3. 用户说「图不对」→ 先看 `image_generation_state.json` 里那条的 `prompt` 全文，**不要凭印象猜**。
4. 任何修改前：先确认改的是哪一份 `photo_prompt_context.py` / 哪一层（场景构建 / 格式拼装 / 尾部约束）。
5. 上线验证顺序：单测（只看相关用例）→ 直连 8790 渲染一张 → 重启 AstrBot → 让用户真实触发一次。

---

## 12. 2026-10-18 夜间修复（三个真故障，已上线）

### 12.1 改备注后「找不到这个人的对话」→ 侧边栏别名
**症状**：把「用户B」的备注改成「用户B别名」后，bridge 连续报
`❌ 未能验证送达 用户B | 原因=could not locate target row by OCR | 尝试=3`，消息进重发队列。
**根因**：WeFlow 的 `displayName` 仍是旧名（WeChat 数据库里的备注没同步），wxui 却用这个旧名去
OCR 侧边栏，而侧边栏画的是新备注 → `find_row` 永远匹配不到。`logs\wmcp\wxui-1792330648.png`
（21:37 那张失败时的侧边栏截图）第 2 行就是「用户B别名」，可作铁证。
**修法**：`scripts\wxui.py` 增加别名层——`target_keys()/alias_names()` 把 `contact_aliases`
里的名字并进精确匹配集合（`exact_keys`），`talker_for` 与聊天头部安全闸门也走同一套键；
别名**只做整行精确匹配**，不做包含/模糊，否则「用户B别名：嗯」这种群预览行也会命中并发错人。
另外每次 WeFlow 回读确认成功的发送会调用 `_learn_alias()`，把侧边栏实际命中的名字写进
`logs\wxui-aliases.json`，所以**改备注后第一次成功发送会自动学会别名**，手填只是兜底。
配置（`scripts\wxui-config.json`）：`contact_aliases: {"用户B": ["用户B别名"]}`（键值互为别名）。
**诊断命令**（只读，不改任何东西）：`<bridge venv python> scripts\wxui.py rows`
→ 打印 OCR 现在实际读到的侧边栏行、以及当前别名表。

### 12.2 CONTROL_PREEMPTED 被误报成「屏幕截不到 / 锁屏」
**症状**：`/senders` 长期 `session_ok=false, session_reason="screen not capturable (RDP disconnected or workstation locked)"`，
但人工截屏完全正常，微信窗口也在。
**根因**：windows-mcp（`windows_mcp 0.8.8`，`uv\tools\windows-mcp\...\desktop\control.py`）的
control latch 被 `_recover_after_rehook` 钉在 `state=user / wait_seconds≈10.0`，此后它**拒绝一切工具**
（连只读 Screenshot 都返回 `{"code": "CONTROL_PREEMPTED"}`），于是 `shot()` 拿不到 image、报出上面那句假诊断。
latch 是进程内的，卡住的长寿 MCP server 永远好不了；新起的客户端直接 `ready`。
**修法**：`shot()` 保留失败文案（`_SHOT_ERR`），`shot_ex()` 与 `preempted()` 把它翻译成
`desktop control preempted by windows-mcp (CONTROL_PREEMPTED: …)`；`fail()` 统一写入 `control_preempted` 标记；
`wait_for_session()` 遇到 `state=user 且 wait>=9.9s`（钉死签名）立即失败而不是空等 60s；
bridge 的 `wxui_sender._run()` 看到该标记会 `_worker_kill()` 丢掉常驻 worker，下一条用全新客户端重试（自愈）。
**验证**：进程内注入假 data 证明 preempted 会回收 worker、普通失败不会；`wait_for_session` 在假 latch 下 0.0s 返回。

### 12.3 输入框残留被拼进新消息（input_fast_clear）
`test_wxui_recovery.py` 的 B 用例（先粘贴残留文本再发送）实测出真 bug：fast clear 只发一个 `delete`、
不先点进输入框，焦点不在框里时是空操作，残留文本会和正式消息**合成一条**发出
（WeFlow 实测：`[自检B] 残留不该被发出 220821[自检B] 正式消息 220821`）。
已把 `input_fast_clear` 默认改成 `false`（省 1.5s 不值得冒发错内容的风险），见 `_fast_clear_note`。

### 12.4 本次上线动作与验证
- 改了：`scripts\wxui.py`、`scripts\wxui-config.json`、`akasha-bridge\wxui_sender.py`。
- 重启：bridge（PID 旧 48600/57012 → 新 67468，日志 `logs\bridge-20261018-221340.log`）。
  重启后 `/status` running=true / ob_connected=true / weflow_connected=true；`/senders` session_ok=true、control_state=ai。
- 测试：`test_wxui_name_match.py`、`test_wxui_clamp.py`、`test_guards.py` 全过；
  `test_wxui_recovery.py` A–E 全 PASS（含真实发送到「文件传输助手」，WeFlow 回读确认 localId=93/94/95/96）；
  bridge 发送器直连实测送达 localId=97。重发队列 0 条。
- **注意**：`pytest` 不在任何本机解释器里（bridge venv / uv python / astrbot venv 都没有），
  这几个自测脚本都是**直接 `python 脚本.py` 跑**的，别用 `-m pytest`。

---

### 附：可直接粘贴给新对话的开场白
> 这是一个「微信小号 AI 伴侣」项目，工作目录 `C:\Users\<YOU>\Documents\<WORKSPACE>`。
> 先读 `wechat-ai-stack\docs\HANDOVER-NEXT-DEEPSEEK.md`，那是完整交接（架构、接口、流程、排障）。
> 当前状态：AstrBot 4.29.1 / WeFlow 5.0.0 / 桥接 8766 / 生图 8790。改动前先按文档 §7 做体检，不要凭猜测改代码。
