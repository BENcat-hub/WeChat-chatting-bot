# 桥接缓冲事故：AI 读到的其实是十几分钟前的旧图（"人设图"）

- 日期：2026-10-16
- 影响组件：`wechat-ai-stack\akasha-bridge\bridge_core.py`
- 现象：用户发来一张**猫**的照片 + 文字"图发给你"，机器人却回："图里是水手服女孩子啊，哪来的小猫"。

## 症状链

| 时间 | 证据 |
| --- | --- |
| 15:44:08 | 收到图片但取图失败：`⏳ 图片还没有本地缓存（等 WeChat 下载，第 1/5 次）`→ `消息列表无图片 mediaUrl (talker=wxid_CONTACT_A)` → `⚠️ 取图失败，图片降级为文字`；文本占位 `[图片: （图片内容无法描述）]` 留在缓冲队列里 |
| 15:45:17 / 15:46:56 | 用户后来发的两张图被保存（`wechat_1792136717.png` / `wechat_1792136816.png`）并注入**同一个**已有条目 |
| 15:56:27 | 用户发文字"图发给你" → 立刻 `推送 4 条消息`：**里面是 12 分钟前的那两张旧图 + 那条旧占位文本** |
| 15:56:32 | 真正的猫才下载完：`✅ 微信图片已保存: wechat_1792137392.jpg (localId=239)` → `📝 图片已注入待处理文本队列`，**此后没有任何推送** |
| 15:56:43 | AstrBot 视觉转述（`source=plugin_vision scope=private_image images=1`）描述的是"水手服少女半身插画" = 旧图；`图文混合上下文 images=2` |

复核：`wechat_1792137392.jpg` 确实是那张猫（小米 17 Pro Max 水印，1280×1706）；插件 `private_inbound_images\1000000006\` 里当轮落盘的两张 158,652 B PNG 是旧的人设图（`shutil.copy2` 保留源 mtime，mtime 分别是 15:46:56 / 15:45:17），**猫那张根本没进当轮**。

## 根因：缓冲条目"推送后不挂计时器"

`bridge_core.py` 的缓冲条目在推送后**不删除**，只把 `processing` 置回 `False`：

- `add_to_buffer()`（文字）：只有 `if not entry["processing"]` 才 cancel/重建 `threading.Timer`。
- `process_image_message()`（图片）：往已有条目里 `insert(0, caption_text)` 的两个分支**完全不挂计时器**。
- 于是：图片落进一个"已经推送过、计时器早已死亡"的条目 → 永远等不到 flush；下次用户发消息时才被一起推出去，AstrBot 拿到的是十几分钟前的旧图与旧占位。
- 另一处同期问题：取图失败的占位文本也留在队列里，会与新图一起被当成"用户刚刚发的图"。

## 修复

1. **过期消息 TTL**：`WeFlowBridge.BUFFER_MESSAGE_TTL_SECONDS = 120.0`；`process_sender()` 按 `entry["message_ts"]` 丢弃超龄消息（日志 `⏭️ 丢弃 N 条过期缓冲消息（>120s）`），全过期则直接清空不发。
2. **补计时器**：新增 `_arm_flush_locked(buffer_key, entry, delay)`——`processing` 中不挂、已有存活计时器不动、否则 cancel 后重建并 bump `timer_version`。三处调用：图片注入已有条目（私聊 2s / 群批处理 5s）、`process_sender()` 推送结束发现条目里又有新消息（1s）。
3. 所有写入路径同步维护 `message_ts`（`add_to_buffer`、`add_text_to_buffer`、`_inject_cached_image`、三个图片分支），保证与 `messages`/`rawids` 等长对齐。

## 验证

- `python -m py_compile bridge_core.py` → exit 0。
- 单测：`akasha-bridge\.venv\Scripts\python.exe -m unittest discover -s tests -t tests` → **Ran 43 tests OK**，其中新增 `tests\test_buffer_lifecycle.py`（7 例）覆盖：
  - 过期图片消息被丢弃、只推新消息；
  - 全部过期时一条都不推；
  - 新鲜消息正常合并；
  - 推送期间新到的消息会被补计时器；
  - `_arm_flush_locked` 在 `processing` 中不挂、对存活计时器不抢占。
- 已在 2026-10-16 16:04 重启 bridge（PID 51532）并确认 `✅ WeFlow API 正常` / `✅ 已连接到 AstrBot` / `✅ 已连接到 WeFlow 推送`。

## 复现/回归提示

若再出现"AI 描述的是更早的图"，按顺序查：
1. `akasha-bridge\bridge.log` 里该条消息前后的 `推送 N 条消息` 与 `⏭️ 丢弃 ... 过期缓冲消息`；
2. `astrbot\data\temp\wechat_images\` 里 AstrBot 收到的图（对照 `localId`）；
3. `astrbot\data\plugin_data\astrbot_plugin_private_companion\private_inbound_images\<user_id>\` 的文件 mtime（copy2 保留源 mtime，可直接看出"贴的是哪一轮的图"）。
