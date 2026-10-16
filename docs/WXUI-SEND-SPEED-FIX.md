# 分段回复发送提速：每条消息 ~8.5s → ~2.0s（常驻 worker + 热会话）

- 日期：2026-10-16
- 影响组件：`wechat-ai-stack\scripts\wxui.py`、`wechat-ai-stack\akasha-bridge\wxui_sender.py`（+ `config.py` / `state.py` / `senders.py` / `web_panel.py`）
- 用户诉求（原话）："现在分段对话之后发送消息所需时间太长了，能不能优化uia逻辑，比如发给同一个人的，不用重复验证窗口位置和名字，直接往对话框粘贴发送再粘贴发送这样"

## 症状与实测（优化前）

每条消息都是 `wxui.py` 的一次性进程调用（bridge `_run` → `subprocess.run`），于是每条消息都要付一遍：

| 阶段 | 耗时 | 证据 |
| --- | --- | --- |
| windows-mcp 服务启动 | **~2.9–4.0s** | `scripts\wmcp.py` 的 `class Client.__init__` 每条消息都 `Popen([...windows-mcp.exe, "serve", "--transport", "stdio"])`；`wmcp.py list` 2.85–2.94s、`wxui.py info` 3.28–3.99s |
| 会话探测 `wait_for_session` | 0.6–1.5s | `ControlStatus` + 64×64 `Screenshot` 探活 |
| 侧边栏 OCR 找行 + 点击 | 1–2s | `find_row`（截图 + Windows OCR）+ `after_click_wait` 1.4s |
| 聊天头部 OCR 复核 | 0.5–1.5s | `chat_header_matches` |
| 清空输入框 | **2.08s** | Click 0.56 + ctrl+a 0.55 + delete 0.52 + 三次 sleep 0.35（单次调用基准实测） |
| 粘贴 + 回车 | 1.60s | Clipboard 0.00 + ctrl+v 0.55 + 硬编码 `sleep 0.5` + enter 0.52 |
| 回读验证 | 0–1.2s | WeFlow `/api/v1/messages`，轮询间隔硬编码 1.0s |

而 AstrBot 的分段回复是**逐段**经过 bridge 的：`ob_protocol.py:107` `await asyncio.to_thread(state.sender_instance.send_text, contact, text)`，每一段都要等上一段的 HTTP 返回，间隔远超调度的 `group_gather_seconds=1.2`，所以 `WxuiSender._sched_loop` 的 `assume_current`（同一联系人连发免侧边栏 OCR）**几乎从不生效**——每段都在重走完整路径。3 段回复 ≈ 25s。

## 修复

### 1) 常驻 worker（省掉每条消息的 windows-mcp 启动）

- `wxui.py` 新增子命令 `worker`：`worker_loop(cfg)` 只创建**一个** `Client()`，然后逐行读 stdin 的 JSON 请求，逐行写 stdout 的 JSON 响应（`{"id":N,"cmd":"send|send-image|verify|info","spec":{...}}` → `{"id":N,"ok":bool,"data":{...}}`）。stdin 关闭即退出（`client.close()`）。
- `worker_dispatch(cfg, client, subcmd, spec)` 把请求映射到已有函数；`send()` / `send_image()` / `probe_session()` 新增 `client=None` 形参——传入就用常驻客户端，不传就自己开一个（`own_client`，用完关掉），CLI 行为不变；`finally: if own_client: client.close()`。
- bridge 侧 `WxuiSender` 常驻这条管道：`_worker_start()`（`Popen(..., stdin/stdout=PIPE)` + 读线程把响应塞进 `queue.Queue`）、`_worker_pump()`、`_worker_call()`（加锁 + 序号匹配 + 超时）、`_worker_kill()`；`_run()` 先试 worker，**任何异常/超时/进程死了都自动回落到原来的 `_run_oneshot()`**，所以 worker 出问题只是慢，不会发不出去。`stop()` 会收掉 worker，`health()` 汇报 `warm_worker / worker_alive / worker_requests`。

### 2) 热会话：同一联系人的下一条不再验证窗口位置和名字

`_WARM = {"target","at","header_at","rect","clean","clean_at"}` 保存在 worker 进程内（跨消息复用）：

- `_warm_mark(target, rect, header_verified=True, clean=True)` 只在**回读确认送达**那一刻写；`_warm_forget_clean()` 在本条消息开始发送前立刻作废它。
- `warm_session_trusted(cfg, target, plan)`：同一联系人 + 窗口矩形没变 + 上一次是本进程内已验证送达 + 在 `warm_reuse_s` 内 → 跳过 `wait_for_session`（省 ~0.6s）。
- `open_chat()` 新增 warm 分支：命中时 `plan["claimed"] = "chat already on target (warm session: no OCR)"`，**既不做侧边栏截图/OCR/点击，也不做头部 OCR**（`warm_header_recheck_s` 秒后会重新确认一次头部名字）。
- `input_box_trusted_clean(cfg, target)` + `clear_input(..., focused_clean=True)`：回车已经把输入框清空、焦点还在微信，所以第 1 次尝试只按一个 `delete`（2.08s → 0.55s）。
- `verify_poll_s`（默认 0.6）、`after_paste_wait_text`（默认 0.3）取代原来硬编码的 1.0s / 0.5s。

### 3) 安全不变式（为什么不会发错人 / 不会重复发）

1. **每条消息仍然回读 WeFlow**（`verify()` / `verify_image()`），只有从微信自己的库里读到才算送达，没读到就进重发队列。
2. 热路径的信任标记**只能由一次成功的回读写出来**；被中断的发送（粘贴后没回车、或回车没到微信）拿不到标记，下一条立刻回到完整路径并重新探测会话。
3. warm 分支只在 `find_window(fuzzy=True)` 已确认当前打开的就是目标会话时才走，且窗口矩形变了就作废；头部名字在 `warm_header_recheck_s` 后会再 OCR 一次。
4. `assume_current` / warm 的判定失败一律回落完整路径；worker 不可用时回落 `_run_oneshot`。
5. 清空输入框仍保留一次 `delete`（不是完全跳过），维持"每次尝试都从空输入框开始"的原有约定。

## 验证

- 单测：`akasha-bridge\.venv\Scripts\python.exe -m unittest discover -s tests` → **Ran 49 tests OK**。新增覆盖：`test_worker_subcommand_exists_on_the_script`、`test_warm_path_still_gates_on_verified_delivery`（断言两条发送路径都"先算 trusted、再作废 clean"）、`test_clear_input_fast_path_is_guarded`、`WarmWorkerWiringTest`（config/state/senders/main 四处接线）、以及 shim 里 `warm_worker=False`（既有用例不能去起真进程）。
- 实测（`scripts\wxui_warm_probe.py <联系人> 4`，走 `send()` 真实发送并回读）：

  | 消息 | 优化前 | 优化后 | 路径 |
  | --- | --- | --- | --- |
  | 第 1 条 | ~8.5s | 6.09s | `chat already open`（首次含 worker 启动） |
  | 第 2 条起 | ~8.5s | **1.98s** | `chat already on target (warm session: no OCR)` |

  4 条全部 `送达=True`，省 67%。
- bridge 端到端（`POST http://127.0.0.1:8766/send` 两条给"文件传输助手"，重启后的真实链路）：
  ```
  18:33:05 [wxui] 🚀 常驻 worker 已启动（复用 Windows-MCP 客户端；同一联系人连发不再重复验证窗口与名字）
  18:33:11 [wxui] ✅ 已验证送达 文件传输助手（localId=80, 尝试=1 | 常驻worker chat already open）
  18:33:13 [wxui] ✅ 已验证送达 文件传输助手（localId=81, 尝试=1 | 常驻worker chat already on target (warm session: no OCR)）
  ```
  `/senders` 显示 `warm_worker=true, worker_alive=true, worker_requests=2, pending_retries=0`。

## 配置项（`scripts\wxui-config.json` / bridge `config.json`）

| 键 | 默认 | 作用 |
| --- | --- | --- |
| `warm_reuse_s` | 45 | 热会话/信任标记的有效期 |
| `warm_header_recheck_s` | 10 | 热路径下多久重新 OCR 一次聊天头部名字 |
| `warm_skip_session_probe` | true | 热路径是否省掉 `wait_for_session` |
| `input_fast_clear` | true | 已确认送达后的第 1 次尝试是否只按一个 `delete` |
| `after_paste_wait_text` | 0.3 | 正文粘贴后等待（图片仍用 `after_paste_wait` 1.2） |
| `verify_poll_s` | 0.6 | 回读轮询间隔（原硬编码 1.0） |
| `wxui_warm_worker` (bridge) | true | 关掉即回到每条消息一次性进程的老路径 |

## 回归提示

- 慢：先看 `bridge.log` 里有没有 `🚀 常驻 worker 已启动`；没有说明 `wxui_warm_worker` 关着，或 worker 起不来（回落到一次性进程，日志里不会出现 `常驻worker ...` 后缀）。`/senders` 的 `worker_alive` 只在“发出过第一条消息”后才为 true（懒启动）。
- 偶发慢：某条消息显示 `chat already open` 而不是 `warm session: no OCR`，说明热标记失效了（换联系人、窗口被移动/改大小、超过 `warm_reuse_s`，或上一条没验证成功）——这是设计内的保守回落。
- 想确认没发错人：`bridge.log` 里每条都有 `已验证送达 <联系人>（localId=...）`，localId 可与 WeFlow `/api/v1/messages` 对照。
