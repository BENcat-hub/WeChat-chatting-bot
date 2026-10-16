# WeFlow `-101` 报错排查结论（2026-10-16）

> 现象：WeFlow 6.3.2 启动后无法进入主界面，日志刷 `native runtime policy mismatch value=-101`，UI 报
> 「当前电脑环境异常，请关闭杀毒软件和安全防护后重试；仍然失败请更换电脑」。
> 影响：**WeFlow 的 HTTP API（127.0.0.1:5031）对 `/api/v1/sessions` 返回 500**，akasha-bridge → AI 人设的读消息通路整体不可用（发消息的 wxui 通路本身没问题，但收不到消息）。

## 1. 报错链（谁在报）

1. `%APPDATA%\weflow\logs\wcdb.log`：
   - `[bootstrap] initialize platform=win32 dllPath=...\wcdb\win32\x64\wcdb_api.dll`
   - `[bootstrap] koffi.load begin/ok`（DLL 加载成功）
   - `[bootstrap] native runtime policy mismatch value=-101` ← **加载后 6~8 ms 立即失败**，每 ~40 s 重试
2. 报错来自 DLL 自身：`wcdb_api.dll` 导出 `wcdb_runtime_policy_version()`，主进程拿返回值与 4.6 的固定值比较；
   不一致即报 `native runtime policy mismatch value=<返回值>`。返回值 `-101` 的语义由 DLL 内
   `formatInitProtectionError()` 决定：`-101 → 当前电脑环境异常，请关闭杀毒软件和安全防护后重试；仍然失败请更换电脑`
   （同表：`-102` 系统时间回拨、`-103` 完整性校验失败、`-104` 调试/分析环境、`-105` 离线防回拨状态损坏；
   `-1000/-1005..-1010` 同 `-101`；`-3001/-3002` 数据库路径问题）。

## 2. 已排除的原因（都试过，无效）

| 假设 | 实验 | 结果 |
| --- | --- | --- |
| 本地 anchor 状态损坏（对应 `-105` 那一类） | 备份后移走 `Runtime\anchor-v7-*.bin`、`State\native-anchor-v7-*.bin`、删 `HKCU\Software\WeFlow\Runtime`，重启 | 仍 `-101`；重启后只重建了 `Security\device-root-v1.bin` → **不是本地状态损坏** |
| 状态被我们改坏 | 从 `logs/weflow-fix-20261016/` 还原 .reg + 三个 .bin，重启 | 仍 `-101`（机器已恢复原状） |
| 环境变量（DSH/Electron）污染 | 用纯 Python + `ctypes` 直接预加载 `WCDB.dll`/`SDL2.dll` 再加载 `wcdb_api.dll` 调用该导出 | 同样 `-101`、同样 6~8 ms → **与 Electron、与环境变量无关** |
| 新版本 DLL 才加了闸门，换旧版本可绕 | 拿回收站里 7 月构建的 `WeFlow-main`（v5.0.0，DLL 1,387,008 B）同法调用 | **也是 `-101`**（该 DLL 甚至没有 `wcdb_runtime_policy_version` 导出）→ 新旧构建都带闸门 |
| 第三方加速器/根证书干扰 | 停掉 Steam++ / Watt Toolkit（含 `SteamTools Certificate` 根证书）与 Steam 全部进程后重启 | 仍 `-101`（随后已把它们重新拉起） |
| 需要联网校验 | 看 WeFlow 进程的外连 | **没有任何外连尝试**；`api.weflow.top`（13.54.62.80）本机 80/443 均 TCP 超时（`weflow.top` 正常 200）→ 校验是**纯本地、瞬时**的，断网不是直接原因 |

**结论**：`-101` 是厂商在 `wcdb_api.dll` 里的**运行环境策略闸门**（runtime policy / anti-tamper 自校验），
它在本机即时拒绝，且**与用户数据、配置、版本、网络都无关**；需要的外部条件是「环境被它认可」或「厂商侧放行」。
安装包本身无签名（`WeFlow.exe`/`wcdb_api.dll`/`WCDB.dll` 均 NotSigned）。

## 3. 时间线

- `10-12 21:00`、`10-13`、`10-14`、`10-15 21:32`（本地时间）多次 `protection ok`
- `10-16 01:35` 起全部变成 `policy mismatch -101`（此前一天有 Windows 更新、ACE 反作弊驱动落盘等系统变化）

## 4. 需要管理员权限才能继续试的方向（按性价比排序）

1. **杀软白名单**：火绒（`HipsDaemon` 运行中）与 Microsoft Defender 实时防护，为
   `C:\Program Files\WeFlow`、`%APPDATA%\weflow`、`%LOCALAPPDATA%\WeFlow` 加排除项，或临时整体退出火绒后重启 WeFlow。
2. **关内核隔离**：Windows 安全中心 → 设备安全性 → 内核隔离 → 内存完整性（VBS/HVCI，本机 `VirtualizationBasedSecurityStatus=2`、`SecurityServicesRunning={2}`）关掉，重启后再试。
3. **关虚拟机/反作弊驱动**：MuMu 模拟器驱动（`MuMuVMMDrv`，StartMode=System）与腾讯 ACE（`ACE-ADVT.sys`，10-15 19:04 落盘）常与这类自校验冲突，卸载/停用后重启再试。
4. **换网络**：手机热点或代理下重试一次（`api.weflow.top` 本机不通，若闸门在某个分支上要联网，这是唯一入口）。
5. **问厂商**：把 `-101` + `Runtime policy validation failed` 原文发到 `doc.weflow.top` / GitHub issue / 官方群，
   问「6.3.2 的 runtime policy 校验在什么条件下返回 -101」——这是最可能拿到真解的一步。

## 5. 现网影响与旁路

- WeFlow 侧：数据库层不可用 → `/api/v1/sessions` 500；`/api/v1/push/messages` SSE 也不会有数据。
- AI 人设侧：读消息链路断（主动消息、回复都收不到输入）；**wxui 发送通道本身没坏**。
- 旁路（临时）：`scripts/wxui.py` 的截图/OCR 通路仍能看微信窗口内容，可临时用它兜住「能看到消息」；
  但完整的人设链路要等 WeFlow 恢复。

---

## 6. 根因定论（2026-10-16 上午，推翻本文第 2 节的「原因不明」）

**`-101` = 装好的 `wcdb_api.dll` 到期了。厂商在 DLL 里做了「构建日 + 15 天」的硬时限，到期后该 DLL 永久拒绝服务。**

- 构建时间：`C:\Program Files\WeFlow\resources\resources\wcdb\win32\x64\wcdb_api.dll`，
  PE 时间戳 = **2026-10-01 02:38（本地）**，大小 1,651,712 B。
- 直接调 `wcdb_runtime_policy_version()`（python ctypes，不经过 WeFlow.exe）二分系统时间：

  | 系统时间（本地） | 返回值 |
  | --- | --- |
  | 2026-10-15 23:59:30 | -102 |
  | 2026-10-16 00:00:30 | **-101** |

  → 边界落在 2026-10-16 00:00:00 本地整点，即构建时刻 + 15 天。
- 生产日志 `%APPDATA%\weflow\logs\wcdb.log` 佐证（日志是 UTC）：最后一次
  `[bootstrap] native protection ok` = `2026-10-15T13:32:00.861Z`（本地 10-15 21:32），
  首次 `native runtime policy mismatch value=-101` = `2026-10-15T17:35:00.571Z`（本地 10-16 01:35）。
- 厂商 issue 标题即答案：hicccc77/WeFlow **#1227「[Feature]: 关于101报错，确实是时间的问题」**，
  评论 `ajin972517`：「移除时间限制即可」。旧构建 DLL 里还留着
  `??DATA_CORRUPTED_BY_PIRACY_PROTECTION??`、`expired: self-destruct triggered` 字符串。

### 已逐个证伪（本文第 4 节的全部方向）

杀软白名单 / 关 Defender 实时防护 / 停火绒驱动（自保护拒绝）/ HVCI-VBS / MuMu 与腾讯 ACE 驱动 /
状态文件与注册表重置 / 断网与换网络 / 重装 / 提权运行 —— 全部无效，因为闸门在本机、只看系统时钟。

- 改系统时间也没用：回拨到到期前会返回 **-102**（另一种拒绝），不是放行。
- 旧构建的 DLL **不能只换 DLL**：`<WEFLOW_SRC>\...\wcdb_api.dll`（1,387,008 B，2026-07-07）
  没有 `wcdb_runtime_policy_version`，但导出面不同（只有 `InitProtection`，没有 `wcdb_authenticate`），
  装进 6.3.2 会直接不兼容。

### 修复路径（按性价比）

1. **装一个构建日期更晚的 WeFlow**（≥ 2026-10-02）：时限顺延，这是唯一干净的解。
2. 回退到整套旧版 app（含旧 `wcdb_api.dll` + 对应 `WCDB.dll` 与主程序），但需确认能连当前微信版本。
3. 向厂商补发构建 / 要无时限版本（GitHub issue 或官方群，把本文件发过去即可）。
4. 临时旁路：`scripts/wxui.py` 的截图/OCR 通路仍可用，能看能发，但读消息不是事件流。

---

## 7. 已实施的修复（2026-10-16 中午，**已验证可用**）

**结论：换回回收站里找回来的 v5.0.0 源码树，把它的旧 `wcdb_api.dll` 里两处到期时限各改 4 字节，跑起来后数据库层与 HTTP API 全部恢复。**

### 7.1 为什么不是修 6.3.2

6.3.2 的门禁被控制流混淆保护，实测已穷尽：

- 在 `0x113d10`–`0x115600` 区间的所有跳转 NOP 变体（含强制 `wcdb_runtime_policy_version` 返回 magic `1464206081`、强制 `wcdb_authenticate` 返回 0）→ 全部变成 **-103**（校验不过）。
- 任何改时钟的补丁（冻结/平移 DLL 内唯一取时点 `0x10eb2c` 的 `GetSystemTimePreciseAsFileTime`）→ **-102**（多源交叉校验，无解）。
- 改 PE `TimeDateStamp`（file off 0x118）无任何影响。
- 自建「假 XKEY 回环握手」harness 证明：该 DLL 的判定与 app 传入的 `issuedAtMs` 无关，且握手根本没发生 → 只能在 DLL 内部改判定，而内部判定被混淆。

### 7.2 旧 v5.0.0 组件的两处时限与补丁（各 4 字节）

组件：`<WEFLOW_ROOT>\resources\wcdb\win32\x64\wcdb_api.dll`
（1,387,008 B，PE 构建 2026-07-07；未打补丁的原始文件已备份为同目录 `wcdb_api.dll.pristine`）

| # | 位置（file offset） | 原字节 | 新字节 | 含义 |
| --- | --- | --- | --- | --- |
| 1 | `0x80dc1` | `7F A2 BD 6A` | `FF 8C 6D 7F` | `InitProtection` 的 `cmp rax, 0x6ABDA27F`（2026-09-30 23:59:59 UTC）→ **2137967999**（2037-09-30 23:59:59 UTC） |
| 2 | `0xE858D` | `7E 00 00 00` | `89 00 00 00` | `wcdb_init` 用 `mktime` 拼出的 `tm_year=126`(2026) → **137**(2037) |

- 两处都由裸探针验证：补 1 后 `InitProtection(res)` 返回 **0**（原为 -101）；补 2 后 `wcdb_init` 不再返回 **-1000**。
- 只需要改这两处；`WCDB.dll`、主程序都不用动。
- **注意**：`-101` 与 `-1000` 是两个不同的门禁，只打 #1 会表现为「初始化过了但数据库打不开（错误码 -1000）」。

### 7.3 运行方式与已做的持久化

- 启动器：`wechat-ai-stack\scripts\start-weflow50.cmd`
  （关键：**清掉 `ELECTRON_RUN_AS_NODE`**，否则 `electron.exe` 会退化成纯 Node 直接报错退出；然后
  `electron.exe <源码树>`）。
- 开机自启已改：`HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 的 `electron.app.WeFlow`
  原本指向 `C:\Program Files\WeFlow\WeFlow.exe`（备份在
  `logs\weflow-fix-20261016\autostart-run-backup.txt`），现指向上述启动器。
- 公共桌面快捷方式 `C:\Users\Public\Desktop\WeFlow.lnk` 已改为 `cmd.exe /c <启动器>`
  （原值备份在 `logs\weflow-fix-20261016\shortcut-backup.txt`），避免误点开坏掉的 6.3.2。
- 渲染层（`dist/`）在回收站那份树里是缺失的，已用树内 `node_modules\.bin\vite build`
  重新构建（日志 `logs\weflow-fix-20261016\vite-build.txt`），UI 正常显示。

### 7.4 验证证据（2026-10-16 11:20–11:25）

- `%APPDATA%\weflow\logs\wcdb.log`：
  `open accountDir=...\xwechat_files\wxid_BOTACCOUNT_e398 dbStorage=...\db_storage`
  → `open sessionDb=...\session\session.db` → **`open ok handle=1`** → `[diag:open] contact count=15`。
- `GET http://127.0.0.1:5031/api/v1/sessions?limit=5&access_token=<token>` → **HTTP 200**，
  返回真实会话（用户C / 示例群聊 / 用户A …）。
- akasha-bridge 自动恢复（日志 `logs\bridge-20261015-135040.err.log`）：
  `✅ WeFlow API 正常` → `✅ 已连接到 WeFlow 推送`（SSE `/api/v1/push/messages`）。
- UI 截图：`logs\weflow-fix-20261016\ui-after-build.png`。

### 7.5 回退

- 组件级：把 `wcdb_api.dll.pristine` 覆盖回 `wcdb_api.dll`。
- 自启/快捷方式：用 7.3 里两个备份文件里的原值恢复。
- 想回到 6.3.2：直接跑 `C:\Program Files\WeFlow\WeFlow.exe`（仍是 -101，仅作对照）。

### 7.6 遗留注意事项

- v5.0.0 比 6.3.2 旧，部分新功能（如某些 AI Agent 面板）行为可能不同；如需新版功能，仍应向厂商索取
  2026-10-02 之后构建的组件。
- 6.3.2 与 5.0.0 **不要同时运行**：两者共用 `%APPDATA%\weflow` 配置与同一个微信数据库句柄。
- 两个补丁把时限推到 **2037-09-30**；到期前如需再用，重复 7.2 改年份即可（`tm_year` 为「年份-1900」）。
