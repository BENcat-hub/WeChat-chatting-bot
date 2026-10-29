# 点歌后消息复读 —— 根因与修复（2026-10-27）

## 1. 现象

点一首歌后，微信里同一条「好啦好啦～你要的《落叶归根》翻唱做好啦…」连发 5~18 条。
DB 里同文本出现的间隔稳定在 **19~20 秒**，最多一条出现过 **18 次**（14:19:34→14:23:27）。

## 2. 两个独立成因

### 成因 A：插件侧一秒内多次投递
`logs/astrbot-*.log` 与 bridge `send_private_msg` 日志显示，同一句回复在 4 秒内被投了 4 次
（seq 7/10/13/16，09:25:57~09:26:00），每次 bridge 都真的发了出去。

### 成因 B：重发队列把「其实已送达」当成失败，每 20 秒重放一次
`akasha-bridge/config.json` 的 `wxui_queue_interval = 20`。回读校验失败（WeFlow 查询超时、
常驻 worker 卡到 90s、「screen not capturable」等）时 `ok=False`，消息留在队列里，
下一轮（+20s）再发一次 —— 与 DB 里 19~20 秒的间隔完全吻合。

放大因素：`wxui_warm_worker=true` 的常驻 worker 会偶发卡死，单条发送要等到 90 秒超时。

## 3. 修复（三层，全部在发送唯一收口处）

### 第 1 层：正文归一化去重（`akasha-bridge/wxui_sender.py`）
- 新增 `_norm_dedupe_body(text)`：去空白、去句末标点（`。．.!！?？~～…,，、;；:：` 与引号）。
  → `好啦好啦~` / `好啦好啦！！` / `好啦好啦` 视为同一条。
- 图片/文件按 `basename` 归一。

### 第 2 层：尝试缓存与送达缓存分离
| 缓存文件 | 语义 | 窗口 |
| --- | --- | --- |
| `wxui_recent_attempts.json` | 「发送动作可能已经执行过」 | 120s（写：发送前） |
| `wxui_recent_sends.json` | 「已确认送达」 | 300s（写：成功后） |

入口（`_post` / `send_image` / `send_file`）跳过条件 = 已确认送达 **或** 近期已尝试。
→ 回读超时不再触发重放（防复读），也不会被当成已送达而丢消息。

### 第 3 层：短文本保护
「嗯 / 好 / 行吧」这类回复本来就该反复出现：
- bridge 入口：归一化后长度 < 8 → 只按「原文完全相同 + 30 秒」去重；
- `scripts/wxui.py` 发送前的 DB 复检同理（原 `dedupe_window_s=600` 会把 41 秒后的第二句「嗯」吞掉）。

## 4. 顺带修掉的两个 bug
- `drain_queue` 的 subcmd 映射：`send-file` 之前被当成 `send`，报 `empty text: caller passed None`；
  现在按 kind 映射 `send` / `send-image` / `send-file`。
- `wxui_warm_worker` 关闭、`wxui_timeout` 90→45：单进程发送实测 3~8 秒，用稳定性换掉 90 秒卡死风险。

## 5. 验证（2026-10-27 19:20~19:26，真实发送到「文件传输助手」）

| 场景 | 结果 |
| --- | --- |
| 长文第 1 次 | 送达 localId 331，6.1s |
| 同文立刻重发 | 跳过（0s），无新消息 |
| 标点变体 `！！` | 跳过（1s） |
| 不同内容 | 送达 localId 332，7.8s |
| 短文「好」首次 | 送达 335，6.1s |
| 短文 5 秒后重复 | 跳过（0s），无新消息 |
| 短文 35 秒后再发 | 真正送达 336，6.7s |
| 模拟插件突发 6 连 | 只送达 localId 337（新增 1 条），另 5 次 `🔁 跳过` |

## 6. 回滚
- 关闭全部去重：`akasha-bridge/config.json` 设 `wxui_dedupe_window_s: 0` + 删除两个 json 缓存（代码侧窗口取 0 即不拦）。
- 恢复常驻 worker：`wxui_warm_worker: true` + `wxui_timeout: 90`（不建议）。

## 7. 还欠的
- `用户C` 这个会话偶发 90s 超时（warm worker 已关，若仍复现需单独查这个联系人的会话状态）。
- 插件侧一秒多次投递（成因 A）只能靠这一层挡住；若想根治，得在 `matsuko_cover` 回复处加一次性标记。
