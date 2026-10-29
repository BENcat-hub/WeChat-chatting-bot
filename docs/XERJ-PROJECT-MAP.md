# XERJ 项目索引与整理报告（2026-10-28）

> 目的：把 38 GB 的 `wechat-ai-stack` 变成「可以用一句话找回代码」的本地语料，
> 并据此给出一份按优先级排好的整理清单。
> 关联文档：[HANDOVER-2026-10-28.md](HANDOVER-2026-10-28.md)（系统现状/坑/未完成项）。

---

## 1. 语料怎么用（3 分钟上手）

节点是**独立进程**，`xerj` CLI 和 MCP 的 `xerj_*` 工具都只是客户端；节点没起时一切检索
都报 `no XERJ node is listening at http://127.0.0.1:9200`。

```bash
# 起节点（常驻；数据目录沿用 10-22 建的那份）
~/.local/bin/xerj --insecure \
  --data-dir C:/Users/<YOU>/.xerj-code/data

# 检索（prefix=ax，字符串查询 = 定义优先的代码检索）
export XERJ_DISABLE_FEEDBACK=true   # 本机不想上报 field report 时
xerj search --url http://127.0.0.1:9200 --prefix ax "_content_key"

# 数据地图 / 进度 / 重建（详见 §7）
xerj autoindex map   --url http://127.0.0.1:9200 --prefix ax
xerj autoindex status --url http://127.0.0.1:9200 --prefix ax
```

- **查符号优先用符号名**：`_ensure_solo_song` 一次命中 `rvc-backend/server.py:350 [function]`（score 229）；
  中文短语适合查注释/文档（如 `点歌 复读`），自然语言长句会退化成 BM25 平铺。
- 需要结构化过滤时传 ES DSL，例如只看函数：
  `{"bool":{"must":[{"match":{"code":"wxui_queue"}},{"term":{"kind":"function"}}]}}`。
- 索引 **30 天过期**会拒绝检索（`xerj` 的防陈旧机制），重建见 §7。
- admin key 在 `wechat-ai-stack/data/admin.key`，CLI 不给 `--api-key` 时会自动读它。

## 2. 这次索引的口径与规模

| 项 | 值 |
| --- | --- |
| 运行 | `run-20261027T164504Z-125c-0000`，根 `...\wechat-ai-stack`，wall 155.1 s |
| 扫描 | **2,466 个路径 → 2,328 个唯一内容**（138 个别名/硬链接去重，23 个 junk 跳过） |
| 入库 | **60,641 条记录**，17 个 dataset |
| 主 dataset | `ax-docs`：58,889 条 / 2,305 文件（代码 + 文档 + 配置，字段见下） |
| 结构化 dataset | `bertvits2-repo`(csv 863)、`astrbot-data-2`(json 280)、`astrbot-data-module-complexity-scan`(csv 223)、`rvc-backend-rvcsvc-api-docs`(218)、`astrbot-data-cases`(59)、`astrbot-data-manifest`(45)、`astrbot-data-giant-mixin-dependency-r`(42)、`astrbot-data`(yaml 22) |
| 空 dataset（0 条） | 7 个 `astrbot-data-*` sqlite（cron/preferences/conversations/personas/platform-stats/provider-stats/umo-aliases）+ `akasha-bridge`(jsonl) —— 运行中的 sqlite 被服务独占，未解析，**查这些数据要直接读文件或走 API，别指望检索** |
| 关系图 | `.xerj-memory-wechat-ai-stack-edges`，2,125 条 `samedir` 边 |
| 代码插件 | `--code-analyzer code`：1,828 个 code 文件，1,827 入库 |

`ax-docs` 的可用字段：`path` / `title` / `kind`(function/class/const) / `name` / `line` /
`start_line`+`end_line` / `language` / `code` / `impl`（周边实现）/ `body` / `text`（semantic_text）/
`headings`（文档标题）。按 `path` 过滤目录：`{"prefix":{"path":"akasha-bridge/"}}`。

**排除表** `wechat-ai-stack/.xerjignore`（中文注释版）：模型/训练/数据集/temp/logs/output/ffmpeg、
`bertvits2/pretrained`、`astrbot/data/plugin_data`、根下 `downloads|logs|output|tools/ffmpeg|secrets|data`、
`*.lock *.db* *.sqlite*`、`*.bak* *.broken* *.orig *.pristine`、`*.mp3 *.wav *.flac *.m4a *.mp4 *.zip *.7z *.pth *.ckpt`。
`.venv` / `node_modules` 未显式排除，但索引器的默认忽略已把它们挡住（2,328 个唯一内容里没有 site-packages）。

## 3. 系统地图（5 个服务 + 微信本体）

| 组件 | 端口 | 入口 | 关键文件（检索验证过的行号） |
| --- | --- | --- | --- |
| 微信客户端 | — | 人工扫码 | `scripts/show-wechat-window.py`（托盘还原）；`C:\Program Files\Tencent\Weixin\Weixin.exe` |
| WeFlow 5.0.0 | 5031 | `scripts/start-weflow50.cmd` | 见 HANDOVER §4：必须用回收站找回的 5.0.0 树 + 打过补丁的 `wcdb_api.dll` |
| akasha-bridge | 8766 | `akasha-bridge/main.py` | `bridge_core.py`（路由/队列）、`wxui_sender.py`（`_post:205`、`_content_key:753`、`drain_queue:879`）、`senders.py`、`uia_sender.py`、`ob_protocol.py:1`（OneBot v11）、`web_panel.py`、`ui_lock.py`、`weemessenger_sender.py`、`image_forward.py` |
| AstrBot 4.29.1 | 6185 / OneBot WS 11229 | `.venv` + `astrbot` 包 | 8 个插件（见 §4）；WebUI 端口在 `start-all.ps1` 的 preflight 里 |
| 即梦生图 | 8790 | `scripts/jimeng_image_server.py` | `scripts/jimeng_server_keeper.py`（守护）、`scripts/jimeng_ban_watch.py`（封禁探测） |
| rvc 翻唱后端 | 3333 | `rvc-backend/server.py`（FastAPI） | `server.py:350 _ensure_solo_song`、`:707 match_dynamics`、`:758 restore_timbre`、`:849 apply_vocal_gate`、`:1351 _pitch_profile`、`:1387 auto_key_shift`、`:1406 clean_vocal_output` |

机器级工具链都在 `scripts/`：`start-all.ps1` / `stop-all.ps1` / `preflight.ps1`、
`wxui.py`（UI 自动化发送）+ `wxui-config.json`、`winio.py`（`close_detached_chat:447`）、
`wechat_cleanup.py`、`wmcp.py`、`voice_send.py`、`ocr.py`。
`start-all.ps1` 的设计约定：WeChat → WeFlow → AstrBot → bridge；**WeeMessenger(9877) 故意不常驻**；
`DEEPSEEK_API_KEY` 从 User 作用域读进子进程（不落盘）。

## 4. 结构热区：5 个巨型文件（整理第一优先级）

| 文件 | 行数 | 体积 | 形态 |
| --- | --- | --- | --- |
| `astrbot/data/plugins/astrbot_plugin_private_companion/page_api.py` | **31,924** | 1.69 MB | 单类 `PrivateCompanionPageApi`（`:385`）≈26,000 行 / **794 个方法**，只有 5 个顶层 def |
| `.../astrbot_plugin_image_companion/image_runtime.py` | 24,110 | 1.15 MB | 565 个函数/方法 |
| `.../astrbot_plugin_private_companion/main.py` | 21,779 | 974 KB | 643 个函数/方法 |
| `.../astrbot_plugin_private_companion/proactive_message.py` | 19,728 | 948 KB | 主动消息全流程 |
| `.../astrbot_plugin_private_companion/daily_state.py` | 19,001 | 898 KB | 日程/便签/状态（`memo_reminder_user_ids:9555`） |

其他 300 KB+ 的单体：`proactive_engine.py`(552 KB)、`user_memory.py`(551 KB)、
`memory_companion/core/service.py`(507 KB) 与 `core/store.py`(506 KB)、
`llm_tool_actions.py`(442 KB)、`command_handlers.py`(339 KB)、`private_image.py`(329 KB)。

**为什么这是问题**：一个类 794 个方法，任何改动都要在 3 万行里定位；`grep` 之外的检索
（本索引）能救急，但真正的整理是**按资源域拆模块**。建议顺序（每步可独立验证、不改变行为）：

1. `page_api.py` → `page_api/` 包：`auth.py`（鉴权/trust boundary，文件头注释集中在 :236–:314）、
   `pages.py`（页面/路由注册）、`media.py`（引用图/上传，`:239–:258` 的内容寻址逻辑）、
   `memory.py`（成员/角色/知识库面板）、`admin.py`（迁移导入/配置投影，`:308` 明确禁止回投的字段）。
   做法：先按方法名前缀聚类（`_handle_*` / `_render_*` / `_upload_*` / `_migrate_*`），
   抽成 `PrivateCompanionPageApi` 的 mixin，保持 MRO 不变 → 跑 `astrbot/data/plugins` 自带测试。
2. `image_runtime.py` → 生成/参考图/缓存/失败重试四条线（565 个函数里大量 `_cli_*`/`_gen_*` 前缀）。
3. `main.py` / `daily_state.py` / `proactive_message.py`：这三个插件文件互相 import 很重，
   拆分前先用本索引查 `{"prefix":{"path":"astrbot/data/plugins/astrbot_plugin_private_companion/"}}`
   统计交叉引用，避免拆出循环依赖。

## 5. 可清理清单（按风险从低到高）

| 类别 | 数量 | 位置 | 建议 |
| --- | --- | --- | --- |
| 探测脚本 | **358** | `rvc-backend/_*.py` 102 + `scripts/_*.py` 256 | 先按修改时间归档到 `_archive/probes/`，保留最近 5 个 `_diag_*`；根目录还有 `_cfg5/_cfg6/_patch_click/_patch_header/_patch_search/_patch_search2/_send_i/_send_txt.py` 8 个 |
| 手工备份 | **95** | 全项目 `*.bak*` | 已被索引排除；审计后压成一个 `backups-20261028.zip` 再删，注意 `rvc-backend/server.py.broken-2139` **必须留**（唯一带合唱拆轨的副本） |
| `.orig`/`.pristine` | 2 | — | 同上看一眼再删 |
| 运行日志 | 5,528 个文本文件 | `logs/`（`bridge.log`、`astrbot`/`weflow` 日志） | 按月归档；`.xerjignore` 已排除，不影响检索 |
| 下载残留 | 146 | `downloads/` | 同上看 |
| 运行时状态 | 99 | `data/` | **别删**（admin.key、统计缓存） |
| 发布仓库落后 | 1 | `wechat-ai-stack-share/wechat-ai-stack`（HEAD 535ba8a） | 见 HANDOVER §5-8，改完统一同步 + push |
| 插件配置备份 | 1 | `private_companion/_conf_schema.json.bak-recitalschema-201217` | 确认新 schema 生效后可删 |

**不做的事**：不自动删任何 `rvc-backend/_*` 探针——其中不少是 10-27 调音时唯一能复现 A/B 的脚本
（`_ab_clean.py`、`_ab_variants.py` 等），删掉就再也复现不了「3 号 harvest」那组对比。

## 6. 实测检索配方（可直接抄）

| 想找什么 | 查询 | 命中 |
| --- | --- | --- |
| 发送去重键怎么算 | `_content_key` | `akasha-bridge/wxui_sender.py:753 [function]` |
| 悬浮聊天窗怎么关 | `close_detached_chat` | `scripts/winio.py:447 [function]` |
| 点歌怎么避开合唱版 | `_ensure_solo_song` | `rvc-backend/server.py:350 [function]` |
| 队列重试配置 | `wxui_queue_max_attempts` | `akasha-bridge/config.py:71` / `:83 [const]` |
| WebUI 路由怎么注册 | `app = FastAPI` | `.../proactive_chat/core/web_admin_server.py:109 _setup_app` |
| 便签提醒白名单 | `memo_reminder_user_ids` | `.../daily_state.py:9555`、`memo_notes.py`、`llm_tool_actions.py` |
| OneBot v11 协议实现 | `OneBot v11 协议处理` | `akasha-bridge/ob_protocol.py:1` |
| 面板主类 | `class PrivateCompanionPageApi` | `.../page_api.py:385 [class]`（另命中 `:358`） |

## 7. 维护

```bash
# 增量（改完代码跑一次，保留 code 分析器）
xerj autoindex wechat-ai-stack --code-analyzer code --yes

# 全量重建（清掉已删文件留下的记录；08:00 本次用过）
xerj autoindex wechat-ai-stack --code-analyzer code --yes --drop-indexed

# 删掉本目录的索引（不会碰别的 prefix/root）
xerj autoindex remove --prefix ax --root wechat-ai-stack --yes
```

**踩过的坑（都已规避）**：
1. `astrbot/astrbot.lock` 被 AstrBot 独占 → `os error 33 另一个程序已锁定文件的一部分`，
   **整轮 abort**。已在 `.xerjignore` 排除 `*.lock`/`*.db*`/`*.sqlite*`。
2. `xerj autoindex` 默认 prefix 是 `ax`，同一个 prefix 换 root 会被拒绝（dataset key 是 `ds:<prefix>:<slug>`），
   换目录先 `remove` 或换 `--prefix`。
3. 曾误在 `docs/` 子目录建过一次 `ax-docs`，把整库顶掉；后用
   `xerj autoindex remove --prefix ax --root wechat-ai-stack/docs --yes` 清掉并 `--replace-prefix` 重建。
4. 控制台是 GBK：打印中文会 `UnicodeEncodeError`，脚本一律写 UTF-8 文件再用 read 工具看。
5. `xerj corpus list` 读的是 `~/.xerj-code/state`（不存在），对本机本地语料会误报「nothing has been indexed」；
   以 `xerj autoindex map` 和 `_cat/indices` 为准。

## 8. 结论

- 这个项目现在**可检索**：60,641 条记录、2,328 个唯一文件、符号级定义优先命中，找代码从
  「翻 38 GB」变成「一条命令」。
- 整理的第一优先级不是删文件，而是**拆 5 个巨型文件**（§4）；第二优先级是把 358 个探针脚本归档、
  95 个 `.bak` 压缩（§5）；这两件事都不改变运行行为，可以安全分步做。
- 索引本身也有边界：sqlite 运行时数据库、二进制模型、音频素材不在语料里（§2），查这些请直读文件/API。
