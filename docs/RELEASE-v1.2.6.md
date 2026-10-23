## 新增：OCR 之前先确认「微信窗口开着、且在最上层」

之前 OCR 直接截屏识别，只要微信被别的窗口盖住，读到的就是别人家 UI —— 表现出来是
「侧栏里找不到会话」「聊天头部认错人」「点到别的窗口去」，而且很难从日志看出来。

现在 `scripts/wxui.py` 新增 `ensure_wechat_ready(cfg)`，在**每一次侧栏扫描与聊天头部复核之前**
都先做前置检查：

1. 微信主窗口存在吗（`find_wechat`）；
2. 可见吗、有没有被最小化（`IsWindowVisible` / `IsIconic`）；
3. 是不是在最上层：先看 `GetForegroundWindow`；不是的话用
   `WindowFromPoint` 在窗口矩形内抽 5 个点，看这些像素实际属于哪个进程
   （有置顶小工具、输入法候选框时前台句柄可能不是微信，但像素仍是微信的，OCR 依然可信）。

不在最上层时会依次尝试：`raise_window`（SetForegroundWindow → AttachThreadInput →
`SwitchToThisWindow`）→ 最小化再还原（`SW_MINIMIZE`/`SW_RESTORE`）→ 临时置顶再取消置顶
（`HWND_TOPMOST`/`HWND_NOTOPMOST`）。全部失败就返回 `ok=False`，**调用方直接跳过 OCR**，
宁可这条失败也不读脏数据、不往错的地方敲。

### 顺带修的两处
- `_expand_pinned_section` 之前永远返回 False（`log` 未定义 + 认不出「8 个置顶聊天」这种文案），
  现在补了 logger、把 `个置顶聊天`/`置顶聊天`/`个置顶` 纳入提示词；实测日志
  `[wxui] 🔓 已展开「折 叠 置 顶 聊 天」，重新扫描侧栏` 后 `文件传输助手` 能被定位到。
- `raise_window` 加了 `SwitchToThisWindow` 与 `SetWindowPos`，超时 0.8s → 1.6s，对「被 Word/Edge
  反复抢前台」的情况更稳。

### 验证
- 人为把 Edge 切到前台后调用：`ensure_wechat_ready` → `foreground: True`，随后
  `chat_header_matches` 读回 `['文 件 传 输 助 手']`（旧版本这里会读成别家 UI）。
- 未唤起状态下 `chat_header_matches` 直接返回
  `{"error": "wechat window is not in the foreground", "texts": []}`，不再产生误判。
