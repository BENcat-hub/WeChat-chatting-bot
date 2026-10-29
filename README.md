# wechat-ai-stack · 微信 AI 伴侣系统

把微信变成一个有**长期记忆**、会**主动找你说话**、能**发图发表情包**、有稳定**人设**的 AI 伴侣。
全部在本机运行：读取微信本地数据库 + 模拟真人键盘操作发送，不经过任何中转服务器。

> 📖 想了解整体设计与各环节接口流程，见 **[`docs/PROJECT-BOOK.zh-CN.md`](docs/PROJECT-BOOK.zh-CN.md)**（项目书）。
> ⚠️ 使用前请先读 **[`NOTICE.md`](NOTICE.md)**（版权与分享范围）与 **[`THIRD-PARTY.md`](THIRD-PARTY.md)**（第三方许可证）。

---

## 目录结构

```
wechat-ai-stack/
├─ weflow/                      WeFlow 5.0.0 源码树（微信本地库 → HTTP/SSE）
│  └─ resources/wcdb/win32/x64/ wcdb_api.dll（已打时间闸门补丁）+ .pristine 原始备份
├─ akasha-bridge/               桥接服务（SSE 接收 + OneBot 转发 + 出站护栏 + 面板）
│  ├─ config.example.json       ← 复制为 config.json 后填写
│  └─ tests/                    单元测试（47+ 用例）
├─ astrbot/
│  ├─ data/plugins/             六个自研插件
│  │  ├─ astrbot_plugin_private_companion/   核心：人格·陪伴·防抖·复核·人设
│  │  ├─ astrbot_plugin_memory_companion/    长期记忆
│  │  ├─ astrbot_plugin_proactive_chat/      主动消息
│  │  ├─ astrbot_plugin_image_companion/     看图/生图
│  │  ├─ astrbot_plugin_affection/           好感度
│  │  └─ meme_manager/                       表情包
│  ├─ data/cmd_config.json.example
│  └─ requirements.txt          AstrBot 本体版本（pip 安装，仓库不含源码）
├─ scripts/                     工具脚本
│  ├─ wxui.py                   UIA 发送器（含常驻 worker 子命令）
│  ├─ wxui-config.json          发送器配置（超时/OCR/预热窗口）
│  ├─ start-weflow50.cmd        WeFlow 唯一受支持的启动入口
│  ├─ start-all.ps1             按序启动全部服务
│  ├─ preflight.ps1             五个端口体检
│  ├─ jimeng_image_server.py    即梦图片生成桥（可选）
│  └─ weflow_*.py / probe_*.py  读取、探测、诊断脚本
├─ weemessenger/                小信使（可选的第三方发送后端，默认不用）
├─ patches/weflow/              wcdb_api.dll 补丁工具与说明
├─ deploy/init.ps1              一键初始化（venv、依赖、配置、补丁）
├─ docs/                        项目书、安装、验证、各组件接口规格、事故排查记录
└─ tests/                       跨组件测试
```

---

## 你需要自己准备的东西

仓库里**不含**下面这些（体积过大或需要授权），需自行安装：

| 需要 | 说明 |
|---|---|
| Windows 10/11 | 整套系统依赖 UI 自动化，仅 Windows |
| 微信 PC 版 4.x | 已登录你要托管的那个帐号 |
| **AstrBot 4.28.2** | `pip install astrbot==4.28.2`（仓库只带插件与数据目录，不含本体源码） |
| Python 3.12 | AstrBot 与 bridge 各自建独立 venv |
| Node.js 20+ / npm | WeFlow 需要跑 `npm install`（会下载 Electron，约 200–300 MB） |
| **ffmpeg.exe** | WeFlow 解码 HEVC/`wxgf` 图片必需，见 `docs/WEFLOW-IMAGE-FFMPEG-FIX.md` |
| 模型 API key | 填进 `astrbot/data/cmd_config.json`（DeepSeek / SiliconFlow / 火山 等） |
| `windows-mcp` | `pip install windows-mcp`（`wxui.py` 的 UI 自动化后端） |

---

## 快速开始

```powershell
# 1) 一键准备：建 venv、装依赖、生成配置、给 DLL 打补丁
powershell -ExecutionPolicy Bypass -File deploy\init.ps1
#    （如果本机已有 ffmpeg.exe，可加：-FfmpegPath "C:\tools\ffmpeg.exe"）

# 2) 填密钥
notepad akasha-bridge\config.json          # access_token / bot_wxid / bot_nicknames / wxui_*
notepad astrbot\data\cmd_config.json       # 模型平台 api_key

# 3) 按顺序启动（每一步稳定后再起下一个）
#    a. 登录微信 PC 版
#    b. scripts\start-weflow50.cmd                 ← 等 5031 可访问
#    c. akasha-bridge\start.bat                    ← 面板 http://127.0.0.1:8766
#    d. astrbot\.venv\Scripts\python.exe astrbot\main.py

# 4) 体检
pwsh -File scripts\preflight.ps1
curl.exe "http://127.0.0.1:8766/senders"
```

首次使用务必保持 `send_dry_run: true`，确认链路全通后再改成 `false` 真发。

---

## 端口一览

| 端口 | 组件 | 说明 |
|---|---|---|
| 5031 | WeFlow | 微信消息读取（HTTP + SSE），**单点依赖** |
| 8766 | akasha-bridge | 控制面板 `/`、状态 `/status`、发送自检 `/senders`、手动发送 `POST /send` |
| 11229 | AstrBot aiocqhttp | OneBot v11 反向 WebSocket |
| 6185 | AstrBot WebUI | 管理面板 |
| 8790 | jimeng_image_server | 即梦图片生成桥（可选） |

---

## 常用运维

```powershell
pwsh -File scripts\preflight.ps1                 # 五个端口 + 关键配置体检
pwsh -File scripts\start-all.ps1                 # 按序拉起全部服务（可 -SkipWeChat 等）
python scripts\weflow_sessions.py                # 列微信会话（验证 5031 通不通）
python scripts\weflow_last_messages.py <wxid> 20 # 看某会话最近 20 条
python scripts\weflow_add_autodownload.py --apply# 把联系人加进高清图自动下载白名单
python scripts\probe_weflow_policy_version.py    # 直接问 wcdb_api.dll 的策略版本（诊断 -101）
python patches\weflow\apply_wcdb_patch.py --check weflow
```

面板接口：

```powershell
curl.exe http://127.0.0.1:8766/status      # 运行状态、连接状态、日志尾巴
curl.exe http://127.0.0.1:8766/senders     # 发送后端、dry-run、白名单、重发队列长度
curl.exe -X POST http://127.0.0.1:8766/send -H "Content-Type: application/json" `
  -d '{\"target\":\"文件传输助手\",\"text\":\"测试\"}'
```

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [`docs/PROJECT-BOOK.zh-CN.md`](docs/PROJECT-BOOK.zh-CN.md) | **项目书**：架构、组件、接口、流程、部署、排障 |
| [`docs/interfaces/weflow.md`](docs/interfaces/weflow.md) | WeFlow 的 HTTP/SSE 全量接口、消息字段、错误码、二进制补丁规格 |
| [`docs/interfaces/akasha-bridge.md`](docs/interfaces/akasha-bridge.md) | bridge 的面板接口、OneBot 协议、发送后端契约、配置键全表 |
| [`docs/interfaces/astrbot-plugins.md`](docs/interfaces/astrbot-plugins.md) | 六个自研插件的钩子、面板路由、配置键、集成点与落盘全表 |
| [`deploy/init.ps1`](deploy/init.ps1) | 一键初始化脚本 |
| [`patches/weflow/README.md`](patches/weflow/README.md) | `wcdb_api.dll` 时间闸门补丁的来龙去脉 |
| `docs/SETUP.zh-CN.md` | 手工安装步骤 |
| `docs/VERIFICATION.md` | 验证清单与实测结果 |
| `docs/WEFLOW-101-FINDINGS.md` | `-101` 事故的完整排查记录（含失败路线） |
| `docs/WXUI-SEND-SPEED-FIX.md` | 发送耗时优化（常驻 worker + warm 快路径）实测数据 |

---

## 上传到 GitHub

发布树本身就是可直接 `git init` 的仓库骨架：`.gitignore` 已就位，密钥（`config.json` / `cmd_config.json` /
`secrets/*.txt`）与全部运行期数据（`plugin_data/`、日志、队列图片、数据库）都已在忽略列表里。

```powershell
cd wechat-ai-stack
git init
git add -A
git commit -m "wechat-ai-stack: 微信 AI 伴侣系统（WeFlow + 补丁、akasha-bridge、AstrBot 六个自研插件）"
git branch -M main
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git push -u origin main
```

体积与上限：

| 项 | 数值 |
|---|---|
| 发布树总大小 | 约 248 MB（2398 个文件） |
| 其中 `weflow/` | 约 200 MB（Electron 构建产物 + WCDB 二进制 + 字体） |
| 本树最大单文件 | `weflow/resources/wcdb/.../WCDB.dll`，约 9.2 MB（GitHub 单文件上限 100 MB） |
| 压缩包 | 约 110 MB（`git` 仓库会比它略大，因为不压缩二进制） |

本树**不含** `node_modules`：里面有 224 MB 的 `electron.exe`，会直接超过 GitHub 单文件上限，也超出普通仓库的体积预期。
需要在本机跑起来时，进 `weflow/` 执行 `npm install` 即可（见「你需要自己准备的东西」）。

仓库建议建为 **Private**。若要转为公开，请先读 [`NOTICE.md`](NOTICE.md) 与 [`THIRD-PARTY.md`](THIRD-PARTY.md)。

---

## 已知限制

- **5031 单点**：WeFlow 挂了，收消息全停（每 30 秒自动重试连接）。
- **发送需要微信窗口可见**：最小化、被锁屏、或你正在用鼠标时会被拒绝（这是刻意的安全设计，避免抢你的输入）。
- **单条发送 2–10 秒**：UI 自动化 + 回读校验的固有代价；常驻 worker 已把热路径压到约 2 秒。
- **WeFlow 5.0.0 与 6.3.2 不能同时运行**：共用 `%APPDATA%\weflow` 与同一个微信数据库句柄。
- **只能一个微信帐号**：整套系统按单账号设计。
- **依赖模型 API**：断网或额度用尽时无法生成回复（收发链路本身仍在跑）。
- **帐号风险自负**：微信官方不支持此类自动化，请只在自己的设备与帐号上、对自愿的对象使用。

---

## 排障速查

| 现象 | 先查什么 |
|---|---|
| 收不到消息 | `weflow` 的 `wcdb.log` 有没有 `open ok handle=1`；5031 通不通（`weflow_sessions.py`） |
| WeFlow 起不来，日志 `InitProtection rc=-101` | DLL 补丁掉了 → `python patches\weflow\apply_wcdb_patch.py --apply weflow` |
| 日志 `native runtime policy mismatch value=-105` | 锚点文件坏了（**不是**时间到期）→ `pwsh -File scripts\weflow-anchor-check.ps1 -Fix` |
| 能收到但 AI 不回 | AstrBot 面板看消息是否进了管线；`11229` 是否在监听；bridge `/status` 里 `ob_connected` |
| 发出去了但没到 | `/senders` 看 `pending_retries`；`wxui_pending.jsonl` 里有没有堆积 |
| 图片读不出来 | `ffmpeg.exe` 是否在 `weflow\node_modules\ffmpeg-static\`；cwd 是否是 WeFlow 树根 |
| 发送很慢 | 确认 `wxui_warm_worker: true`；`/senders` 里 `worker_alive` 是否为 true |
| 消息重复 | 见 `docs/` 下语义重复排查记录：复核 severe 集合与表情包冷却配置 |
