# 独立验证报告 · 微信小号 AI 机器人链路

- **验证方**：独立验证方（teammate `verifier`），不采信任何既有结论，只记录亲眼跑出的原始输出。
- **验证时间窗**：2026-10-14 17:47 – 17:52 (+08:00)
- **验证对象**：微信 4.1.15.13 ← WeFlow(5031) ─SSE→ akasha-bridge(8766) ←反向 WS─ AstrBot(11229/面板 6185) → 发送后端 `wxui`（scripts/wxui.py）→ 微信
- **本次只读**：除本文件外未修改任何代码/配置/文档，未启停任何服务。
- **凭据**：WeFlow access_token 在本文中一律写作 `***`。

## 一、结论摘要

| 判定 | 内容 |
|---|---|
| ✅ 通过 | 5 个端口/健康检查（第二次 preflight 全 OK） |
| ✅ 通过 | 出站护栏单测 `test_guards.py` 28/28 PASS，退出码 0 |
| ✅ 通过 | 被动回复链路真实闭环（用户C「你好」→ AI 回复 → 回读 `localId=101` 证实送达） |
| ⚠️ 有缺陷 | 文本发送**已送达却回报失败**（假阴性）：1 次实测送达被 bridge 记为 `ok=false` |
| ❌ 未通过 | 端到端主动发送**第二次尝试失败**（OCR 截图返回空） |
| ❌ 未通过 | 消息内容完整性：落地文本与请求文本不一致（多出全角逗号） |
| ❌ 发现严重设计缺陷 | OneBot `user_id` 跨进程不稳定（`hash()` 随机化），重启即变更 |
| ⬜ 未测 | 图片发送 / 表情包 / 语音 / 绘图 / 群聊 / 图片理解（后者已确认环境不具备） |

## 二、证据表

> 原始输出按实跑结果誊录；`***` 处为 token 打码。

### 1) 端口 / 健康检查 —— `pwsh -File wechat-ai-stack\scripts\preflight.ps1`

**第一次运行（17:47:45 起）**

```
=== 1) 进程 / 端口 ===
OK  微信 4.x (Weixin.exe)     : 运行中 PID=17028,32856,...
✗   微信 3.9 (WeChat.exe)     : 未运行
OK  WeFlow HTTP API            : 监听中 <- { "status": "ok" }
✗   WeeMessenger 指令服务          : 未监听
OK  bridge 控制面板                : 监听中 <- {"running": true, "paused": false, "send_method": "WxuiSender", ...}
OK  AstrBot 反向 WS (OneBot)     : 监听中
OK  AstrBot 面板                 : 监听中 <- <!doctype html> ...
=== 2) 发送链路护栏与其配置 ===
    后端(配置/生效) : wxui / WxuiSender
    dry_run         : False  ⚠ 真实发送已开启
    白名单          : （空 = 不限对象 ⚠）
    图片通道        : uia
    后端健康        : 健康 hwnd=1642320 rect=1300,150,2820,1350
    机器人昵称      : 用户E, 用户E
    AstrBot OB 地址 : ws://127.0.0.1:11229/ws
    图片描述        : ollama / qwen2.5vl:7b
    ⚠ 图片理解当前不可用：Ollama(11434) 没在跑，收到图片只会退化成 [图片]
```

**第二次运行（约 4 分钟后）**：`WeeMessenger 指令服务 : 监听中 <- {"status":"ok"}` —— **5 个端口全部 OK**。

**结论：通过（但有瞬态）**。9877 在第一次探测时未监听，随后自行变为监听中（进程 `WeeMessenger\...\main.py` 期间启动）。`docs/SETUP.zh-CN.md:184` 声称「5 个端口全 OK」在稳定态成立，但**并非任何时刻都成立**。

独立 TCP 复测（`Test-NetConnection`）：5031/9877/11229/6185/8766 全部 LISTENING；**11434 全时段 DOWN**（Ollama 未安装，`Get-Command ollama` 无结果，`%LOCALAPPDATA%\Programs\Ollama` 不存在）。

### 2) 出站护栏单测 —— `akasha-bridge\.venv\Scripts\python.exe scripts\test_guards.py`

```
PASS  白名单拦截非名单对象: got=False want=False
PASS  限频第一条放行: got=True want=True
PASS  限频第二条放行: got=True want=True
PASS  限频确实等待了 (>=0.55s): got=True want=True
      elapsed=0.60s
...
28 PASS / 0 FAIL
退出码 = 0
```

日志侧同时出现（预期行为）：
```
[WeMessenger] 目标 文件传输助手 不在白名单 ['允许的人']，已拦截（文本）
[WeMessenger] 目标 文件传输助手 在黑名单，已拦截（文本）
[WeMessenger][dry_run] 不实际发送 → target=文件传输助手 type=send_message 内容=hi
```

**结论：通过（但覆盖面有限）**。`test_guards.py` 只实例化 `WeMessengerSender`（日志前缀 `[WeMessenger]`），**完全没有覆盖当前生效的 `WxuiSender`**（见问题 P6）。

### 3) `/status` 与 `/senders` —— `Invoke-RestMethod http://127.0.0.1:8766/...`

```
后端(配置/生效) : wxui / WxuiSender
dry_run         : False          ← 真实发送已开启
白名单          : （空 = 不限对象）
图片通道        : uia
后端健康        : 健康  hwnd=1642320  rect=1300,150,2820,1350
```

**结论：通过**。`/senders` 中发送后端的健康信息统一放在 `weemessenger` 键下（`wxui`/`weemessenger` 共用该槽位），这与 `preflight.ps1:51` 的注释一致，属已知设计。白名单为空 = **不限对象**，是当前最大的运行期风险敞口。

### 4) 端到端主动发送 —— `POST http://127.0.0.1:8766/send`

**第一次 POST**（body `{"target":"文件传输助手","text":"[独立验证]2026-10-14 17:48:10"}`）

bridge 响应：
```json
{ "ok": false, "target": "文件传输助手", "dry_run": false }
```
bridge 日志：
```
17:48:30 [ERROR] [wxui] ❌ 未能验证送达 文件传输助手 | 原因=message not read back from WeChat DB within timeout | 行号=1 | 微信/WeFlow 是否在线？| stdout={ "target": "文件传输助手", "talker": "filehelper", "window_rect": [1300,150,2820,1350], "ocr": {"png": "...\logs\wmcp\wxui-1791971293.png", ...} }
17:48:30 [INFO] [Web] 主动发送(文本) → 文件传输助手 | ok=False
```

WeFlow 回读（`GET /api/v1/messages?talker=filehelper&limit=8&access_token=***`）：
```
localId=16 send=1 time=2026-10-14 17:48:55 content=[市场在]
localId=15 send=1 time=2026-10-14 17:48:17 content=[[独立验证]2026-10-14 17:48:10，]
localId=14 send=1 time=2026-10-14 15:22:30 content=[[链路自检] bridge 发送 152223]
```

**判定：内容实际上已送达（`localId=15`, `isSend=1`, createTime 17:48:17），但 bridge 报告 `ok=false` —— 假阴性。**
- 送达时间 17:48:17 早于 bridge 放弃时间 17:48:30，故这不是「发出去但还没落库」。
- 落地文本比请求文本**多一个全角逗号**。
- 机制已定位：`scripts/wxui.py:249` 用**全等比较** `fit(m.get("content")) == fit(text)`，而 `wxui.py:142` 的 `fit()` 只做「去空白 + 小写」：
  `fit("[独立验证]2026-10-14 17:48:10，")` ≠ `fit("[独立验证]2026-10-14 17:48:10")` → 必然判为未送达。
- **诚实声明**：我无法 100% 确定该逗号是我方请求文本、输入法，还是并发操作引入；但当它存在时，上面的全等比较**必定**导致假阴性，这一因果链是确定的。

**第二次 POST**（同一目标）

bridge 响应：`{ "ok": false, "target": "文件传输助手", "dry_run": false }`
bridge 日志与 wxui stdout：
```
17:48:51 [ERROR] [wxui] ❌ 未能验证送达 文件传输助手 | 原因=could not locate target row by OCR | 行号=None | stdout={
  "target":"文件传输助手","talker":"filehelper","window_rect":[1300,150,2820,1350],
  "ocr":{"png":null,"engine":null,"picked_index":null},
  "ok":false,"error":"could not locate target row by OCR","ocr_candidates":[] }
```
**全库检索**（遍历全部 8 个会话，`content -like '*独立验证*'`）：
```
*** HIT talker=文件传输助手 (filehelper) localId=15 send=1 time=2026-10-14 17:48:17
（仅此 1 条；其它 7 个会话 0 命中）
```
**判定：未通过 —— 确实没有送达，bridge 的失败报告是准确的。** 根因是 OCR 侧 `png=null, engine=null, ocr_candidates=[]`：截图 `Screenshot` 未返回图像，`find_row` 直接报错，**尚未执行任何输入动作**。

### 5) 被动回复链路（端到端闭环）

bridge 日志：
```
17:45:38 [INFO] 📩 收到: 用户C → 你好
17:45:43 [INFO] 推送 1 条消息 [私|用户C]
17:45:45 [INFO] [OB11] API: send_private_msg echo={'seq': 1}
17:45:52 [INFO] [wxui] ✅ 已验证送达 用户C（localId=101, createTime=1791971151）
```

WeFlow 回读（`talker=wxid_CONTACT_A`）：
```
localId=101 send=1 time=2026-10-14 17:45:51 content=[你好！很高兴见到你 😊 ... 我是你的 AI 助手 ...]
localId=100 send=0 time=2026-10-14 17:45:37 content=[你好]
localId= 95 send=0 time=2026-10-14 08:57:54 content=[我是用户C]
```

**判定：通过**。`docs/SETUP.zh-CN.md:187-188` 的验收条目（`localId=101, createTime=1791971151`）**独立复核为真**。AI 回复内容中出现了「文档处理 / 表格处理 / PDF 操作 / 技能开发」等能力声明——这些是 AstrBot 侧生成的文本，本次**未验证**其对应工具是否真的可用。

## 三、未验证 / 不可用功能（诚实清单）

| 功能 | 状态 | 依据 |
|---|---|---|
| **图片理解** | ❌ **确认不可用** | `ollama` 未安装、11434 全时段 DOWN；`image_caption_provider=ollama/qwen2.5vl:7b`，preflight 明确告警「收到图片只会退化成 `[图片]`」 |
| **图片发送** | ⬜ 未测 | `image_mode=uia`，走 `weemessenger_sender.py:116-125` → `UiaSender` 剪贴板贴图，**绕过** `WxuiSender._post`；本次未执行（涉及真实发图给他人，超出安全边界） |
| **表情包** | ❌ **当前后端不支持** | `send_sticker` → `weemessenger_sender.py:140` `_post({"type":"send_sticker"})` → `wxui_sender.py:56-58` 明确拒绝非 `send_message` 并 `return False`。即生效的 `wxui` 后端下表情包**完全不可用**（文档写「⚠ 部分」指的是 WeeMessenger 通道能力） |
| **语音** | ⬜ 未测 | 文档称「只收」，需 VB-Cable 虚拟声卡；本次未验证 |
| **群聊** | ⬜ 未测 | `group_reply_mode=mention`；本次未构造群聊消息 |
| **绘图** | ⬜ 未测 | 文档称需 AstrBot 绘图插件 + 图像 API key；本次未验证 |
| **压力 / 长时间稳定性** | ⬜ 未测 | 见文末局限 |

## 四、与 `docs/SETUP.zh-CN.md` 验收清单逐条对照

| 文档条目 | 文档声称 | 独立验证结论 |
|---|---|---|
| `SETUP.zh-CN.md:184` 5 个端口全 OK | `[x]` | **基本一致**。稳定态成立；但首次探测 9877 未监听，措辞偏绝对 |
| `SETUP.zh-CN.md:185` 发送模式 WxuiSender | `[x]` | **一致**（`wxui / WxuiSender`，`dry_run=false`） |
| `SETUP.zh-CN.md:186` 文本真实送达并回读（`localId=14`） | `[x]` | **一致**：`localId=14 send=1 time=15:22:30` 复核存在。但「已实测送达」未涵盖**假阴性**（本地 `localId=15` 送达却报失败），措辞偏乐观 |
| `SETUP.zh-CN.md:187-188` 被动回复，`localId=101` | `[x]` | **一致，复核为真** |
| `SETUP.zh-CN.md:189` 面板主动发送能发出去 | `[ ]` | **仍未通过**：实测 2 次，1 次假阴性、1 次真实失败 |
| `SETUP.zh-CN.md:84` 发图片 ✅ | `✅` | **夸大/有误导**：生效后端是 `wxui`，图片走 `UiaSender` 且**未回读验证**；而文档把「✅」与 `weemessenger_image_mode: uia`（WeeMessenger 通道）并列，未说明当前实际走的是 `wxui`+UiaSender。本次未测 |
| `SETUP.zh-CN.md:85` 发表情包 ⚠ 部分 | `⚠ 部分` | **对当前后端夸大**：`wxui` 后端下为**完全不支持**（见上表） |
| `SETUP.zh-CN.md:87` 图片理解 ⚠ 待提供方 | `⚠` | **一致**，且已确认为不可用 |
| `SETUP.zh-CN.md:50` WeFlow v6.3.2 | — | **一致**：`C:\Program Files\WeFlow\WeFlow.exe` FileVersion=`6.3.2`（仓库内 `weflow\WeFlow-main\package.json` 的 `5.0.0` 是源码副本，与安装版无关） |
| `SETUP.zh-CN.md:73/102` `scripts\weflow-start.ps1` | 要求执行 | **不一致 —— 该文件不存在**。`scripts\` 下无此文件；`start-all.ps1` 会打印「✗ 未找到 scripts\weflow-start.ps1（WeFlow 尚未配置完成）」。文档的启动步骤 2 无法照做 |
| `SETUP.zh-CN.md:46-76` 目录树 | — | **遗漏** `scripts\test_guards.py`（护栏单测，实际存在且是文档「排错/安全」的核心工具） |
| `SETUP.zh-CN.md:155` 「`send_whitelist` 填死」 | 安全红线 | **与实际运行状态矛盾**：`/senders` 显示白名单为空 = 不限对象 |
| `SETUP.zh-CN.md:158` 「两个自动化程序不要同时操作微信窗口」 | 安全红线 | **与实际运行状态矛盾**：9877 WeeMessenger 在跑 + bridge 用 `wxui` + `image_mode=uia`，三个 UI 驱动并存 |

## 五、问题清单（按严重度排序）

### P0-1 消息已送达却回报失败（假阴性）→ 重试会重复发送
- **证据**：`localId=15, isSend=1, time=17:48:17` 已入库；bridge `17:48:30 ok=False`。
- **机制**：`scripts/wxui.py:249` 全等比较 + `wxui.py:142` `fit()` 不剔除标点。
- **影响**：调用方（AstrBot / 面板 / 任何重试逻辑）会认为发送失败并**重复发送**，造成骚扰与封号风险。
- **复现**：
  ```powershell
  Invoke-RestMethod -Method Post http://127.0.0.1:8766/send -ContentType 'application/json' `
    -Body '{"target":"文件传输助手","text":"[假阴性复现]"}' | ConvertTo-Json
  $tok=(Get-Content wechat-ai-stack\secrets\weflow-token.txt -Raw).Trim()
  Invoke-RestMethod "http://127.0.0.1:5031/api/v1/messages?talker=filehelper&limit=5&access_token=$tok"
  ```

### P0-2 消息内容完整性：落地文本 ≠ 请求文本
- **证据**：请求 `[独立验证]2026-10-14 17:48:10`，落地 `[独立验证]2026-10-14 17:48:10，`。
- **旁证**：`localId=16 time=17:48:55 content=市场在`（`isSend=1`）**不是本次任何命令发出的**，却出现在同一会话、同一分钟内 → 存在**并发的另一个 UI 自动化写入者**。
- **影响**：消息可能被污染/串写。与 `SETUP.zh-CN.md:158` 自己定的红线「两个自动化程序不要同时操作微信窗口」直接冲突（当前 WeeMessenger 9877 在跑）。
- **复现**：连续两次 `/send` 并逐条比对 `content` 与请求文本。

### P1-3 OneBot `user_id` / `group_id` 跨进程不稳定（设计缺陷）
- **代码**：`akasha-bridge/state.py:34-36`
  ```python
  def _wxid_to_int(wxid: str) -> int:
      """将微信 wxid 映射为稳定的整数 ID。"""
      return abs(hash(wxid)) % (2**31)
  ```
- **实测**（`PYTHONHASHSEED` 未设置；3 个全新进程，同一 wxid `wxid_CONTACT_A`）：
  ```
  run 1 -> 1580386373
  run 2 -> 1282326851
  run 3 -> 1257425979
  ```
  钉住后即稳定：`PYTHONHASHSEED=0` → `900347488`（两次一致）。
- **影响**：**docstring 声称「稳定」，实际每次重启 bridge 都变**。`state._ob_id_to_contact` 又是纯内存映射（`bridge_core.py:264/266`），重启即清空。AstrBot 侧以 `user_id` 为会话键 → 重启后历史会话失联、记忆错乱；且 `bridge_core.py:263`（群组用 `group_name`）与 `:220`（私聊用 `sender_wxid`）共用同一 2^31 值域，**哈希碰撞会导致群/私聊回复错人**。
- **复现**：见上（连跑 3 次 `python -c 'print(abs(hash("wxid_CONTACT_A")) % (2**31))'`）。

### P1-4 白名单校验与 OCR 定位口径不一致 → 可绕过白名单发错人
- **代码**：`weemessenger_sender.py:192` 白名单用**精确匹配** `name not in self.whitelist`；`scripts/wxui.py:202` OCR 定位行却用**部分匹配** `key in text or text in key`。
- **影响**：白名单填 `张三` 时，OCR 的 partial 兜底可能命中「张三丰」的行 → 护栏判定「已授权」，消息却发给另一个人。这是**授权校验与实际投递对象脱钩**。
- **复现**：在白名单写入一个为他人昵称子串的名字，观察 `wxui.py` stdout 的 `ocr_candidates` / `picked_index` 与最终 `localId`。

### P2-5 OCR 截图偶发返回空 → 整次发送失败
- **证据**：`"ocr":{"png":null,"engine":null,"picked_index":null}`，`"ocr_candidates":[]`。
- **影响**：可用性抖动，用户可见「发送失败」。属真实缺陷而非配置问题（文档 `SETUP.zh-CN.md:176` 把它归因为「会话列表里没有这个名字/窗口太小」，**归因不准**——本例是截图根本没拿到图）。
- **复现**：重复调用 `/send`，统计出现 `could not locate target row by OCR` 的频率。

### P2-6 护栏单测不覆盖生效后端
- `test_guards.py` 仅测 `WeMessengerSender`；当前生效的是 `WxuiSender`（且有额外的 `_post` 拒绝分支 `wxui_sender.py:56-58`）。护栏在 `WxuiSender` 上的行为**无自动化测试**。
- **复现**：`Select-String -Path scripts\test_guards.py -Pattern 'WeMessenger|Wxui'`

### P2-7 代码内注释/报错信息与实现不符（易误导排错）
- `senders.py:67`：`未知 sender_backend: {backend}（可选值：weemessenger / uia）` —— 但 `wxui` 才是推荐且当前生效的值（`senders.py:31`）。报错信息**漏列 `wxui`**。
- `senders.py:4-11` 模块 docstring 只描述 `weemessenger` / `uia` 两种通道，未提 `wxui`。
- **复现**：`Select-String -Path akasha-bridge\senders.py -Pattern '可选值|wxui'`

### P2-8 凭据卫生（观察项，非本次链路缺陷）
- `filehelper` 会话中存有 `localId=9 send=1 time=2026-10-13 18:09:35 content=[sk-bc86***（已打码）]` —— 形似 OpenAI 风格 API key 的字符串**曾被发出并留存在微信数据库中**。建议核查并轮换。本文不再复制该字符串。

## 六、本次验证的局限

**我只跑了上面列出的这些命令（preflight ×2、test_guards、/status、/senders、2 次 /send、WeFlow 回读、日志检查、以及相关源码阅读），没有做任何压力测试、并发测试或长时间稳定性测试**；因此：

- 未能给出 wxui 发送路径的成功率/失败率（仅 2 次样本，1 送达成败报告错误、1 次真实失败）。
- 未能复现或排除 P0-2 的并发写入者身份；未能确定全角逗号的产生环节。
- 未验证图片发送、表情包、语音、群聊、绘图、图片理解（后者已确认环境不具备）。
- 未验证 AstrBot 侧 AI 话术中「文档处理 / 表格处理 / PDF 操作 / 技能开发」等能力是否真实可用。
- 未验证重启后的 `user_id` 漂移对 AstrBot 会话的实际影响（仅通过 3 进程哈希实验证明 ID 不稳定，未做重启 AstrBot/bridge 的端到端对比——重启服务超出本次授权范围）。
- 所有「通过」结论仅代表**在我采样的这个时间窗内、以我执行的命令为限**成立。
