# 微信小号 AI 机器人 · 完整链路搭建与使用手册

> 目标：让一个**微信小号**具备「被动回复、主动聊天、发表情包/图片、图片理解、绘图」能力。
> 本机已把整条链路搭好，本文是配置说明 + 人工步骤 + 排错手册。

---

## 一、这套链路长什么样

```
┌──────────────────────┐
│ 微信小号 4.x (Weixin) │  ← 你登录的、专门跑机器人的账号
└──────────┬───────────┘
           │ 读本地数据库（解密）
┌──────────▼───────────┐        SSE /api/v1/push/messages
│ WeFlow (Electron)    │ ───────────────────────────────────┐
│ 读消息 / 导出媒体     │                                     │
│ HTTP API :5031       │                                     │
└──────────────────────┘                                     ▼
                                          ┌──────────────────────────────┐
                                          │ Akasha bridge  (panel :8766) │
                                          │ · 消息缓冲合并               │
                                          │ · OneBot v11 事件封装        │
                                          │ · wxid ↔ 显示名 映射         │
                                          │ · 图片→文字描述(image_caption)│
                                          └───────┬──────────────▲───────┘
                                   反向 WS 11229  │              │ OneBot API: send_msg
                                                  ▼              │
                                          ┌──────────────────────────────┐
                                          │ AstrBot  (panel :6185)       │
                                          │ 平台 aiocqhttp · 模型 DeepSeek│
                                          │ 主动型能力 future_task        │
                                          └──────────────────────────────┘

  发送侧（独立于上面）：
    文本：bridge ──子进程──▶ scripts\wxui.py ──Windows-MCP 点击/按键──▶ 微信窗口
                              └──读回 WeFlow /api/v1/messages 验证──▶ 只有读到才算成功
    图片：bridge ──剪贴板贴图（UIA）──▶ 微信窗口
    表情包：bridge ──HTTP 指令──▶ WeeMessenger (:9877) ──UIA──▶ 微信窗口（仅 GIF）
```

- **收消息**：WeFlow 解密微信本地库 → SSE 推给 bridge（无注入、无协议破解）。
- **发文字**：默认走 `scripts\wxui.py`。原因见下面「十、为什么文本不用 WeeMessenger」——微信 4.x 的 `Ctrl+F → 粘贴 → 回车` 会选中下拉第一行「搜索网络结果」，消息静默丢失。`wxui.py` 改用「OCR 按名字定位会话行 → 点开聊天 → 确认微信窗口真的在前台 → 粘贴 → 回车 → 读数据库回验」。
- 收发分离的好处：读消息用「数据库读取」，发消息用「UI 自动化」，两边都可以单独替换、单独关停。

## 二、目录结构

```
wechat-ai-stack\
├─ weflow\                         消息读取端（用户自装的 WeFlow v6.3.2，C:\Program Files\WeFlow）
├─ weemessenger\WeeMessenger-main\ 表情包/语音发送端（Python + UIA，.venv 已装好依赖）
├─ akasha-bridge\                  中间桥接（Python，.venv 已建；config.json 是主配置）
│   ├─ config.json                 ★ 主配置
│   ├─ weemessenger_sender.py      发送后端（白名单/限频/dry_run 护栏都在这里）
│   ├─ wxui_sender.py              ★ 文本发送后端（转调 scripts\wxui.py，默认用它）
│   ├─ senders.py                  发送后端工厂（wxui | weemessenger | uia）
│   ├─ uia_sender.py               原版 UIA 发送（bridge 自己按键）
│   ├─ web_panel.py                控制面板 + /send /send_image /send_sticker
│   └─ bridge.log                  运行日志
├─ astrbot\                        AI 大脑（AstrBot 4.28.2，.venv）
│   └─ data\cmd_config.json        平台 / 提供方 / 能力配置
├─ docs\                           手册与验证报告
├─ logs\                           各组件启动日志、pids.json、windows-mcp 截图/OCR
├─ secrets\                        access token 等本机凭据（不要提交/外发）
└─ scripts\
    ├─ preflight.ps1               一键自检（先跑这个）
    ├─ start-all.ps1               按顺序启动
    ├─ stop-all.ps1                停止
    ├─ wmcp.py                     Windows-MCP stdio 客户端（点击/截图/键盘）
    ├─ ocr.py                      Windows OCR（按名字找会话行）
    ├─ wxui.py                     ★ 已验证的文本发送器（wxui-config.json 校准）
    ├─ wxui-config.json            窗口几何 + 校验参数
    ├─ weflow-start.ps1            单独启动 WeFlow
    └─ astrbot-start.ps1           单独启动 AstrBot
```

## 三、功能对照表（你要的那几项）

| 需求 | 状态 | 实现方式 |
|---|---|---|
| 被动回复 | ✅ 已打通 | 微信 → WeFlow SSE → bridge → AstrBot → `send_msg` → `wxui.py`（回读验证） |
| 主动聊天 | ✅ 可用 | AstrBot「主动型能力 / 未来任务」定时触发；或 bridge 面板「主动发送」框（走同一条验证过的发送链路） |
| 发文字 | ✅ 已实测送达 | `wxui.py`：OCR 定位会话行 → 点开 → 粘贴 → 回车 → WeFlow 读回（实测 `localId=14`） |
| 发图片 | ✅ | bridge 剪贴板贴图（`weemessenger_image_mode: uia`，png/jpg/gif 无损） |
| 发表情包 | ⚠ 部分 | WeeMessenger `send_sticker` 只吃 **GIF（base64）**；微信「收藏表情」那种真表情包需要 Hook 层，本链路不支持 |
| 收图片 / 文件 / 视频 | ✅ | WeFlow SSE + 媒体导出（`/api/v1/media/...`） |
| 图片理解 | ✅ | bridge `image_caption_*` 走 OpenAI 兼容端点（现配 `https://api.deepseek.com/` + `deepseek-flash`，实测能描述真实聊天图片）。⚠️ 该模型是「边想边答」型：`max_tokens` 太小会被思考文本吃光，`content` 返回空串 → 日志表现为「表情包图片描述失败，保留原文」；已给到 1500 并在 content 为空时退回思考文本（见 `bridge_core.py` 的 `caption_image_via_ollama`） |
| 绘图 | ⚠ 待提供方 | 需在 AstrBot 装绘图插件并配图像 API（本机没有图像 API key）；也可以把绘图请求转发给已有的图像服务 |
| 群聊回复 | ✅ 可配 | bridge `group_reply_mode`: `mention`(仅@) / `all` / `batch` |
| 语音 | 只收 | 发语音需要装 VB-Cable 虚拟声卡（WeeMessenger 支持，未配） |

## 四、启动 / 停止（一键）

**一条命令启动全部**（微信 → WeFlow → AstrBot → bridge：启动顺序、端口等待、密钥注入、日志落盘都处理好了）：

```powershell
pwsh -File <REPO_ROOT>\scripts\start-all.ps1
```

常用变体：

| 场景 | 命令 |
| --- | --- |
| WeFlow 起不来（5031 不监听、日志报 `-105`） | `pwsh -File scripts\start-all.ps1 -FixWeFlowAnchor` |
| 微信已经开着 | `pwsh -File scripts\start-all.ps1 -SkipWeChat` |
| 只想补 bridge | `pwsh -File scripts\start-all.ps1 -SkipWeChat -SkipWeFlow -SkipAstrBot` |
| 启动后不做端口自检 | `pwsh -File scripts\start-all.ps1 -NoPreflight` |
| 停止（默认不动微信和 WeFlow） | `pwsh -File scripts\stop-all.ps1` |
| 只看端口 / 健康 | `pwsh -File scripts\preflight.ps1` |

脚本会打印每个组件的 PID 与日志路径，并写进 `logs\pids.json`。
**微信登录只能人工做**：微信窗口停在登录页（头像 + 「登录」/「切换账号」/「仅传输文件」）时点「登录」或扫码。
登录前 WeFlow 读到的是上一次的旧数据库，而 `wxui` 发送必然失败
（日志：`could not locate target row by OCR`——登录页根本没有会话列表行）。

> ⚠️ **鼠标被抢的两个来源**
> 1. WeeMessenger 的「鼠标漫游」（`MouseWanderer`）默认每 10–30 秒主动移动真实鼠标 1–3 次。
>    已在 `weemessenger\WeeMessenger-main\config.yaml` 设为 `wanderer.enabled: false`；
>    发文字/图片都不依赖它，平时**不用常开**，只有要发 GIF 表情时才手动启动。
> 2. 任何机器人发送（被动回复、主动消息、`future_task` 到点）都靠 UI 自动化：点会话行 → 粘贴 → 回车，
>    那一瞬间会占用鼠标与键盘约 5–15 秒。请在不用电脑的时段排主动消息。
>    **你抢了键鼠也不会丢消息**：windows-mcp 会把桌面交还给你，脚本等到你空闲约 10 秒再发；
>    超过 `session_wait_s` 就先入队，恢复后自动补发（见第八节「发送失败之后会发生什么」）。

面板地址：
- bridge 控制面板 <http://127.0.0.1:8766>（启停、暂停、群聊模式、主动发送、配置编辑）
- AstrBot 面板 <http://127.0.0.1:6185>（对话、提供方、插件、能力）
- WeFlow 主窗口（API 服务开关、Access Token）

## 五、人工步骤（就这四件，机器做不了）

1. **微信**：只登录**小号**；保持窗口打开且不被最小化到托盘（发送靠前台窗口操作）。
   - 窗口别缩得太小：`wxui.py` 用 OCR 在左侧会话列表里按名字找行，会话区被压缩到看不见时会找不到。
   - 真发消息那几秒**不要动鼠标键盘**，否则可能把内容敲进别的窗口（脚本会自检前台窗口，不是微信就拒绝发）。
2. **WeFlow**：主窗口 →「设置 → API 服务 → 启动服务」（并打开「主动推送」）；把 **Access Token** 复制到
   `akasha-bridge\config.json` 的 `access_token` 字段（或写进 `secrets\weflow-token.txt`，两者等价，前者即生效）。
3. **bridge 配置**（`akasha-bridge\config.json`，改完重启 bridge）：
   - `bot_nicknames`：填**小号自己的昵称**（本机是 `用户E`）。⚠ 千万别填成大号/好友的名字——bridge 用它
     判断「这条消息是不是我自己发的」，填错会把对方发来的消息当自问自答丢掉（实测踩过）。
   - `bot_wxid`：小号自己的 wxid（本机是 `wxid_BOTACCOUNT`）。
   - `send_whitelist`：**强烈建议填**，只允许给这些昵称/群名发消息；留空 = 不限制对象。
   - `send_dry_run`：首次联调保持 `true`（只记日志不真发），确认 AI 回复内容没问题后再改 `false`。
4. **AstrBot 面板**：确认平台 `aiocqhttp`（11229）与 DeepSeek 提供方在跑；打开「主动型能力」（提供 `future_task` 工具）。

## 六、主动聊天怎么配

三种方式，按需选：

1. **让 AI 自己决定什么时候找你**（推荐）
   AstrBot 面板 →「能力 → 主动型能力」启用 → AI 可以用 `future_task` 工具给自己排一个未来任务；
   「扩展功能 → 未来任务」里能看到待执行列表（Cron 五段式）。
   ⚠ 已知坑：AstrBot `manager.py` 里 `misfire_grace_time` 硬编码 **30 秒**，如果任务里要调用外部命令（比如往后 bash/HTTP 发送），一旦超时就**静默丢任务**；升级 AstrBot 会覆盖你的修改。
2. **面板手动发**：bridge 面板「主动发送 · 目标/内容 → 发送」（同样经过白名单/限频/dry_run）。
3. **定时脚本**：走 bridge 的 HTTP 接口（同一个护栏 + 同一条验证过的文本通道）：
   ```powershell
   Invoke-RestMethod -Uri http://127.0.0.1:8766/send -Method POST -ContentType application/json `
     -Body '{"target":"张三","text":"早上好"}'
   ```
   注意 `target` 必须是**微信里显示的名字**（`wxui.py` 靠 OCR 匹配这个名字），重名会发错人；
   给服务号/官方号（微信团队、腾讯新闻等）发消息前先确认它在会话列表里，否则会返回
   `target not found in WeFlow session list / contacts`。
   ⚠ 别再用 `POST http://127.0.0.1:9877/` 发文字：WeeMessenger 的 `Ctrl+F → 粘贴 → 回车` 在微信 4.x 会
   命中下拉第一行「搜索网络结果」，消息静默丢失（实测过，日志显示成功、数据库里没有）。

## 七、安全红线（务必看）

- **封号风险是真实存在的**。本链路读消息走数据库解密、发消息走 UI 自动化，都属于「非官方自动化」。请务必：
  - 只用**小号**，不要用主号；不要让机器人替你在陌生群/朋友圈发言。
  - `send_whitelist` 填死、`send_min_interval_contact/global` 保持 ≥1~2 秒、群聊先用 `mention` 模式跑两周。
  - 每天看一眼 `bridge.log` 里有没有大量「发送失败 / 被拦截」。
- **微信不要自动升级**。WeFlow 依赖的 `wx_key.dll` 与微信版本强相关；微信一升级，读消息可能直接失效，请到微信设置里关掉自动更新，并给 `weflow\WeFlow-main\resources\` 里的原生二进制留个哈希备份。
- **两个自动化程序不要同时操作微信窗口**。WeeMessenger 和 bridge 的 UiaSender 都能按键；`weemessenger_image_mode: uia` 时图片由 bridge 发（会先等 `ui_handoff_wait` 秒错开）。想彻底单一驱动就把图片模式改成 `sticker`（只有 GIF）。
- **发送期间不要抢鼠标键盘**：UIA 靠剪贴板 + 焦点，你手动切窗口可能把消息发到别人那里。
- `secrets\` 与 `config.json` 里的 token 是本机凭据，不要贴到聊天/仓库里。

## 八、排错

| 现象 | 原因 / 处理 |
|---|---|
| bridge 日志 `❌ 未配置 access_token` | WeFlow 的 Access Token 没填进 `config.json` |
| bridge 日志 `[OB11] 无法连接 AstrBot ... 远程计算机拒绝网络连接` | AstrBot 没起或没监听 11229（`scripts\astrbot-start.ps1`） |
| 面板「WeFlow 未连接」 | WeFlow 没起 / 没开「主动推送」/ token 错 |
| 发了没反应，日志 `[WeMessenger] 无法连接指令服务` | WeeMessenger 没起（9877）或 `config.yaml` 里 `http.enabled` 不是 `true` |
| 日志 `[WeMessenger][dry_run] 不实际发送` | 正常，`send_dry_run: true`；确认后改 `false` |
| 日志 `目标 X 不在白名单，已拦截` | 按需把昵称/群名加进 `send_whitelist` |
| 消息发到了错误的群 | `target` 是显示名，重名会串；群里同名/改过名的对象请用唯一备注名 |
| 收到图片只显示 `[图片]` | 没有视觉提供方：装 Ollama + 视觉模型，或配 `image_caption_provider: openai` 的兼容端点 |
| WeFlow 报连不上数据库 | 微信没开 / 版本太新（`wx_key.dll` 失效）/ 没在 WeFlow 里完成数据库连接 |
| WeFlow 启动日志 `[bootstrap] native runtime policy mismatch value=-105`，5031 不监听，bridge 报 `WeFlow 自检返回 HTTP 500` / `❌ 无法连接 WeFlow` | **不是「时间到期」**。原生 DLL 的 `wcdb_runtime_policy_version()` 必须返回魔数 `1464206081`，`-105` 的含义是「离线防回拨状态损坏或无法安全保存」——实测是 `%LOCALAPPDATA%\WeFlow\Runtime\anchor-v7-*.bin` 被非正常关机写成了**全 0**，与完好的 `State\native-anchor-v7-*.bin` 配不上。修法：`pwsh -File scripts\weflow-anchor-check.ps1 -Fix`（停 WeFlow → 备份 → 移走全 0 文件 → 重启，DLL 会重建 anchor）。详见下文小节 |
| `[wxui] ❌ ... foreground window is not WeChat; refusing to type blind` | 发送期间微信被切走了（你点了别的窗口/有弹窗抢焦点）。关掉抢焦点的窗口，再发一次 |
| `[wxui] ❌ could not locate target row by OCR` | 会话列表里没有这个名字，或窗口太小/被滚动走了。把微信窗口放大、让目标出现在列表里 |
| 群/联系人明明存在，却连续 `could not locate target row by OCR` 或 `open chat header does not show the target` | OCR 只能在**当前可见**的会话行里找，不滚动列表。让该会话有新消息（会顶到列表顶部）或在微信里先把它拖到可见位置；消息已进重发队列，成功前会一直补发 |
| `[wxui] ❌ target not found in WeFlow session list / contacts` | 目标不在 WeFlow 的会话/联系人里（多见于从未聊过的服务号）。先在微信里打开一次该会话 |
| `[wxui] ❌ ... chat window did not take focus` | 微信聊天窗没抢到前台，通常是别的窗口置顶。关掉置顶窗口再试 |
| `[wxui] ❌ 未能验证送达 ... 原因=wechat session unavailable: screen not capturable (RDP disconnected or workstation locked)` | RDP 断了或屏幕锁了。**消息不会丢**：已进重发队列，会话恢复后自动补发 |
| `... 原因=user is using the desktop (control=user, ~Ns)` | 你正在动键鼠。脚本会等你空闲约 10 秒，超过 `session_wait_s`（默认 60s）就先入队稍后补发 |
| `[wxui] ⏳ 已加入重发队列 X → Y` | 本轮没发成功（原因见同一行）。后台每 20 秒重试，最多 5 次；重发前先回读，送到过就不再发 |
| `[wxui] ✅ 重发队列：X 其实已送达（localId=…），丢弃不重发` | 上一次其实发出去了、只是回读晚了。这条被丢弃，不会重复打扰对方 |
| `[wxui] ❌ 重发放弃（尝试 5 次…）` | 重试耗尽，需要人工看一眼：窗口是否被最小化、RDP 是否恢复、目标名是否还在会话列表里 |
| bridge 日志 `⚠ 文字未验证送达（已交重发队列，稍后自动补发）` | 上游（AstrBot）那边这条不算成功；旧版本这里会照样打印「文字已发送至」，属于谎报，已修 |
| 连着发两条，第二条没有回（日志 `[主链] 已记录被动未回复: reason=重复私聊事件被忽略` / `[事件分发] 用户消息防抖拦截`） | 旧版本桥接没给 OneBot 事件带 `message_id`，AstrBot 侧把它记成字符串 `"None"`，于是**60 秒内同一私聊的所有消息共用一个键**，第二条起被当重复丢掉。已修：`ob_protocol.onebot_message_id()` 用 WeFlow 推送里的 `rawid` 生成唯一 id，`bridge_core` 按推送顺序记下每条 rawid。改完要重启 bridge |
| 想让回复分多条发，实际只有一条 | `astrbot\data\cmd_config.json` 的 `platform_settings.segmented_reply.regex` 必须**包含 `\n`**：`.*?[。？！~…\n]+|.+$`。旧 regex 对「一行一句、句尾没有句号」的回复只会切出 1 段。注意 `words_count_threshold`（默认 150）是**上限**：超过就不分段 |
| 分段太多，一条回复拖很久 | 每段 = 一次 UI 自动化发送（7–15 秒，期间占鼠标）。桥接侧在 `akasha-bridge\ob_protocol.py` 用 `SEGMENT_MAX_SENDS`（默认 4 段）封顶，多出来的并进最后一段 |
| 点会话列表**最上面那行**点歪、点到聊天区 / 点开对话会弹出独立窗口 | 两个原因：①截图区域是按 2026-10-14 的窗口尺寸量的绝对像素，窗口变小（或微信没登录、窗口只有 514px 宽）时区域伸出窗口右边 46px，OCR 会读到微信以外的文字，于是点到名单外；②对**已经打开**的会话行再点一次就是双击，微信会把对话弹成单独窗口。已修：所有截图区域用 `clamp_region()` 夹在窗口内；点击点改用 OCR 到的**会话名字框**（`row_click_x_bias_px`/`row_click_y_bias_px`，默认 12/6）而不是固定偏移；目标会话已经打开时**直接跳过点击**；重复点击受 `max_row_clicks`(2) + `repeat_click_min_interval_s`(1.6) + `after_click_wait`(1.4) 限制 |

### 发送失败之后会发生什么（RDP 断连 / 被抢键鼠）

一条消息的恢复链（`scripts\wxui.py` + `akasha-bridge\wxui_sender.py`）：

1. `wxui.py` 单次调用内部最多尝试 `send_attempts`（默认 3）轮。每轮：等会话可用（窗口没被最小化、
   截屏拿得到、**桌面控制权在 AI 手里**）→ 点开会话 → **先清空输入框** → 粘贴 → 回车 → 回读数据库。
   第 2 轮起先做一次只读回读，发现其实已送达就判成功（`stage=precheck_delivered`），**绝不重复发**。
   清空输入框是为了防「上一条被打断、文本残留在输入框里，下一条变成 `旧文本新文本`」。
2. 3 轮都不行、且原因可重试（RDP 断连、锁屏、窗口最小化、抢不到焦点、OCR 找不到会话行、桌面被用户接管），
   bridge 把消息写进 `akasha-bridge\wxui_pending.jsonl`；后台线程每 `wxui_queue_interval`（默认 20 秒）
   重试一轮，最多 `wxui_queue_max_attempts`（默认 5）次。**重发前一律先回读**：已送达的就丢弃。
3. 图片入队前会复制一份到 `akasha-bridge\wxui_queue_assets\`（AstrBot 传来的图片是临时文件，发完就被删，
   不复制的话队列重发会指向一个不存在的路径）。

配置：`scripts\wxui-config.json` 的 `send_attempts` / `retry_wait_s` / `session_wait_s` /
`session_poll_s` / `precheck_timeout_s` / `clear_input_box`；`akasha-bridge\config.json` 的
`wxui_queue_interval` / `wxui_queue_max_attempts` / `wxui_retry_queue`。

> 桌面控制权：windows-mcp 自带「人 / AI」抢占——你一碰键鼠，它立刻把桌面交还给你，并在你空闲约 10 秒前
> 拒绝一切输入注入。`wxui.py` 会等到控制权回到 AI 再动手，所以「发送时你动了鼠标」的结果从
> 「把内容敲进别的窗口」变成「等一会儿、或稍后自动补发」。

自检脚本（都在 `scripts\` 下，只对**文件传输助手**发消息）：

```powershell
python scripts\wxui.py session            # 现在能不能发（含 control_state：ready/ai/user）
python scripts\wxui.py info               # 窗口 / 矩形 / 会话可用性
python scripts\test_wxui_recovery.py      # A–E：正常发、清残留、回读失败后重发不重复、会话探测、队列丢弃+补发
python scripts\test_wxui_image_queue.py   # 图片入队后副本仍在、丢弃时清理
python scripts\test_live_queue_inject.py  # 往**正在运行的 bridge** 队列里塞 2 条：一条已送达（应丢弃）、一条未送达（应补发）
python scripts\test_meme_tool_flow.py     # 表情包链路（只读、不发微信）：真人设 + 插件提示词 → 模型是否调 search_memes 并回 &&meme:xxx&&
```

### 分段回复 /「连发只回一条」（2026-10-14 修）

两个独立问题，都在桥接这一层：

1. **连发被吞**：桥接推给 AstrBot 的事件没有 `message_id`，AstrBot 的 aiocqhttp 适配器写成 `"None"`，
   私聊去重（`astrbot_plugin_private_companion` 的 `_is_duplicate_inbound_message`，窗口 `max(60, 防抖秒数)`）
   就把 60 秒内所有消息当成同一条。现在 `ob_protocol.onebot_message_id(rawid)` 把 WeFlow 的 `rawid`
   折成 OneBot 的 int32 id，`bridge_core.add_to_buffer` 逐条记 rawid，`process_sender` 取最后一条非空 rawid。
2. **分段没生效**：`segmented_reply.regex` 改成带 `\n` 的版本后，模型自己换行的短句会变成**多条真实微信消息**
   （每段一次 UI 自动化发送）。桥接再按 `SEGMENT_MAX_SENDS` 封顶。

改完生效顺序：改 `cmd_config.json` → 重启 AstrBot；改 `ob_protocol.py`/`bridge_core.py` → 重启 bridge。

日志位置：`logs\bridge-*.log`、`logs\weemessenger-*.log`、`logs\astrbot-*.log`、`logs\weflow-*.log`、`logs\wmcp\*.png`（每次发送的截图）、`akasha-bridge\bridge.log`。

### WeFlow 启动即 -105（离线防回拨状态损坏，2026-10-14 修）

现象：开机后 WeFlow 一直起不来，`%APPDATA%\weflow\logs\wcdb.log` 反复打印

```
[bootstrap] koffi.load ok
[bootstrap] native runtime policy mismatch value=-105
```

5031 不监听 → bridge 每 30 秒 `❌ 无法连接 WeFlow（30s 后重试）`；微信收发全停。

机制（从 `C:\Program Files\WeFlow\resources\app.asar` 里读出来的原逻辑）：

```js
let e = this.lib.func('int32 wcdb_runtime_policy_version()'), t = Number(e());
if (t !== 1464206081) return F = this.formatInitProtectionError(t < 0 ? t : -103), ...  // 拒绝初始化
```

`1464206081` 是原生 DLL 必须回给 JS 的魔数；错误码表里 `-102` 才是时间回拨，`-105` = 离线防回拨状态损坏/无法安全保存
（文案里那句「请检查当前用户目录权限」是误导，实测三个目录 ACL 完全正常）。

实测根因：`%LOCALAPPDATA%\WeFlow\` 下有 3 个保护状态文件，非正常关机（本次是 22:16 那次断电/重启）
把 `Runtime\anchor-v7-<设备hash>.bin` **写成了 310 字节全 0**，而 `State\native-anchor-v7-<设备hash>.bin` 完好
（两者结构相同，都以 `01 00 00 00 d0 8c 9d df 01 15 d1 11 …` 开头 + 尾部 HMAC）。DLL 一看这对状态对不上就返回 -105。

修法与验证：

```powershell
pwsh -File scripts\weflow-anchor-check.ps1        # 只体检：列出三个文件、标出全 0 的、报告 policy/5031 状态
pwsh -File scripts\weflow-anchor-check.ps1 -Fix   # 修复：停 WeFlow → 备份到 logs\weflow-state-backup-<时间戳>\ → 移走全 0 文件 → 重启
```

修好后的日志与状态（2026-10-14 23:28 实测）：

```
[bootstrap] wcdb_authenticate begin port=50676
[bootstrap] native protection ok
open sessionDb=…\xwechat_files\wxid_BOTACCOUNT_e398\db_storage\session\session.db
open ok handle=1
```

5031 恢复监听，`/api/v1/sessions`、`/api/v1/contacts` 返回 200，bridge 自行重连（`✅ WeFlow API 正常` → `✅ 已连接到 WeFlow 推送`）。

> 两个坑：①`Start-Process WeFlow.exe` 在自动化上下文里会**立刻退出**（单实例判定），要用
> `Start-Process explorer.exe -ArgumentList '"C:\Program Files\WeFlow\WeFlow.exe"'` 走交互式 shell 才起得来，
> 修复脚本已经这么写；②移走 anchor 只动了**损坏的那一个**，完好的 State anchor 保留，防回拨保护仍有效。
> 若只移 Runtime anchor 仍是 -105，再把 `State\native-anchor-v7-*.bin` 一起移走让 DLL 全新初始化。

排查用的只读探针（都在 `scripts\` 下）：`probe_weflow_policy.py`（在 app.asar 里搜 policy 检查那段）、
`probe_weflow_policy2.py js|dll`、`probe_weflow_anchor.py js|dll`。

## 九、验收清单

- [x] `scripts\preflight.ps1` 里 5 个端口全 OK（5031 WeFlow / 9877 WeeMessenger / 11229 AstrBot OB11 / 6185 AstrBot 面板 / 8766 bridge）
- [x] bridge 面板显示：桥接「运行中」、AstrBot「已连接」、WeFlow「已连接」、发送模式「WxuiSender」
- [x] 文本真实送达并回读验证（`[wxui] ✅ 已验证送达 文件传输助手（localId=14, createTime=1791962550）`）
- [x] 用另一个微信号给小号发一句话，小号能自动回复（实测 2026-10-14 17:45：用户C发「你好」→ bridge SSE 收到 →
      AstrBot 用 DeepSeek 生成回复 → `wxui` 发送并回读验证 `localId=101, createTime=1791971151`）
- [ ] 面板「主动发送」能发出去（`send_dry_run` 现在已是 `false`，点一次即真实发送）
- [ ] 连续两条消息都被处理（修完 `message_id` 后待实测）
- [ ] 回复按分段发出多条微信消息（改完 `segmented_reply.regex` 后待实测）
- [ ] 用户要表情时收到一张表情包图片（`test_meme_tool_flow.py` 已验证模型会调 `search_memes` 并回 `&&meme:xxx&&`，链路待实测）
- [x] 发送失败恢复（2026-10-14 实测）：`test_wxui_recovery.py` A–E 全绿（正常发 / 清残留 / 回读失败后重发且不重复 /
      会话探测 / 队列丢弃已送达+补发未送达）；`test_live_queue_inject.py` 在**运行中的 bridge** 上验证
      队列线程丢弃一条已送达、补发一条未送达（`localId=38`）
- [ ] 发一张图片，AI 能描述内容（需视觉提供方）
- [ ] 发一个 GIF 表情包（`/send_sticker` 或 `weemessenger_image_mode: sticker`）

## 十、为什么文本不用 WeeMessenger（实测结论）

两条老路（WeeMessenger 的 UIA 发送、bridge 自带的 `UiaSender`）都用同一种套路：
`Ctrl+F` 打开搜索 → 粘贴联系人名 → 回车选中第一项。微信 4.1.x 上实测：

1. 搜索下拉的第一项是**「搜索网络结果」**（网页/小程序），回车会打开一个网页搜索结果页，
   聊天区被 `WeChatAppEx` 盖上，消息**静默丢失**；下拉里真正的会话行在**最后一行**（绿色高亮）。
2. 换成「`Ctrl+F` → 粘贴 → `↓` → 回车」也不安全：一旦焦点不在搜索框，粘贴与回车会把**搜索词当成消息**
   发进当前聊天（实测把「微信团队」四个字发进了文件传输助手）。
3. 所以日志里的「完整发送完成」**不代表送达**。判断送达只有一种可靠办法：**回读微信数据库**。

`scripts\wxui.py` 现在的做法（每一步都可验证）：

1. `EnumWindows` 找「标题=微信、类名以 Qt 开头」的主窗口，用 `AttachThreadInput + SetForegroundWindow`
   把它真的拉到前台，并**确认它确实是前台窗口**（后台进程调用 `SetForegroundWindow` 会被 Windows 拒绝）。
2. 截左侧会话列表 → Windows OCR 识别每一行 → 按**名字**（不是索引，WeFlow 的会话表缺服务号）定位目标行。
3. 点击该行打开聊天 → 找到标题等于目标名的聊天窗口并聚焦 → 再确认前台窗口类名以 `Qt` 开头，
   **否则拒绝对键盘动手**（宁可失败也不把内容敲进别的程序）。
4. 剪贴板写入 → `Ctrl+V` → 回车 → 轮询 `/api/v1/messages` 直到读到 `isSend=1` 且内容一致；
   读不到就返回失败，bridge 也就返回失败（不会假装成功）。
5. 失败可重试时（RDP 断连 / 锁屏 / 窗口最小化 / 被抢键鼠 / OCR 找不到行）会在一次调用里重试 3 轮，
   仍不行就进 `wxui_pending.jsonl` 重发队列，后台每 20 秒重发一次、最多 5 次；每次重发前都先回读，
   确认「其实已送达」的就丢弃。**发送日志不再无条件写「已发送至」**：回读失败会写
   `⚠ 未验证送达（已交重发队列）`。

这套东西每次发送都会在 `logs\wmcp\` 留下截图，方便事后复盘。
