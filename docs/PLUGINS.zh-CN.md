# AstrBot 插件（表情包 / 情绪 / 主动）配置与验证说明

本文档记录 2026-10-14 在本机安装并调试的四个 AstrBot 插件，以及发图通路的关键修复。
主链路说明见 `docs/SETUP.zh-CN.md`。

## 一、已安装插件

| 插件 | 版本 | 来源 | 作用 | 当前状态 |
| --- | --- | --- | --- | --- |
| `meme_manager` | 5.1.0 | anka-afk（社区插件市场缓存 `downloads/astrbot_plugin_cache.json` 中 `anka/meme_manager`） | 表情包管理：资源广场、AI 智能发送、WebUI 管理页 | 已加载，注册 LLM 工具 `search_memes`，绑定 15 个 Mixin |
| `astrbot_plugin_private_companion` | 6.7.0 | `menglimi/astrbot_plugin_private_companion` | 「我会永远陪着你」类私聊陪伴：主动关怀、记忆、群陪伴 | 已加载，注册 `/陪伴`、`/陪伴群`，检测到 Proactive Chat 并启用私聊主动联动 |
| `astrbot_plugin_proactive_chat` | 1.2.6 | 随 AstrBot 生态安装 | 主动消息（Cron/随机） | 已加载（警告 `session_list` 为空，需先有会话） |
| `astrbot_plugin_affection` | 1.3.1 | `middcom/astrbot_plugin_affection` | 情绪量化：双驱情绪模型（好感度/情绪值） | 已加载 |

安装目录：`wechat-ai-stack/astrbot/data/plugins/<插件名>/`
依赖已装入 AstrBot 的 venv（`wechat-ai-stack/astrbot/.venv`）：`aiohttp`、`Pillow`、`chinese-calendar`、`lunarcalendar`、`markdown-it-py`、`tqdm`、`boto3`、`faiss-cpu` 等。

## 二、表情包资源

- 官方初始包 `official-001`（「官方表情包1号」，来源 `anka-afk/astrbot-meme-pack-official-01`）已手工安装到
  `wechat-ai-stack/astrbot/data/plugin_data/meme_manager/packs/official-001/`
  —— 400 个文件 / 67.5 MB，分类目录：`angry baka color confused cpu fool givemoney happy` 等 11 类，每类带 `previews/`。
- 安装方式（当插件市场页面不可用时）：从插件市场缓存里找包的 GitHub 地址 → 经 `https://ghfast.top/` 或
  `https://api.gitproxy.dev/github.com/<owner>/<repo>/archive/refs/heads/main.zip` 下载 → 解压（剥掉顶层目录）到上述 packs 目录 → 重启 AstrBot。
- 包索引（`memes_data.json`）由插件运行时生成，无需手工创建。

## 三、AstrBot 启动的硬前提：DEEPSEEK_API_KEY

AstrBot 的 provider 配置里两个源都是 **环境变量引用**：

```
provider_sources[0].key = ["$DEEPSEEK_API_KEY"]   # deepseek_source / deepseek-v4-pro_source
```

本机 `DEEPSEEK_API_KEY` 是 User 级持久环境变量，但**已经打开的 shell 不会自动获得它**。
若 AstrBot 从这样的 shell 里启动，日志会出现：

```
[WARN] Provider deepseek configuration key[0] references environment variable ...
[ERRO] openai.OpenAIError: Missing credentials. Please pass an `api_key` ...
```

→ LLM 直接不可用（被动回复仍会走桥接，但模型会报错）。

修复（已执行）：启动前注入，再拉起进程。

```powershell
$env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')
Start-Process -FilePath wechat-ai-stack\astrbot\.venv\Scripts\astrbot.exe -ArgumentList 'run' -WorkingDirectory wechat-ai-stack\astrbot -WindowStyle Hidden -RedirectStandardOutput wechat-ai-stack\logs\astrbot-runN.log
```

验证：重启后日志中 `references environment` / `Missing credentials` 计数为 0，且 11229/6185 端口正常监听。

## 四、插件 → 微信 的图片通路（已修复 + 已离线验证）

问题：`bridge/ob_protocol.py` 原来只认 `base64://` 或 `ASTRBOT_ATTACHMENTS`（`astrbot/data/temp`）里的文件名；
而表情包插件给出的图片是**自己插件目录下的绝对路径**，会被判为「图片文件未找到」而静默丢弃。

修复（`akasha-bridge/ob_protocol.py`，image 段分支）：先认绝对路径，再回退附件目录。

```python
raw = file_val[7:] if file_val.startswith("file://") else file_val
if os.path.isabs(raw) and os.path.exists(raw):
    img_path = raw
elif config.ASTRBOT_ATTACHMENTS:
    ...
```

离线验证脚本：`scripts/test_ob_image.py`（模拟 AstrBot 下发 `send_private_msg` + image 段）

```
PASS  absolute path :: sent=...official-001\memes\angry\0FFD...jpg bytes=13046
PASS  base64:// (17396 chars) :: contact=文件传输助手 decoded=13046 expected=13046
PASS  missing file is refused :: no image sent
PASS  empty file field is refused :: no image sent
4/4 passed
```

## 五、当前已验证 / 未验证

已验证（有证据）：
- 文本被动回复全链路：微信(用户C) → WeFlow SSE → bridge → AstrBot(LLM) → bridge → wxui → 微信，回读数据库确认（`localId=101`）。
- 主动发送文本/图片（bridge HTTP → wxui）：`localId=19`（文本）、`localId=20`（图片）、`localId=21`（**官方表情包包里的真实表情图** `official-001/memes/happy/1739433254_1.png`，type=3，从微信数据库回读确认）。
- 插件 → bridge 的图片段落两种形态（绝对路径 / base64）。
- 四个插件加载无报错；`search_memes` 工具已注册。

未验证（需要一条真实的微信来信来触发）：
- 插件真正回复一条表情包到微信（链路两端都验证过，中间的「插件实际产出 image 段」尚未被真实对话触发）。
- 情绪量化（affection）在真实对话中的数值变化。
- 主动消息（proactive_chat / private_companion）按计划自行发出。

触发方法：用大号（用户C）给小号（用户E）发一句「发个表情包」，
然后在 `wechat-ai-stack/logs/bridge-run4.log` 查 `[OB11] 图片已发送至` 与 `[wxui] ✅ 已验证送达`。

仍需提供方（当前缺失）：
- 图片理解：`config.json` 里 `image_caption_provider=ollama` + `qwen2.5vl:7b`，但本机 11434 没有 Ollama 在跑。
  **用户决定自己在插件侧接入 `deepseek-flash` 作为视觉提供方（2026-10-14），待验证。**
  两处可选接入点：
  1) AstrBot：新建/复用一个 `openai_chat_completion` 提供方（model=`deepseek-flash`，api_base=`https://api.deepseek.com/v1`），
     然后把 `provider_settings.default_image_caption_provider_id` 填成该提供方 id；
  2) bridge：`config.json` 改 `image_caption_provider=openai`、`image_caption_api_base=https://api.deepseek.com/v1`、
     `image_caption_model=deepseek-flash`、`image_caption_api_key=<key>`。
  注意 key 必须真的进到进程环境里（AstrBot 的 `$DEEPSEEK_API_KEY` 引用、或 bridge 启动时的环境变量）。
- 绘图：没有任何图像生成 API（环境里只有 DEEPSEEK_API_KEY）。

## 六、配图意愿（表情包发图频率）怎么调

开关与文案都在插件自己的配置里（**不是** AstrBot 面板的插件配置页也能改，改完必须重启 AstrBot）：

- 文件：`wechat-ai-stack/astrbot/data/config/meme_manager_config.json`
- `generation.prompt.quantity_guidance_enabled`：2026-10-14 由 `false` 改为 **`true`**（改前这段不注入提示词，所以机器人几乎不主动配图）。
- `generation.prompt.quantity_guidance`：注入给 LLM 的配图节奏段，当前文案大意是
  「主动、频繁配表情，大约每 2–3 条回复就有 1 条配图；玩笑 / 互怼 / 撒娇 / 关心 / 鼓励 / 庆祝 / 共情 / 被逗笑 / 早晚问候，或对方情绪明显时优先配一张；用户明确要表情、要图时这一条必配（确实选不到合适候选才例外）；话题或情绪一变就继续配；需要时 1 张，情绪强烈或对方要求时 2 张」。
- 相关但不常动的键：`semantic.enabled=true`、`semantic.top_k=10`、`semantic.min_score=0.1`（候选来自 551 张已语义化索引的 `atri-meme-pack`）、`generation.emotion.probability=100`。
- 让改动生效：`scripts\astrbot-start.ps1`（插件在启动时读 `self.config`，热重载不一定会重读）。
- 自测（只读、不发微信）：`wechat-ai-stack/scripts/test_meme_tool_flow.py`。它会真读上面配置里的 `quantity_guidance` 段，把真人设 + 插件真实系统提示词 + 真实 `search_memes` 检索 + `deepseek-flash` 工具循环跑一遍，并逐句打印「是否主动搜索 / 最终 `&&meme:xxx&&` 标记」。2026-10-14 实测默认 4 句 **4/4 都配了图**（连「嗯，知道了」都回了个困惑表情）；嫌太频繁就把 `quantity_guidance` 里的「每 2–3 条」改回「每 4 条」，或把 `quantity_guidance_enabled` 关掉。
- 微信里是否真的出图，看 `wechat-ai-stack/logs/bridge.log` 的 `[OB11] 图片已发送至` 与 `[wxui] ✅ 已验证送达`；AstrBot 侧确认工具已注册看日志 `Added llm tool: search_memes`。

## 七、三个「表情包 / 动图」故障的根因与修复（2026-10-15）

排查依据：`logs/astrbot-20261014-223840.log`（00:03 前后）、`logs/astrbot-20261014-220342.log`、
`akasha-bridge/bridge.log`，以及资源包在 `astrbot/data/plugin_data/meme_manager/packs/*` 的实际索引状态。

### 7.1 「Function calling is not enabled」WARN —— 不是故障，也跟本问题无关

- 出处：`astrbot/.venv/Lib/site-packages/astrbot/core/provider/sources/openai_source.py:1152-1170`。
  异常重试分支里做**字符串匹配**（`"Function calling is not enabled" in str(e)`，或异常文本同时含 `tool` 与 `support`），
  命中就这一轮 `payloads.pop("tools", None)` + `func_tool=None`，**下一轮照常带工具**。
- 全量日志只出现一次：`astrbot-20261014-223840.log:1614`（00:03:43.971），同轮即被 caption 的 json 回退接住；
  同一份日志 23:40:52 还有一次**成功的** `search_memes` 工具调用（`:1127` 使用工具 → `:1186` 已选择语义表情）。
- 结论：不要把它当成「表情发不出来」的原因，也不要据此关掉工具调用。真要追根因，等复现时把提供方返回的原始异常文本落盘。

### 7.2 表情发不出来 —— 两条独立闸门（都已修）

1. **默认资源包指错**：`astrbot/data/plugin_data/meme_manager/selection_rules.json` 的 `scope=default` 规则原本指向
   `blue-fat-fish-meme-pack`（该包只做完 caption，没有 FAISS 索引，检索必然为空）→ 已改为
   **`atri-meme-pack`**（551 张，索引 `index-a9786577e9ca4619a45ca52adf680105.faiss`，1024 维，`phase=finished`）。
2. **候选白名单按轮失效**：`backend/semantic/query.py:124-157` 的 `validate_selected_id` 有五道校验
   （ID 前缀 / 本轮候选集 / 候选条目存在 / 分类非 needs_review / 文件 sha256 一致），而候选集只由**本轮**
   `search_memes` 写入（`query.py:107-121`）。模型跨轮复用上一轮 ID 时必然被拒 —— 日志里同一个
   `meme:07f1c15c34d6` 被连拒 3 次（`:775`、`:786`、`:1008`），且那几轮根本没调用 `search_memes`。
   → `mixins/event_handlers.py` 新增 `_recover_rejected_semantic_ids()` + `_semantic_fallback_categories()`：
   第一级用同一查询词重新检索、改用本轮候选；第二级回退分类随机选图；`validate_selected_id` 仍是唯一放行口。

### 7.3 GIF 发出去不动 —— 两处真问题（都已修），通道无需更换

1. **剪贴板只放首帧**（主因）：`scripts/wxui.py` 原来只有 `Clipboard::SetImage`，微信粘到的是 CF_DIB，动图只剩第一帧。
   → 现在动图走 `Clipboard::SetFileDropList`（CF_HDROP 文件拖放），静态图仍走位图；文件路径改由环境变量
   `WXUI_CLIP_PATH` 传入 PowerShell，不再做字符串插值。
2. **bridge 把 GIF 写成 `.png`**：`akasha-bridge/ob_protocol.py` 原来硬编码 `suffix=".png"`，GIF 字节落盘成 `tmpXXXX.png`。
   → 新增 `_guess_image_suffix()` 按魔数嗅探（GIF87a/GIF89a→`.gif`，PNG/JPEG/BMP/WEBP 同理），
   `[OB11] 图片已解码:` 日志现在显示真实后缀。
3. **「通道不支持动图」已被实测推翻**，不需要 WeeMessenger：2026-10-15 00:55 真机实测，CF_HDROP 粘进微信后，
   微信把它当作**自定义表情**发出——WeFlow 回读为 `localType=47`、`content="[表情]"`，动画帧由微信自己处理。
   原先评估的 `weemessenger` sticker 通道（`akasha-bridge/weemessenger_sender.py:104-142`）**未启用也不需要启用**；
   当前 `config.json` 保持 `sender_backend="wxui"` + `weemessenger_image_mode="uia"`。
4. **由此暴露的新缺陷（已修）**：`verify_image` 只认 `localType==3`，把 type 47 的贴纸消息判成「没发出去」，
   于是三次重试把同一张动图发了 3 遍（实测出现 6 条重复消息）。`IMAGE_LOCAL_TYPES = (3, 47)` 之后，
   同一次发送变成 1 次尝试、10.2 秒完成（修复前是 3 次尝试、82 秒后报失败）。

### 7.4 两条容易误判的日志（排错时别被带偏）

- `发送前检查 / 发送前结果为空`（`astrbot_plugin_private_companion/main.py:10895-10923`）是**记录**，不是拦截：
  该 handler 只调用 `_record_passive_no_reply()` 写一条原因，`main.py:17822-17915` 同样只记录、不修改 `result.chain`。
  它出现只说明「回复链在发送前是空的」——那是结果，不是原因。
- `没有通过筛选`：全量日志 0 命中，**不是**任何代码里的字符串，别拿它做验收关键字。对应的真实日志是
  `mixins/event_handlers.py:1184` 的 `忽略不在本轮候选中的语义图片 ID: %s`。

### 7.5 离线回归测试（不需要登录微信）

```
& wechat-ai-stack\akasha-bridge\.venv\Scripts\python.exe wechat-ai-stack\tests\test_image_passthrough.py
```

覆盖：图片后缀嗅探（7 个魔数用例 + base64 落盘）、剪贴板动图→CF_HDROP / 静态图→位图、
回读校验 `localType 3/47` + appmsg 表情(8)/文件(6) 按 md5 认领且无关消息不误判、语义回退接线 + 默认资源包。
2026-10-15 实测 **4/4 通过**。

### 7.6 动图的验收边界（诚实说明）

- WeFlow 回读（`verify_image` / `classify_outgoing`，`scripts/wxui.py`）能证明「**我这一张**图发出去了」：
  `localType in (3, 47)` 与 `mediaType in (image, emoji, sticker)`；appmsg 形态则必须按 **md5** 认
  （表情 `appmsg type 8` → 内容里的 `emoticonmd5`；文件 `appmsg type 6` → 内容里的 `md5`），文件名只作兜底。
  只靠 `localType` 或文件名都会误判（前者漏认表情/文件，后者会把别人转发的同名文件算成自己送达）。
- 但它**证明不了动画帧**——DB 里只有「类型 + 时间」，没有帧数。所以「动画是否真的在播」仍必须在微信端目视确认。
- `send_image` 返回 JSON 里的 `image_animated`、`image_md5`、`clipboard_mode`（`file_drop` / `bitmap`）、
  `delivered_as`（`image` / `sticker` / `emoticon` / `file`）、`verify_note`、`attempt_log`
  可直接判断走了哪条通路、微信落成什么形态、重试了几次。
- **教训**：验收口径比发送实现更容易出错。这次的 6 条重复消息不是发送失败，而是校验漏认一种消息类型
  （type 47、type 8）触发了重试。以后凡是「发送失败」告警，先确认是不是**误判**，再动手重发。

### 7.7 微信端把图落成什么形态、显示多大（2026-10-15 实测）

链路**不缩放、不重编码**：三种形态在 DB 里的凭据都等于本地文件的原始字节（`totallen` = 文件字节数，
`emoticonmd5`/`md5` = 本地 md5）。**显示大小由微信按形态决定，发送侧改不了**：

| 源图 | 微信落成的形态 | DB 凭据 | 聊天里的显示 |
|---|---|---|---|
| 静态图（1 帧，如 512×512/117KB） | 图片 `localType=3` | —（图片不记 md5） | **大图** |
| 多帧且最大边 ≲1000（500×500 32 帧、850×850 22 帧） | 表情 `appmsg type 8` | `emoticonmd5` + `totallen` = 原文件 | **固定小气泡**（与源像素无关） |
| 多帧且尺寸超限（1080×1080 15 帧、1920×1080 5 帧） | 文件 `appmsg type 6` | `md5` + `totallen` = 原文件 | 文件气泡（点开大而能动） |
| 很小的图（91×102） | 自定义表情 `localType=47` | `content="[表情]"` | 小气泡 |

判定依据是**像素尺寸 + 是否多帧**，与文件字节数无关（2.78MB 的 850×850 走表情，110KB 的 1920×1080 走文件）。
对照样本留在「文件传输助手」（探针原件 `logs/wmcp/size-probe/`）。
**决定（用户，2026-10-15）**：保持现状、不动原图——动图就是表情小气泡，不为了显示更大去缩放/改格式。

