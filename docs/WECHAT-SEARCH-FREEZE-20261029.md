# 微信「搜索定位导致卡死」修复 — 2026-10-29

> 承接：`HANDOVER-2026-10-28.md` 第 5 节未闭环 / `docs/WECHAT-WINDOW-FIX-20261027.md`。
> 用户原话（10-29 早）：「采用搜索栏查找聊天一定会导致微信冻结，请解决这个问题，并只将其
> 作为兜底方案，优先 ocr」。
>
> 本文记录三条根因（其中一条是**我们自己按的 Esc**）、修复、实测证据、配置现值与回滚方式。

---

## 0. 30 秒速览

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| 搜索后微信「卡死」 | **搜索面板已自动关闭后，代码又补按了一次 Esc**。微信 4.x 在没有覆盖层时按 Esc，会让 MMUI 渲染子窗停止重绘（消息不更新、点击/滚轮全无响应，但 WM_NULL 仍 9ms、IsHungAppWindow=False） | 搜索路径**删除全部 Esc**；清覆盖层改为 `dismiss_overlay()`（点聊天头部空白处）。`ui_escape()` 保留但标注禁用 |
| OCR 找不到「用户F」 | Windows OCR（winsdk）对 12px 的「用户F」在侧栏和聊天头部**任何缩放都读不出**（`丕忄尸` 或整行漏掉）；「示例群聊D」读成「测讠羊」 | 新增 RapidOCR（PP-OCRv4）常驻服务 `.venv-ocr`，1x 原图即读对（用户F score 0.91）。`ocr_engine=rapid` |
| 用户F的消息全被拦下 | `_identity_recheck` 把头部碎字「丕忄尸」当成「另一个像人名的会话」→ 判 `chat switched mid-send` 并清空输入 | 碎字（`_header_unreadable`）一律按「读不出」放行；RapidOCR 上线后头部直接读对「用户F」 |
| 搜索点进视频号 | 旧搜索路径按**固定坐标盲点**第一条，面板没开/没渲染时点进聊天区或网络结果 | 搜索只在整张侧栏 OCR 失败后才用；必须 OCR 读到「整串等于目标名」的联系人/群聊行才点；**禁止盲点**（`search_blind_click=false`） |
| 半冻结无法恢复 | minimize/restore、hide/show、InvalidateRect 都无效（实测） | 只有重启微信有效。新增「悬停是否重绘」探活 + 自愈重启（冷却/次数上限/用户在用键鼠时让路） |

验证：`文件传输助手` 真发 3 条全部 WeFlow 回读确认（localId 346/347/348）；`用户F` 搜索
路径 + 头部复核通过、搜索后 UI 仍然重绘（hover ratio 0.42）；`用户C`/`示例群聊D` 侧栏
OCR 定位 0.5~0.8 s。

---

## 1. 根因 A：Esc 键会冻结微信（决定性证据）

### 1.1 现象

「半冻结」：主窗可见、前台、未最小化；`SendMessageTimeoutW(WM_NULL)` **9 ms** 返回、
`IsHungAppWindow=False`；但侧栏/聊天区**不刷新、不接鼠标、滚轮不动**，只有原生搜索框能
接键盘。历史记录里它总出现在「用搜索定位过」之后，所以被归因成「搜索导致卡死」。

### 1.2 隔离实验（4 个 trial，`scripts/_exp_search_freeze_1029.py`）

判据：把光标移到会话行上，正常会整行高亮（灰度差像素占比 ~0.42）；冻结时 0.000。
每个 trial 前确认 alive=True，trial 后测 alive；判死就 `restart_wechat()` 复原。

| trial | 动作 | 结果 |
| --- | --- | --- |
| 0 | **只按一次 Esc**（没有搜索面板） | **冻结**（ratio 0.4259 → 0.0000） |
| A | 点搜索框 → 粘贴「用户F」→ 按 Esc | 正常（面板收起） |
| B | 点搜索框 → 粘贴 → 点联系人结果（**不按 Esc**） | 正常（微信自己收面板，头部=用户F） |
| C | 点搜索框 → 粘贴 → 点结果 → **再补一个 Esc** | **冻结** |

结论：**面板开着时 Esc 是安全的（收起面板）；面板没开时 Esc 落在聊天窗口上 → 冻结。**
旧 `open_chat_by_search()` 的收尾正是「点完结果 → sleep 1.4s → Esc」；点结果时微信已经把
面板收起来了，这一下 Esc 就打在聊天窗口上。所以每次搜索定位后（尤其随后还有发送/复核）
就容易进入半冻结。

> 口径说明：实验里的 Esc 是 `winio.chord(VK_ESCAPE)`（SendInput，虚拟键、无 scan code），
> 也就是自动化实际发出的那一种；**物理键盘按 Esc 是否同样触发未单独验证**。但对本系统而言
> 结论不变：自动化路径从此不再发送 Esc。

> 补充实测：minimize→restore、hide→show、`InvalidateRect+UpdateWindow` 三招都无法解除，
> 只有 `taskkill Weixin.exe` + 重启（约 6 s，自动登录点点一下）能恢复。

### 1.3 修复

* `wxui.open_chat_by_search()`：点中结果后**不再按 Esc**（面板会自己收）。
* 所有清理/收面板改走 `dismiss_overlay()`：点聊天头部空白处 `(0.464w, 0.084h)` ——
  面板开着时点在面板外面，微信自己收起；面板没开时点在标题栏空白处，无副作用。
* 失败路径（没认出行/头部复核不过）也用 `dismiss_overlay()`，不再用 Esc。
* `ui_escape()` 保留但文档里写明「只在确认面板展开时可用」，代码里已无调用（grep 可验）。

---

## 2. 根因 B：Windows OCR 读不出「用户F」

### 2.1 证据

* 侧栏 12 px 的「用户F」：winsdk 在任何缩放（1x/2x/3x、灰度、二值化、加白边）下都读不出，
  合成 48 px 图能读出 → 引擎字表上限，不是参数问题。
* 聊天头部：「用户F」固定被读成「丕 忄 尸」（三个部首碎片），1x/2x/3x 一样。
* 结果：`find_row()` 永远找不到她 → 只能靠搜索；`_identity_recheck()` 又把「丕忄尸」当
  别人 → 07:51–07:53 给用户F的 4 条消息全被清空拦死（`chat switched mid-send`）。

### 2.2 修复：RapidOCR 常驻服务

* 新建 `scripts/ocr_server.py` + `wechat-ai-stack/.venv-ocr`（Python 3.12，
  `rapidocr_onnxruntime 1.4.4`）。模型加载约 2.5 s，所以做成**常驻命名管道服务**
  （`\\.\pipe\wxui-ocr`，空闲 30 min 退出），wxui 按需拉起、复用。
  akasha-bridge 的 venv 不装 onnxruntime/opencv，依赖隔离。
* `wxui.ocr_lines()`：`ocr_engine=rapid|auto` 先走常驻服务；失败（或显式 `windows`）退回
  原 winsdk `ocr.py`。响应 schema 不变（`text/x/y/w/h/words`），坐标就是输入图像素。
* 实测：侧栏 1x「用户F」score 0.91，「示例群聊D」不再读成「测讠羊」；聊天头部 1x 直接读
  「用户F」。单帧 OCR 0.3~0.4 s（winsdk ~0.6 s）。
* `sidebar_ocr_scale: 2.0 → 1.0`、`header_ocr_scales: [1.0]`（RapidOCR 1x 够用），
  一次 `open_chat` 的 OCR 次数从 15 降到 3~5。

### 2.3 顺带修掉的两个误判

1. `_identity_recheck()`：头部全是 ≤4 字碎片（`_header_unreadable`）时按「读不出」放行，
   不再当「另一个像人名的会话」否决。
2. `selected_row_text()` 的坐标 bug：截图是**侧栏区域**，旧代码把绝对坐标传给
   `_row_selected()` 再减窗口原点，等于偏移了 `(90, 150)`，永远在测下面 1.5 行的绿底
   （用户F被读成「示例群聊D」→ 触发多余的整屏扫描和补点）。现在用截图内坐标，读回正确。

---

## 3. 根因 C：搜索面板本身就是「看起来像冻结」的覆盖层

实测（`logs/ocrdiag/search_panel.png`）：点搜索框粘贴后，面板从顶部展开、覆盖整个侧栏
（右缘约窗口 x=640），分区为「最常使用 / 聊天记录 / 搜索网络结果」。面板开着时：

* 侧栏**不更新**（被盖住）→ 看起来「卡在某一刻」；
* 点侧栏坐标实际点在面板上 → 「点了没反应/点到视频号」；
* 悬停探活也会误报（0.0）——所以 `ensure_ui_live()` 在判死之前会先 `dismiss_overlay()`
  收覆盖层、再复测，并且**确认窗口可见/未最小化/前台/未被遮挡**（`ensure_wechat_ready`）
  才允许走到重启分支。

搜索兜底的加固（`scripts/wxui.py:2802 _search_result_pick` / `:2948 open_chat_by_search`）：

* **禁止盲点**：`search_blind_click=false`（默认），不再按 `rect.top+210` 瞎点。
* **必须先看见面板**：OCR 里出现「最常使用/搜索网络结果/搜索聊天记录」才继续；
  否则判「粘贴没落进去」→ 重粘一次 → 仍没有就 `dismiss_overlay()` 收工，绝不点。
* **只点联系人/群聊分区里整串等于目标名的行**（容差 ≤2 字符头像噪声前缀），
  公众号/文章/视频号/小程序/网络结果分区一律跳过。
* 只在「侧栏整张列表 OCR 失败」后才用搜索：`locate_search_first=false`、
  `sidebar_search_fallback=true`。

---

## 4. 半冻结探活与自愈

`scripts/wxui.py`：

| 函数 | 作用 |
| --- | --- |
| `_render_repaint_once` :2528 | 光标离开窗口抓基线 → 移到会话行 → 再抓，返回灰度变化比例 |
| `ui_render_alive` :2573 | 先 `ensure_wechat_ready()`（可见/未最小化/前台/WindowFromPoint 未遮挡），再测两行取最大值；测不出返回 None（绝不当冻结） |
| `ensure_ui_live` :2717 | 25s→**60s** TTL 缓存（`logs/wxui-liveness.json` 跨进程）；判死后先 `dismiss_overlay()` 收面板复测；仍死→冷却闸门→重启微信；用户在用键鼠（`human_busy`）时让路 |
| `restart_wechat` :2644 | taskkill + 启动 + 等窗 + 登录页点「进入微信」（0.5w/0.79h）；记录 `logs/wxui-wechat-restarts.json` |
| `_recover_budget` :2608 | 10 分钟冷却、每小时最多 3 次 |

`open_chat()` 在拿到可用窗口矩形后调用 `ensure_ui_live()`；失败返回可重试错误，消息留在
bridge 重发队列里，20s 后自动再来。

另外：wxui 现在**同时写 `logs/wxui-YYYYMMDD.log`**（按天追加）。原因：bridge 用
`subprocess.run(capture_output=True, timeout=45)` 调 wxui，超时被杀时 stdout/stderr 全丢，
日志里只剩「send 超时」，看不出卡在哪一步。新日志会记录
`↪ attempt=… open_chat ok claimed=…`、`⏎ 复核通过，按回车`、`🔎 verify … found=…` 三行，
一看时间戳就能判断卡在「打开会话 / 输入 / 回读」哪一段。

---

## 5. 实测证据（2026-10-29）

| 项目 | 结果 |
| --- | --- |
| `wxui.py rows`（侧栏 OCR） | engine=rapidocr，第一行 `用户F` y=13 |
| `find_row` 用户F / 用户C / 示例群聊D | 0.54 s / 0.81 s / 0.82 s，全部 current viewport 命中 |
| 搜索兜底 `open_chat_by_search('用户F')` | True 4.38 s，picked=`用户F`（最常使用行），头部 `['用户F','+']` |
| 搜索后活性 | hover ratio **0.4259**（修复前同样动作 0.0000 = 冻结） |
| 面板没开时 `_search_result_pick` | 返回 None + `panel_visible=false`（拒绝盲点） |
| 真发 `文件传输助手`（commit） | 3 次全部 WeFlow 回读确认：localId **346 / 347 / 348** |
| 单次发送耗时 | 5.5~12.4 s（含 ~1.9 s windows-mcp 启动 + 首次 ~3.6 s 探活），远低于 bridge 45 s 超时 |
| 冻结恢复 | `restart_wechat` 6.2 s 恢复，hover ratio 0.0 → 0.4259 |

---

## 6. 当前配置（`scripts/wxui-config.json`）

```json
"ocr_engine": "rapid",
"ocr_pipe": "\\\\.\\pipe\\wxui-ocr",
"ocr_idle_s": 1800,
"ocr_timeout_s": 20,
"sidebar_ocr_scale": 1.0,
"header_ocr_scales": [1.0],
"header_empty_retries": 2,
"locate_search_first": false,
"sidebar_search_fallback": true,
"search_panel_wait_s": 1.2,
"search_panel_tries": 3,
"search_blind_click": false,
"overlay_dismiss_x_ratio": 0.464,
"overlay_dismiss_y_ratio": 0.084,
"ui_alive_diff_ratio": 0.01,
"ui_live_ttl_s": 60,
"wxui_auto_recover": true,
"wxui_recover_cooldown_s": 600,
"wxui_recover_max_per_hour": 3,
"wechat_exe": "C:\\Program Files\\Tencent\\Weixin\\Weixin.exe"
```

`scripts/wxui-config.json` 每次调用重读，改完**不用重启 bridge**；wxui.py 由 bridge 每次发送
新起进程（`wxui_warm_worker=false`），代码改动同样即时生效。

---

## 7. 回滚

* 只回滚搜索策略：`locate_search_first: true` 可恢复旧顺序（不推荐）；`search_blind_click: true`
  才恢复固定坐标盲点（强烈不推荐）。
* 回滚 OCR 引擎：`ocr_engine: "windows"`（并把 `sidebar_ocr_scale` 改回 2.0、
  `header_ocr_scales` 改回 `[1.0,2.0,3.0]`）。RapidOCR 服务不启动就自动退回 winsdk。
* 回滚自愈：`wxui_auto_recover: false`。冻结时返回可重试错误、消息留在重发队列。
* 改动前的 `wxui.py` 备份：仓库内 `*.bak-*`（本轮没有新增 `.bak`，因为改动都在 git 之外
  的文件里；如需回滚旧行为，按上面三项配置即可，代码路径保留）。

---

## 8. 遗留与注意

1. **不要再用 Esc 清场**。代码里已无 `ui_escape()` 调用；以后新增微信自动化时也要遵守。
2. `windows` OCR 引擎下「用户F」仍然读不出，所以 `ocr_engine=windows` 只是排障退路。
3. `.venv-ocr` 是新增的 274 MB 环境；如果删掉，`ocr_engine=rapid` 会退回 winsdk（功能降级）。
4. 自愈重启会**杀掉用户正在看的微信**：已加冷却/每小时上限/`human_busy` 让路，但仍是
   最后手段；日志 `logs/wxui-wechat-restarts.json` 可查。
5. 本轮实验期间为定位根因主动重启过微信约 5 次（08:38 误判 1 次 + 实验 4 次），属一次性
   诊断开销；生产路径有闸门不会再这样。
6. 复现/诊断脚本（都在 `scripts/`，前缀 `_`）：
   `_exp_search_freeze_1029.py`（根因实验）、`_probe_search_panel_1029.py`（面板 OCR + 活性）、
   `_test_ocr_server.py`（OCR 服务往返）、`_bench_rapidocr.py`（OCR 耗时）、
   `_probe_hover_detail_1029.py`（悬停判据细节）、`_diag_steps_1029.py`（提交路径分段计时）、
   `_diag_bridge_invoke_1029.py`（按 bridge 的方式跑一次 send 并计时）。
7. **调试期间不要在 bot 在线时跑 GUI 实验**：bridge 的 `_ui_lock` 只串行化它自己的发送，
   外部脚本（我这次的面板实验）会与它抢同一个微信窗口——09:10–09:16 那批 45s 超时就是
   实验脚本和 bridge 发送叠在一起造成的，停止实验后立刻恢复（同一套 bridge 调用
   09:20–09:27 连续 4 条全部 verified）。定位线上问题请先看 `logs/wxui-*.log` 的三段时间戳。
8. 09:10–09:16 期间被 bridge「重发放弃」的 4 条 bot 回复（`草，这不就是我早上起床的样子`、
   `你几点睡的`、`这是躺平了在翻来覆去体吧`、`几点了还没睡`）**没有送达、也没有自动补发**；
   需要的话可以按原顺序重发（它们已过时，建议先问用户）。
9. XERJ 索引需要增量重建才能检索到 `ocr_server.py` 与本轮改动：
   `xerj autoindex wechat-ai-stack --code-analyzer code --yes`（节点 :9200）。
