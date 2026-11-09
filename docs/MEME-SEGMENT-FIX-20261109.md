# 表情包与分段回复：2026-11-09 归因与修复

## 一、表情包为什么不发（两层，都已修）

1. **图片根本没进微信**。scripts/wxui.py::send_image() 消息模式过去用
   SetForegroundWindow + MCP Shortcut ctrl+v；后台进程的 SetForegroundWindow
   会被系统静默拒绝，剪贴板里的图粘不进输入框，日志只报
   image not read back from WeChat DB within timeout，看起来像“没发”。
   - 改：aise_window 三次重试，用 GetForegroundWindow()==微信hwnd 校验，
     确认在前台才发真键盘 Ctrl+V，三次都失败才退回 MCP Shortcut；
     发送后 inally 把前台还回去。
   - erify_timeout_image_s 15 → **90s**（WeFlow 对自己刚发的图片落库慢 1~2 分钟，
     实测图已在库里，是回读窗口先超时）。
2. **模型不知道能用表情包**。meme_manager 虽然注册了 send_meme 工具，但
   generation.emotion.llm.inject_persona=false，且 generation.prompt.head
   只有一行标题「[表情图片工具]」，聊天请求里没有任何标记语法说明
   （核对 companions.json.recent_prompt_injections.passive：无 &&、无“表情”）。
   - 改：inject_persona=true；generation.prompt.head 写入标记规则
     （“想表达情绪时写 &&分类&&，单独出现、一条最多一个、拿不准就不加，只写这些分类”）。
   - eply_example_enabled/category_example_enabled 保持 false（默认模板是占位符，
     打开只会注入无意义文本）。备份：meme_manager_config.json.bak-inject-* / .bak-head-*。

## 二、分段回复为什么不分段

运行时三个开关曾被镜像回退成 false（root 配置里还能看到 false 的历史值）：
nable_segmented_proactive_reply / nable_llm_controlled_segmenting /
nable_segmented_plugin_rules。当日日志 0 条分段记录。
- 改：走面板 API 重写（POST /page/settings/update + /page/persona/settings/update），
  segmented_proactive_scope|private_scope|group_scope=all_llm、chat_scope=all。
- 重启后复验仍为 true；实测日志出现
  已按分段计划发送 LLM 回复: segments=2 llm_segments=2 —— 模型自己写标记、真的分成两条气泡。

## 三、验收证据

| 项目 | 证据 |
|---|---|
| 表情包选择 | [meme_manager] 选图完成：状态=selected 后端=chat 原因=selected、已选择语义表情：['meme:066fd977c789'] |
| 表情包送达 | [OB11] 图片已发送至 黎栀；WeFlow 	alker=wxid_n4ljpzuu3zxh22 回读 localId=441/447 isSend=1 type=3 |
| 分段 | segments=2 llm_segments=2 first=…（15:16:55 / 15:17:16 两条） |
| 提交 | da2df9（发图链路）、70ba68（分段开关） |