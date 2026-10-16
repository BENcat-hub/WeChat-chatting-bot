# 机器人“看不了图”的根因与修复（WeFlow 5.0.0 · ffmpeg 缺失）

日期：2026-10-16　状态：**已修复并验证**（等用户实发一张图做端到端确认）

## 症状

- 对方发来图片，AstrBot 侧收到的是占位文本 `[图片: （图片内容无法描述）]`，机器人回复“图打不开”。
- bridge 日志：`⏳ 图片还没有本地缓存（等 WeChat 下载，第 N/5 次）` → `消息列表无图片 mediaUrl (talker=…)` → `⚠️ 取图失败，图片降级为文字`。
- WeFlow API 返回的消息里，入向图片（`localType=3, isSend=0`）**没有** `mediaType` / `mediaUrl` / `mediaLocalPath`，而机器人自己发出的图片有。
- 与图片大小**无关**：`length≈293 KB` 与 `length≈294 KB` 的照片同样失败，成功过的只有微信表情（`localType=47`）与机器人自己发出的图。

## 排查路径（结论：不是大小问题）

1. 对照 API：入向 `localType=3` 无 mediaUrl；出向图片和 emoji 有 mediaUrl → 问题在 WeFlow 导出这一步。
2. 读 WeChat 附件目录：`…\msg\attach\<hash>\2026-10\Img\*.dat` 里**缩略图/mid 都在**（`.dat` 163,780B、`_h.dat` 158,683B 等），但原图 `hdlength=12,646,562`（12 MB）不在 → 微信没有下载原图，WeFlow 得从缩略图/mid 解。
3. 读 `electron/services/httpService.ts:1744-1813exportMediaForMessage`：`localType===3 && exportImages` 时调 `imageDecryptService.decryptImage({... hardlinkOnly: true, preferFilePath: true})`，失败再 `resolveCachedImage(hardlinkOnly: true)`。
4. 读 `electron/services/imageDecryptService.ts`：`resolveDatPath` 的 `hardlinkOnly: true` 仍会兜底到文件系统扫描（`allowFilesystemScan = options?.allowFilesystemScan !== false`）；拿到 `.dat` 后要 `unwrapWxgf()` + `convertHevcToJpg()`。
5. **命中根因**：`getStaticFfmpegPath()`（`imageDecryptService.ts:13-40`）在未打包时取 `join(process.cwd(), 'node_modules', 'ffmpeg-static', 'ffmpeg.exe')`，而 5.0.0 回收站树里的 `node_modules\ffmpeg-static\` **只有 JS**（`index.js`/`install.js`/`package.json`…，npm postinstall 的二进制下载从未跑过，`ffmpeg-static@5.3.0` / b6.1.1）→ HEVC/wxgf 解不出来 → 该条消息导出失败。
   微信收到的照片是 HEVC 容器（`.dat` 头部 `07 08 56 32 08 07`，微信自有 `wxgf`），**必须**经 ffmpeg 转码，浏览器/普通图片库都读不了。

## 修复

```powershell
# 从 6.3.2 安装目录取 ffmpeg（82,797,568 B，MD5 25243548BE231A3C928CB7A0B6225AE9）
$src = 'C:\Program Files\WeFlow\resources\app.asar.unpacked\node_modules\ffmpeg-static\ffmpeg.exe'
$dst = '<WEFLOW_ROOT>\node_modules\ffmpeg-static\ffmpeg.exe'
Copy-Item $src $dst -Force
```

- ffmpeg 路径是**每次调用时现算**的，所以不用重启 WeFlow 就生效。
- 其他可用来源（备用）：`%TEMP%\weflow-asar\node_modules\ffmpeg-static\ffmpeg.exe`（同 82,797,568 B）、
  `%LOCALAPPDATA%\Programs\Trae CN\resources\app\bin\ffmpeg.exe`（7 MB）、`%APPDATA%\bilibili\ffmpeg\ffmpeg.exe`（43 MB）。

## 验证结果

| 消息 | XML 里的 md5 | 修复前 | 修复后 |
| --- | --- | --- | --- |
| localId 231（照片 293 KB） | `1be980f455272ad91480c6d5ef8c52ed` | 无 mediaUrl | `…/api/v1/media/wxid_CONTACT_A/images/1be980f4….jpg` 507,029 B |
| localId 235（照片 294 KB） | `1be980f455272ad91480c6d5ef8c52ed` | 无 mediaUrl | 同上 |
| localId 237（照片 295 KB） | `cd914280a19d0c766e1cb8f0529ac24b` | 无 mediaUrl | `…/cd914280….jpg` 507,029 B |

- Pillow 校验：JPEG **1280×1706** RGB，可正常解码；另外两张导出为 PNG 1024×1024 / 512×512。
- 全链路（bridge 取图 → AstrBot 识图 / 生图参考图）**待用户实发一张图确认**。

## 防复发（已写进启动脚本）

- `scripts/start-weflow50.cmd`：启动前若 `node_modules\ffmpeg-static\ffmpeg.exe` 不存在，就从 6.3.2 安装目录 `copy /y` 过去。
- `scripts/start-all.ps1` 的 `[2/4] WeFlow` 段：同样的存在性检查 + 复制，并打印警告。
- 已测试：把 ffmpeg.exe 临时移走 → 启动脚本输出 `[weflow50] restoring ffmpeg.exe (HEVC/wxgf image decode)...` → 恢复后 82,797,568 B / MD5 一致。

## 相关文件

- `wechat-ai-stack\akasha-bridge\bridge_core.py`：取图重试（`_query_messages_with_media` / `_pick_media_message` / `_fetch_wechat_image(attempts=5, delay=1.5)`），失败才降级为文字。
- `wechat-ai-stack\scripts\weflow_add_autodownload.py`：往 `autoDownloadHighResWhitelist` 补会话（本次已补齐 7 个）。
- `wechat-ai-stack\scripts\weflow_last_messages.py <talker> [limit]`、`weflow_sessions.py`：查消息与识图状态。
- `wechat-ai-stack\scripts\inspect_hardlink_db.py`：hardlink.db 只读检查（该库是加密的，外部 sqlite 打不开，属正常）。
