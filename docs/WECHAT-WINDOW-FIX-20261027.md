# 微信「关不掉的悬浮会话窗」修复（2026-10-27）

## 现象
- 桌面上会出现一个悬浮在顶层的聊天窗（截图里是 `与"文件传输助手"的聊天记录`、`用户D` 那种独立窗口），
  **点 × 关不掉，只能退出微信重开**。
- 用户点窗口里「点此从手机导入更多聊天记录」的提示条时，微信直接崩溃（`Weixin 遇到错误`）。

## 实测
- 这类窗口的类名与主窗口相同（`Qt51514QWindowIcon`），标题是会话名，属工具窗口样式。
- `EnumWindows` 里确实能看到它（例：`hwnd=7539564 title='与"文件传输助手"的聊天记录' rect=[1416,406,2420,1678] visible=True`）。
- 对它单独发 `WM_CLOSE` 就能关掉；关不掉的情况再多发一次 `WM_SYSCOMMAND/SC_CLOSE`，仍不行就
  `ShowWindow(SW_HIDE)` + 挪到屏幕外（工具窗口只认隐藏）。

## 改动
1. `scripts/winio.py`
   - `close_detached_chat(title=None, main_title="微信", force=True)`：WM_CLOSE → 等 200ms → 仍在则 SC_CLOSE →
     仍在则 SW_HIDE + `SetWindowPos(-20000,-20000)`；返回值带「（已强制隐藏）」后缀便于日志辨认。
   - 新增 `list_wechat_chat_windows()`：列出微信除主窗口外的窗口（hwnd/标题/类名/可见/rect），供诊断。
   - 跳过 `WxTrayIconMessageWindow`（托盘消息窗）与主窗口，不会误关。
2. `scripts/wechat_cleanup.py` / `scripts/wechat_cleanup.cmd`
   - 干跑：`python -X utf8 scripts\wechat_cleanup.py`
   - 执行：`python -X utf8 scripts\wechat_cleanup.py --apply`（先 raise 主窗口，再强制关闭所有悬浮会话窗，输出前后对比 JSON）
   - 双击 `scripts\wechat_cleanup.cmd` 即可，不用管理员。
3. `wxui.py` 的发送前/打字前清理沿用同一函数 → 现在默认走 force 分支，顺手把残留悬浮窗清掉。

## 关于崩溃
- 崩溃是用户手点「导入更多聊天记录」提示条触发的（微信自身 bug / 风控），不是自动化点击导致；
  自动化只点侧栏行与麦克风/发送键，不会碰这条提示。
- 如果之后它挡住侧栏导致定位失败，跑一次 `wechat_cleanup.cmd` 或让机器人发一条消息（发送前会自动清理）。

## 验证
- 实测当场有一个残留悬浮窗，执行 `--apply` 后：
  `closed: ["与\"文件传输助手\"的聊天记录"]`，`strays_after` 只剩托盘窗 → 已消失。
