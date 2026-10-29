# WeFlow 接口与集成规格

> 盘点时间：2026-10-16。树根：`weflow\`（即 `<WEFLOW_ROOT>`，v5.0.0 源码树，已打二进制补丁）。
> 行号均已核对源码。凡源码未确证者标注「未确认」。

## 1. WeFlow 是什么

- **定位**：Electron 桌面应用（非纯服务），主体是 `electron/main.ts`；它用 FFI 加载 `wcdb_api.dll`（WCDB，微信本地 SQLite 封装）**读取**微信 4.x 本地数据库，并在本机暴露一个 HTTP API + SSE 推送，供外部自动化调用。DB 访问跑在 worker 线程（`electron/wcdbWorker.ts`），核心逻辑在 `electron/services/wcdbCore.ts`（5001 行）。
- **监听地址/端口**：默认 `127.0.0.1:5031`。
  - 配置键：`httpApiPort: 5031`、`httpApiHost: '127.0.0.1'`（`electron/services/config.ts:237-238`）；服务类字段 `httpService.ts:129-130`。
  - 自启：`httpService.autoStart()`（`electron/services/httpService.ts:353-365`）读 `httpApiEnabled` / `httpApiPort` / `httpApiHost`；`listen` 在 `httpService.ts:196`，`EADDRINUSE` 处理 `httpService.ts:187-189`。
- **鉴权**：
  - 校验函数 `verifyToken(req, url, bodyParams)`（`httpService.ts:406-425`）：期望 token = `configService.get('httpApiToken')`；**配置为空则一律拒绝**。
  - 三种传递方式任选其一：请求头 `Authorization: Bearer <token>`、query `?access_token=<token>`、POST body `access_token=<token>`。
  - 仅 `/health` 与 `/api/v1/health` 免鉴权，其余全部校验（`httpService.ts:458-463`），失败返回 `401 Unauthorized: Invalid or missing access_token`。
  - token 存放：`%APPDATA%\weflow\WeFlow-config.json` 的 `httpApiToken`（`config.ts:88`、`config.ts:235`、`config.ts:291`，配置文件名 `WeFlow-config`）。本机实测值 `<FILL_ME>`（硬编码于 `scripts\weflow_sessions.py:17`、`scripts\weflow_last_messages.py:17`、`scripts\weflow_add_autodownload.py:39`；bridge 侧对应 `akasha-bridge\config.json` 的 `access_token`，见 `docs\SETUP.zh-CN.md:136`）。
- **启动方式**：可执行文件是树内 Electron：`<tree>\node_modules\electron\dist\electron.exe <tree>`。启动器 `scripts\start-weflow50.cmd` 做了四件事：
  1. `set "ELECTRON_RUN_AS_NODE="` —— 不清掉它 electron 会以 Node 模式跑，不启窗口/不启 API；
  2. 若缺则从 `C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe` 复制到 `<tree>\node_modules\ffmpeg-static\ffmpeg.exe`；
  3. `cd /d "<tree>"` —— **cwd 必须是树根**，未打包的 `imageDownloadService` 用 `process.cwd()\resources\image\...` 找 `img_helper.dll`，cwd 不对则 hook 静默失败、图片无 `mediaUrl`；
  4. `start "" "<tree>\node_modules\electron\dist\electron.exe" "<tree>"`。
- 开机自启与快捷方式已改指该启动器（`HKCU\...\Run\electron.app.WeFlow`、`C:\Users\Public\Desktop\WeFlow.lnk`，原值备份见 `logs\weflow-fix-20261016\`）。

## 2. HTTP API 清单

路由总表在 `handleRequest`（`electron/services/httpService.ts:430-524`）。全部响应 JSON；body 参数会被并入 query（`httpService.ts:452-456`）；CORS 只放行 `localhost|127.0.0.1` origin（`httpService.ts:431-438`）；`OPTIONS` → 204。

| Method | Path | 处理器（行号） | 备注 |
|---|---|---|---|
| GET | `/health`、`/api/v1/health` | `httpService.ts:465-466` | 免鉴权，返回 `{status:'ok'}` |
| GET | `/api/v1/push/messages` | `handleMessagePushStream` `httpService.ts:467-468` | SSE；`messagePushEnabled !== true` 时 403（`httpService.ts:544`） |
| GET | `/api/v1/messages` | `handleMessages` `httpService.ts:1043-1152` | 消息列表 |
| GET | `/api/v1/sessions` | `handleSessions` `httpService.ts:1158-1210` | 会话列表 |
| GET | `/api/v1/sessions/:id/messages` | `handlePullMessages` `httpService.ts:1216-1258` | ChatLab 拉取；`:id` 取 `pathname.split('/')[4]`（`httpService.ts:477-483`） |
| GET | `/api/v1/contacts` | `handleContacts` `httpService.ts:1264-1296` | 联系人 |
| GET | `/api/v1/group-members` | `handleGroupMembers` `httpService.ts:1302-` | 群成员 |
| GET | `/api/v1/sns/timeline` | `httpService.ts:488-490` | 朋友圈；**字段结构未确认** |
| GET | `/api/v1/sns/usernames` | `httpService.ts:491-493` | **未确认** |
| GET | `/api/v1/sns/export/stats` | `httpService.ts:494-496` | **未确认** |
| GET | `/api/v1/sns/media/proxy` | `httpService.ts:497-499` | **未确认** |
| POST | `/api/v1/sns/export` | `httpService.ts:500-502` | **未确认** |
| GET | `/api/v1/sns/block-delete/status` | `httpService.ts:503-505` | **未确认** |
| POST | `/api/v1/sns/block-delete/install` | `httpService.ts:506-508` | **未确认** |
| POST | `/api/v1/sns/block-delete/uninstall` | `httpService.ts:509-511` | **未确认** |
| DELETE | `/api/v1/sns/post/:id` | `httpService.ts:512-514` | **未确认** |
| GET | `/api/v1/media/*` | `handleMediaRequest` `httpService.ts:515-516`、`570-617` | MIME 表 + 路径穿越防护；根目录 = `getApiMediaExportPath()` |
| — | 其它 | `httpService.ts:517-518` | 404 |

方法不符时返回 405（`sendMethodNotAllowed`，SNS 系列均做此检查）。

### 2.1 `GET /api/v1/messages`（最重要）

- 参数（`handleMessages`，`httpService.ts:1043-1152`）：`talker`（**必填**，wxid 或 `xxx@chatroom`；缺失返回 HTTP 400，见 `akasha-bridge\main.py:121-122` 注释）、`limit`（默认 100，上限 10000）、`offset`（默认 0）、`keyword`、`start`/`end`、媒体开关（`parseMediaOptions` `httpService.ts:1011-1030`：`media`/`meiti`、`image`/`tupian`、`voice`、`video`、`emoji`）、`chatlab`、`format=json|chatlab`。
- 时间参数（`parseTimeParam` `httpService.ts:1929-1953`）：`YYYYMMDD` → 秒（`end` 归到 23:59:59.999）；纯数字时 **> 1e10 视为毫秒**并折算成秒。
- 布尔参数（`parseBooleanParam` `httpService.ts:991-1000`）：接受 `1/true/yes/on`。
- 响应：`{success, talker, count, hasMore, media:{enabled, exportPath, count}, messages:[…]}`；`format=chatlab` 时返回 ChatLab 文档 + media。

### 2.2 消息字段（`toApiMessage`，`httpService.ts:1874-1902`）

| 字段 | 含义 | 确认度 |
|---|---|---|
| `localId` | 该会话内本地自增消息 ID（number） | 源码确认 |
| `serverId` | 服务端消息 ID（string；无则 `'0'`，`getMessageServerId` `httpService.ts:1904-1910`） | 源码确认 |
| `localType` | 微信原始消息类型（见 2.5 映射） | 源码确认 |
| `createTime` | 发送时间，**秒**级 Unix 时间戳 | 源码确认 |
| `sortSeq` | 库内排序序号 | 源码确认 |
| `isSend` | 是否本人发出（0/1） | 源码确认 |
| `senderUsername` | 发送者：私聊=会话 wxid，群聊=群成员 wxid | 源码确认 |
| `content` | 展示用文本 | 源码确认 |
| `rawContent` / `parsedContent` | 原始 XML / 解析后内容 | 源码确认 |
| `mediaType` / `mediaFileName` | 媒体类型与文件名 | 源码确认 |
| `mediaUrl` | 可下载 URL（走 `/api/v1/media/...`，需附 `access_token`） | 源码确认 |
| `mediaLocalPath` | 已导出到本地的绝对路径 | 源码确认 |
| `replyToMessageId`、`quote` | 引用消息相关（条件出现） | 源码确认 |
| 群聊「群昵称」字段 | 未在 `toApiMessage` 出现（ChatLab 里有 `groupNickname`） | 未确认 |

### 2.3 `GET /api/v1/sessions`

- 参数：`keyword`（对 `username`/`displayName` 做小写包含过滤，`httpService.ts:1171-1177`）、`limit`（默认 100，1–10000）、`format`。
- 响应（`httpService.ts:1195-1206`）：`{success:true, count, sessions:[{username, displayName, type, sessionType, lastTimestamp, unreadCount}]}`
  - `username` = 会话标识（wxid / `xxx@chatroom` / `gh_xxx`）；`lastTimestamp` 秒；`sessionType` = `group|channel|private`（`getApiSessionType` `httpService.ts:1032-1041`：`@chatroom`→group；`gh_`/`@openim`/`weixin*`→channel；否则 private）。
- `format=chatlab` 变体（`httpService.ts:1181-1193`）：`{sessions:[{id, name, platform:'wechat', type, messageCount, lastMessageAt}]}`。

### 2.4 其它端点参数

- `/api/v1/sessions/:id/messages`（`handlePullMessages` `httpService.ts:1216-1258`）：`limit`（默认 5000，上限 5000）、`offset`、`since`、`end`；返回 ChatLab 文档 + `sync:{hasMore,nextSince,nextOffset,watermark}`。
- `/api/v1/contacts`（`httpService.ts:1264-1296`）：`keyword`、`limit`（默认 100）。
- `/api/v1/group-members`（`httpService.ts:1302-`）：`chatroomId` 或 `talker`，`includeMessageCounts`/`withCounts`、`forceRefresh`；响应字段明细未确认。

### 2.5 消息类型映射（`mapMessageType`，`httpService.ts:2240-2273`）

`1` 文本、`3` 图片、`34` 语音、`43` 视频、`47` 动画表情、`48` 位置、`42` 名片、`50` 语音/视频通话、`10000` 系统、`49` 复合（`mapType49`）、`244813135921` 引用、`266287972401` 拍一拍、`8594229559345` 红包、`8589934592049` 转账；其余 `OTHER`。

### 2.6 SSE 推送（`GET /api/v1/push/messages`）

- 帧格式（`broadcastMessagePush` `httpService.ts:326-346`）：`id: <n>\nevent: <name>\ndata: <json>\n\n`；`event` 名取自 payload 的 `event` 字段（`httpService.ts:348-349`）。
- 事件类型：`message.new`（新消息，`messagePushService.ts:726`、`741`）、`message.revoke`（撤回，`messagePushService.ts:1029`、`1044`）。
- payload 字段：`event`、`sessionId`、`sessionType`（`group|official|private|other`，`messagePushService.ts:1167-1178`）、`rawid`、`avatarUrl`、`groupName`（仅群聊）、`sourceName`、`content`、`timestamp`（秒）。
- 断线重连回放：缓冲 ≤1000 条 / TTL 10 分钟（`httpService.ts:146-147`）；心跳 25 秒写 `: ping\n\n`（`httpService.ts:525-540`）。
- 撤回事件 content 形如：`对方撤回了一条消息（rawid：<id>） 内容为"<原文>"`（`messagePushService.ts:1015`）。

## 3. 数据库层（wcdb_api.dll）

- **作用**：WCDB（微信的 SQLite 封装）原生库的 API 层。JS 侧用 `koffi` 做 FFI：`require('koffi')`（`wcdbCore.ts:761`）→ `koffi.load(dllPath)`（`wcdbCore.ts:813`）。
- **谁加载**：主进程/worker 里的 `wcdbCore` 类；DLL 路径解析 `getDllPath()`（`wcdbCore.ts:332-381`）：
  - 库名：`wcdb_api.dll` / `libwcdb_api.dylib` / `libwcdb_api.so`（`wcdbCore.ts:336`）；
  - 平台目录 `macos` / `linux` / `win32`，架构目录 `universal` / `x64`（arm64 时 `arm64`）（`wcdbCore.ts:337-339`、`357`）；
  - 主候选 `wcdb/<platformDir>/x64/<lib>`（`wcdbCore.ts:361`），并保留旧目录兼容（`wcdbCore.ts:371`）；
  - 资源根候选：`process.resourcesPath`、`process.resourcesPath/resources`、配置的 `resourcesPath`、`join(process.cwd(),'resources')`（`wcdbCore.ts:347-354`）。
- **树内支持平台目录**：`resources\wcdb\win32\x64\{wcdb_api.dll, wcdb_api.dll.pristine, WCDB.dll, SDL2.dll}`、`resources\wcdb\win32\arm64\{wcdb_api.dll, WCDB.dll}`、`resources\wcdb\macos\universal\{libwcdb_api.dylib, libWCDB.dylib}`、`resources\wcdb\linux\x64\libwcdb_api.so`。旁路原生件：`resources\image\win32\x64\img_helper.dll`、`resources\key\win32\x64\wx_key.dll`、`resources\welive\win32\x64\welive.exe`、`resources\wedecrypt\win32\x64\weflow-image-native-win32-x64.node`。
- **依赖预加载**：需先加载同目录 `WCDB.dll`、`SDL2.dll`（`wcdbCore.ts:772-812`；独立验证脚本 `scripts\probe_weflow_policy_version.py:19-25` 用 `os.add_dll_directory` + `ctypes.WinDLL` 预加载）。
- **初始化顺序**（`wcdbCore.ts:813-870`）：`koffi.load` → `InitProtection(const char* resourcePath)`（`wcdbCore.ts:818`，候选资源路径 `wcdbCore.ts:824-827`）→ `wcdb_init()`（`wcdbCore.ts:869`，`int32`）。
- **只读边界**：**未确认** WeFlow 是否存在写微信库的代码路径（未逐行审计 `wcdbCore.ts` 5001 行里的 open/参数与所有 SQL）。已知：它读 `session.db` 等消息库（`-3002` 提示「未找到 session.db」，`wcdbCore.ts:386`）；`-3` 为 `WCDB_STATUS_NO_MESSAGE_DB`（消息库缓存为空，`wcdbCore.ts:3922`）。**不要把「只读」当作已验证结论对外承诺。**

## 4. 二进制补丁（关键）

目标：`resources\wcdb\win32\x64\wcdb_api.dll`，**1,387,008 字节，PE 时间戳 2026-07-07**（v5.0.0 树）。原始备份：同目录 **`wcdb_api.dll.pristine`**。

| # | 文件偏移 | 原字节 | 新字节 | 语义 | 修掉的码 |
|---|---|---|---|---|---|
| ① | `0x80dc1` | `7F A2 BD 6A` = `0x6ABDA27F` = **2026-09-30 23:59:59Z** | `FF 8C 6D 7F` = **2137967999** = 2037-09-30 23:59:59Z | `InitProtection` 的「构建日 + 15 天」硬时限常量 | `-101` |
| ② | `0xE858D` | `7E 00 00 00`（mktime 的 `tm_year = 126` → 2026） | `89 00 00 00`（`tm_year = 137` → 2037） | `wcdb_init` 内部 mktime 年份上限 | `-1000` |

**错误码语义**

| 码 | 触发条件 | 出处 |
|---|---|---|
| `-101` | `InitProtection` 保护闸门拒绝：超过「构建日 + 15 天」 | 补丁①前实测 |
| `-102` | 改系统时钟后的码（时钟回拨被识破） | 多源交叉校验实测 |
| `-103` | 对 `0x113d10–0x115600` 所有跳转 NOP 变体尝试后仍失败（控制流混淆） | 实测 |
| `-1000` | `wcdb_init` 失败（mktime 年份越界） | 补丁②前实测 |
| `-105` | `native runtime policy mismatch`：`wcdb_runtime_policy_version()` ≠ 魔数 `1464206081`（`0x57440001`）；实测为 `%LOCALAPPDATA%\WeFlow\Runtime\anchor-v7-*.bin` 被写成全 0，与 `State\native-anchor-v7-*.bin` 不匹配 | `scripts\probe_weflow_policy_version.py:16`；`docs\SETUP.zh-CN.md:189` |
| `-2301` | 动态库加载失败 | `wcdbCore.ts:389` |
| `-2302` | WCDB 初始化异常 | `wcdbCore.ts:390` |
| `-2303` | WCDB 未能成功初始化 | `wcdbCore.ts:391` |
| `-3001` | 未找到数据库目录（`db_storage`） | `wcdbCore.ts:385` |
| `-3002` | 未找到 `session.db` | `wcdbCore.ts:386` |
| `-3003` | 数据库句柄无效 | `wcdbCore.ts:387` |
| `-3004` | 恢复数据库连接失败 | `wcdbCore.ts:388` |

- JS 侧文案表只覆盖 `-3001…-3004`、`-2301…-2303`，其余码原样回显 `操作失败，错误码: <code>`（`wcdbCore.ts:393-394`）——所以 `-101/-102/-103/-1000/-105` 是**原生 DLL 返回的裸码**。
- **打补丁**：在 `wcdb_api.dll` 里按上表两个偏移各改 4 字节（先备份为 `.pristine`）。**还原**：`copy /y wcdb_api.dll.pristine wcdb_api.dll`。自动化脚本见 `patches\weflow\apply_wcdb_patch.py`。
- **6.3.2 不可修**：`C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64\wcdb_api.dll` 为 **1,651,712 字节、PE 构建 2026-10-01**，同样带「构建日 + 15 天」硬时限且做了控制流混淆——NOP 全部跳转变体只得到 `-103`，改时钟只得到 `-102`，改 PE `TimeDateStamp` 无效。因此以 v5.0.0 树替代。
- **5.0.0 与 6.3.2 不可同时运行**：共用 `%APPDATA%\weflow` 与同一微信 DB 句柄。

## 5. 与微信客户端的交互

- **DB 根 + wxid 来自配置**：`dbPath`、`myWxid`（`electron/services/config.ts:35,37,199,201`）；账号目录由 `config.ts:1041-1060` 的 `getAccountDir(dbPath?, wxid?)` 解析，另有 `electron/services/dbPathService.ts`、`electron/services/accountDirResolver.ts`；三者都注明「修复 #996（错误码 -3001）」——会扫描 `dbPath` 下所有子目录，**不要自行 `join(dbPath, wxid)`**（`config.ts:1005`、`config.ts:1028`）。
- **实测微信数据根**：`C:\Users\<YOU>\Documents\xwechat_files`（`scripts\wechat_dat_decode.py:36`）。图片落点：`...\xwechat_files\wxid_*\msg\attach\<hash>\<yyyy-mm>\Img\<md5>.dat`（`scripts\wechat_dat_decode.py` 文件头注释）。
- **微信是否必须在运行**：需要。`-3002` 文案即「请确认微信已登录并且数据目录完整」（`wcdbCore.ts:386`）；WeFlow 的图片 hook（`img_helper.dll`）本质是截微信自身的下载动作，微信不在跑就没有新图缓存。
- **版本/时间窗限制**：未确认存在微信版本强校验；但**补丁 DLL 本身有「构建日 + 15 天」时限**（`-101` 的根因，已由补丁①推到 2037）。验证环境为微信 4.1.15.13（`docs\VERIFICATION.md:5`）。

## 6. 图片与语音附件

- **导出目录**：`<configService.getCacheBasePath()>\api-media\<sanitizeFileName(talker)>`（`httpService.ts:1653-1655`、`1707-1708`）；`sanitizeFileName` 把 `<>:"/\|?*` 与控制字符替成 `_`（`httpService.ts:1657-1663`）。`/api/v1/messages?media=true` 的响应里 `media.exportPath` 就是该路径（`httpService.ts:1132/1147`），逐条 `mediaUrl`/`mediaLocalPath` 指向导出结果。
- **写文件策略**：`writeFileIfLarger`（`httpService.ts:1683-1695`）——已存在且不比新数据小就跳过，避免高清图被缩略图覆盖。
- **图片格式**：`detectImageExt`（`httpService.ts:1671-1681`）支持 jpg(`FF D8 FF`)/png(`89 50 4E 47…`)/gif(`GIF87a|GIF89a`)/webp(`RIFF….WEBP`)/bmp(`BM`)，兜底 `.jpg`。解密侧白名单 `.gif/.png/.jpg/.jpeg/.webp`（`imageDecryptService.ts:2148-2149`、`1586`），MIME 表 `imageDecryptService.ts:2123-2131`。
- **ffmpeg 定位**：`getStaticFfmpegPath()`（`electron/services/imageDecryptService.ts:13-50`）依次尝试 ① `require('ffmpeg-static')` 返回值并把 `app.asar` 改成 `app.asar.unpacked`（`:20-24`）；② `join(process.cwd(),'node_modules','ffmpeg-static','ffmpeg.exe')`（`:32`）；③ `<resourcesPath>\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe`（`:40`）；`getFfmpegPath()`（`:2435-2446`）检测不到就回退系统 `'ffmpeg'`。
- **HEVC / wxgf（微信图片格式）**：`unwrapWxgf`（`imageDecryptService.ts:2277-2315`）识别魔数 `wxgf`（`:2281`），用 ffmpeg 把内部 HEVC 转 JPG：`convertHevcToJpg`（`:2450-2490`）依次尝试 `-f hevc -i`（第 0 帧）、`-vf select=eq(n\,1)`、`-vf select=eq(n\,5)`，`spawn` 单次 15 秒超时（`:2518-2527`）。
- **ffmpeg 修复要点（`docs\WEFLOW-IMAGE-FFMPEG-FIX.md`）**：`ffmpeg-static` 的 npm postinstall 下载从未执行，`node_modules\ffmpeg-static\` 里没有 `ffmpeg.exe` → WeFlow 解不出 HEVC/wxgf 图 → 入向图片导出全失败。修法：`start-weflow50.cmd` 每次启动前从已安装的 WeFlow 6.3.2 树复制 `ffmpeg.exe`。bridge 侧配套：取图重试 `_fetch_wechat_image(attempts=5, delay=1.5)`，失败才降级为文字（实现 `akasha-bridge\bridge_core.py:479-484`）。
- **微信 4.x `.dat` 直解（绕过 WeFlow）**：`scripts\wechat_dat_decode.py` —— 文件头 6 字节后整段单字节 XOR，暴力 `key(0..255) × offset(6/0/8/4/2)` 并用魔数表（jpg/png/gif/bmp/webp/tif）双次确认；跳过 `_t/_h/_b` 缩略图副本；输出到 `logs\dat-decode`。背景：WeFlow 只对「已导出过」的图给 `mediaUrl`，`type=3` 普通图经常没有，`type=47` 表情基本都有。
- **语音**：`Message` 结构里有 `voicePath`（`electron/services/export/core/ExportContext.ts:2750` 引用 `msg?.voicePath`）；**未确认** HTTP API 是否暴露语音下载/转写端点。转写语言配置 `transcribeLanguages` 默认 `['zh']`（`config.ts:60`、`config.ts:219`），存在 `electron/transcribeWorker.ts`。

## 7. 已知限制与排障表

| 现象 | 原因 | 处理 |
|---|---|---|
| `/api/v1/sessions` 返 500，日志 `InitProtection rc=-101` | DLL「构建日 + 15 天」硬时限 | 用已补丁①的 v5.0.0 树（`start-weflow50.cmd`）；6.3.2 不可修 |
| `wcdb_init` 返 `-1000` | mktime `tm_year=126` 年份上限 | 补丁② |
| 改系统时钟或 NOP 跳转补丁后得 `-102` / `-103` | 时钟回拨检测 / 控制流混淆 | 不要走这条路，回到补丁①+② |
| 5031 不监听，日志 `native runtime policy mismatch value=-105` | `%LOCALAPPDATA%\WeFlow\Runtime\anchor-v7-*.bin` 被非正常关机写成全 0，与 `State\native-anchor-v7-*.bin` 不匹配（**不是时间到期**） | `pwsh -File scripts\weflow-anchor-check.ps1 -Fix`（停 WeFlow → 备份 → 移走全 0 文件 → 重启，DLL 重建 anchor）；体检不带 `-Fix` |
| `-3001` 未找到数据库目录 | `dbPath` 选错 / 账号目录解析错 | 选包含 `wxid_*` 子目录的微信数据根；用 `getAccountDir` 逻辑，别自行拼 `join(dbPath, wxid)` |
| `-3002` 未找到 `session.db` | 微信未登录 / 数据目录不完整 | 登录微信并确认目录完整 |
| 端口 5031 被占用 | 6.3.2 与 5.0.0 同时运行，或残留 electron 进程 | 只留一个版本；杀掉旧进程（两者共享 `%APPDATA%\weflow` 与 DB 句柄） |
| API 返回 401 | `httpApiToken` 未配置或 token 不匹配 | 从 `%APPDATA%\weflow\WeFlow-config.json` 的 `httpApiToken` 取值；同样写进 `akasha-bridge\config.json` 的 `access_token` |
| API 返回 403（`/api/v1/push/messages`） | `messagePushEnabled !== true` | 置 true（`httpService.ts:544`） |
| 图片没有 `mediaUrl`，bridge 降级成文字 | 会话不在 `autoDownloadHighRes` 白名单 / `ffmpeg.exe` 缺失 / cwd 不是树根 | 跑 `scripts\weflow_add_autodownload.py --apply`（**先退 WeFlow 再写，再启动**）；补 `ffmpeg.exe`；用启动器保证 cwd |
| 收消息 API 全挂、bridge 报 `❌ 无法连接 WeFlow` | WeFlow 没起来 / 5031 挂了 | 看 `wcdb.log` 是否出现 `open ok handle=1`，用 `preflight.ps1` 查 5 个端口 |
| 6.3.2 装不上补丁 | 1,651,712 B，PE 2026-10-01，构建日+15 天 + 控制流混淆 | 放弃 6.3.2，用 v5.0.0 树 |

## 8. 调用方集成

- **整体链路**（`docs\SETUP.zh-CN.md:18`、`docs\VERIFICATION.md:5`）：
  `微信客户端 ← WeFlow(127.0.0.1:5031) ──SSE──> akasha-bridge(8766) ←反向 WS─ AstrBot(OB11 11229 / 面板 6185) → 发送后端 wxui（scripts\wxui.py）→ 微信`
  **5031 的位置**：唯一的数据读取入口，只负责「读消息 + 推送新消息」；发送不经过 WeFlow（走 UI 自动化 `wxui`）。`docs\SETUP.zh-CN.md:263`：5031 不监听 → bridge 每 30 秒 `❌ 无法连接 WeFlow（30s 后重试）`，微信收发全停。
- **bridge 配置**：`akasha-bridge\config.json` → `akasha-bridge\config.py:22-23`（`WE_FLOW_BASE_URL = config["weflow_base_url"]`、`ACCESS_TOKEN = config["access_token"]`）；面板字段名 `weflow_base_url` / `access_token`（`akasha-bridge\web_panel.py:310-311`）。未配置 token 时 bridge 报 `❌ 未配置 access_token：WeFlow 消息接收暂停`（`akasha-bridge\main.py:117`）。
- **真实调用点**：
  - 自检：`GET {WE_FLOW_BASE_URL}/api/v1/sessions?limit=1&access_token={ACCESS_TOKEN}`（`akasha-bridge\main.py:126`）。
  - 会话列表：`requests.get(f"{WE_FLOW_BASE_URL}/api/v1/sessions", params={"access_token": ACCESS_TOKEN}, timeout=10)`（`akasha-bridge\bridge_core.py:57-59`）。
  - SSE 订阅：`f"{WE_FLOW_BASE_URL}/api/v1/push/messages?access_token={ACCESS_TOKEN}"`，请求头 `Accept: text/event-stream`、`Cache-Control: no-cache`，`stream=True, timeout=None`，按行解析 `data:` 前缀（`bridge_core.py:372-393`）；日志里 token 会被正则脱敏成 `***`（`bridge_core.py:376`）。
  - 取消息：`GET /api/v1/messages`，`params={"access_token":…, "talker": talker, "media": "true", "limit": limit}`（默认 `limit=6`，`bridge_core.py:452-467`）。
  - 下载图片：`dl_url = f"{media_url}{sep}access_token={ACCESS_TOKEN}"`（`bridge_core.py:514`），即 `mediaUrl` 上追加 token。
  - 选图逻辑：只挑 `mediaType in _MEDIA_TYPES 且 mediaUrl` 的消息，优先 `isSend` 为假（别人发来的）（`bridge_core.py:469-477`）。
- **探测/运维脚本**（`scripts\`）：
  - `weflow_sessions.py`：`GET http://127.0.0.1:5031/api/v1/sessions?access_token=<token>&limit=80`，打印会话字段并顺带读 `%APPDATA%\weflow\WeFlow-config.json` 的 `autoDownloadHighRes`/`autoDownloadWhitelist`/`dbPath`/`myWxid`。
  - `weflow_last_messages.py <talker> [limit]`：`GET /api/v1/messages?access_token=…&talker=…&media=true&limit=…`，打印 `localId/isSend/localType/createTime/content/mediaType/mediaFileName/mediaUrl/mediaLocalPath`。
  - `weflow_add_autodownload.py [--apply]`：把私聊 wxid 并入 `autoDownloadWhitelist` 并置 `autoDownloadHighRes=true`，写前备份为 `WeFlow-config.json.bak-<时间戳>`；跳过 `filehelper` 与 `gh_` 前缀。
  - `weflow_imgdiag.js`：在 Electron 运行时里复现 `imageDownloadService` 三步（`require('koffi')` → `getDllPath()` 存在性 → `load` + `InitImgHelper` 可用性），`koffi.load` 失败时打印 `GetImgHelperError()`，定位 hook 静默失败原因。
  - `wechat_dat_decode.py`：绕过 WeFlow 直解微信 `.dat` 图片（见第 6 节）。
  - `probe_weflow_policy_version.py`：直接 `ctypes` 调 `wcdb_runtime_policy_version()`，期望 `1464206081`（`0x57440001`），`-101` 表示被拒；退出码 0=通过 / 1=被拒。
  - `start-weflow50.cmd`：唯一受支持的启动入口。
- 冒烟命令：`GET http://127.0.0.1:5031/api/v1/sessions?limit=5&access_token=<token>` → HTTP 200；成功后 bridge 日志出现 `✅ WeFlow API 正常` → `✅ 已连接到 WeFlow 推送`。

---

**未确认清单（不得当作结论）**：SNS 系列各端点参数/响应字段；`/api/v1/contacts`、`/api/v1/group-members` 的完整字段；语音附件的 HTTP 暴露情况；WeFlow 是否严格只读访问微信库；微信版本号是否有硬校验。
