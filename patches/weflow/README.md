# WeFlow `wcdb_api.dll` 时间闸门补丁

## 症状

WeFlow 启动后窗口正常，但 HTTP API（`127.0.0.1:5031`）返回 500，日志里出现：

```
InitProtection rc=-101
```

或（第二道闸门）：

```
wcdb_init rc=-1000
```

表现是 `GET /api/v1/sessions` 永远失败，akasha-bridge 里每 30 秒刷一次
`❌ 无法连接 WeFlow（30s 后重试）`，微信收发全停。

## 原因

`wcdb_api.dll` 里有「**构建日 + 15 天**」的硬编码时间闸门：

| 库版本 | 大小 | PE 构建日 | 可用到 | 可否修补 |
|---|---|---|---|---|
| **5.0.0**（本仓库 `weflow/`） | 1,387,008 B | 2026-07-07 | 补丁前 2026-09-30；补丁后 2037-09-30 | ✅ 只需 2 处 4 字节 |
| 6.3.2（`C:\Program Files\WeFlow\...`） | 1,651,712 B | 2026-10-01 | 2026-10-16 之后即失效 | ❌ 控制流混淆，不可修 |

6.3.2 的 DLL 对 `0x113d10–0x115600` 区间做了控制流混淆：把该区间内所有条件跳转改成 NOP
的所有变体都只能得到 `-103`；改系统时钟会得到 `-102`（时钟回拨检测）；改 PE
`TimeDateStamp` 无效。**不要**在这条路上浪费时间。本仓库因此直接携带 5.0.0 源码树。

## 补丁内容

目标文件：`weflow/resources/wcdb/win32/x64/wcdb_api.dll`

| # | 文件偏移 | 原字节 | 新字节 | 语义 | 修掉的码 |
|---|---|---|---|---|---|
| ① | `0x80DC1` | `7F A2 BD 6A` = `0x6ABDA27F` = 2026-09-30 23:59:59 UTC | `FF 8C 6D 7F` = 2137967999 = 2037-09-30 23:59:59 UTC | `InitProtection` 的时间闸门常量 | `-101` |
| ② | `0xE858D` | `7E 00 00 00`（mktime 的 `tm_year = 126` → 2026） | `89 00 00 00`（`tm_year = 137` → 2037） | `wcdb_init` 内部 mktime 的年份 | `-1000` |

原始 DLL 的备份命名为同目录 `wcdb_api.dll.pristine`（本仓库已随包提供一份）。

## 用法

```powershell
# 检查状态（不改文件）
python patches\weflow\apply_wcdb_patch.py --check weflow
python patches\weflow\apply_wcdb_patch.py --check "weflow\resources\wcdb\win32\x64\wcdb_api.dll"

# 打补丁（首次会自动备份 .pristine）
python patches\weflow\apply_wcdb_patch.py --apply weflow

# 还原
python patches\weflow\apply_wcdb_patch.py --revert weflow
```

手工还原也行：

```cmd
copy /y "weflow\resources\wcdb\win32\x64\wcdb_api.dll.pristine" "weflow\resources\wcdb\win32\x64\wcdb_api.dll"
```

## 如何验证补丁生效

1. 启动 WeFlow：`scripts\start-weflow50.cmd`（**不要**直接双击 `electron.exe`；该启动器会清掉
   `ELECTRON_RUN_AS_NODE`、补 `ffmpeg.exe`、并把 cwd 切到树根）。
2. 看 WeFlow 的 `wcdb.log`，应出现：

   ```
   open ok handle=1
   ```

3. 冒烟测试（把 `<token>` 换成 `%APPDATA%\weflow\WeFlow-config.json` 里的 `httpApiToken`）：

   ```powershell
   curl.exe "http://127.0.0.1:5031/api/v1/sessions?limit=5&access_token=<token>"
   ```

   应返回 HTTP 200 + 真实会话列表。

4. akasha-bridge 日志应出现 `✅ WeFlow API 正常` 与 `✅ 已连接到 WeFlow 推送`。

## 另一个坑：`-105`（不是时间到期）

如果 5031 完全不监听、日志里是：

```
native runtime policy mismatch value=-105
```

那不是时间闸门，而是锚点文件被非正常关机写坏了：

- `%LOCALAPPDATA%\WeFlow\Runtime\anchor-v7-*.bin` 变成全 0
- 与 `State\native-anchor-v7-*.bin` 不匹配

处理方式（停 WeFlow → 备份 → 移走全 0 文件 → 重启，让 DLL 重建 anchor）：

```powershell
pwsh -File scripts\weflow-anchor-check.ps1 -Fix
```

不带 `-Fix` 只做体检。

## 注意事项

- **5.0.0 与 6.3.2 不能同时运行**：两者共用 `%APPDATA%\weflow` 与同一份微信数据库句柄，
  会互相抢锁。用 5.0.0 前先确保 `C:\Program Files\WeFlow\` 那套已退出。
- 补丁只改 8 个字节，不改变任何逻辑，只是把常量往后推。DLL 的其余部分完全不动。
- 该补丁属于对 WeFlow 的修改。WeFlow 采用 **CC BY-NC-SA 4.0**（署名—非商业—相同方式共享），
  再分发时请保留署名并遵守其条款，详见仓库根的 `THIRD-PARTY.md`。
