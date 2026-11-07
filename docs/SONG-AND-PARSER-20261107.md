# 点歌（想听你唱 xxx）与链接解析的接线说明（2026-11-07）

## 点歌：主模型判断意图 → 调现成插件

不新增插件。`astrbot_plugin_matsuko_cover` 已注册 LLM 工具：

- `smart_cover(song_name, artist_name, model_name, api_type, music_source)` —— 一句话点歌，
  自己搜歌（QQ音乐）、自己翻唱、完成后自己发音频
- 其它：`search_music`、`svc_cover`、`svcvc_cover`、`get_available_models`、`confirm_selection`

**唯一需要改的是提示词**（`astrbot/data/config/astrbot_plugin_private_companion_config.json`
的 `reply_style_prompt` 末尾追加，2026-11-07 已加）：

> 当对方说想听你唱歌、点歌、来一首、唱一首某首歌时，调用 smart_cover 工具
> （song_name 填歌名，model_name 填「孙燕姿」，music_source 填 qqmusic），
> 调用前只回一句很短的话（例如「我去找伴奏」），不要自己写歌词、不要假装在唱、
> 不要许诺完成时间；工具会把翻唱音频发出去。

注意：

1. 该配置文件**不在 git 里**（`.gitignore` 排除了 `astrbot/data/config/`），
   换机/重装后要照上面这段重新加一次。
2. 模型能调工具的前提是 AstrBot 那一轮把工具集合递给模型；private_companion 只在
   便签/表情场景摘掉个别 `pc_*` 工具（`llm_tool_actions.py:2551/2590`），不动 `smart_cover`。
3. `smart_cover` 是异步的：调用立刻返回「正在处理」，音频由插件后台发。
4. 翻唱走 `rvc-backend` 的 `/convert`，模型固定 `孙燕姿`（`rvc-backend/models/孙燕姿/G_27200.pth`）。

## 链接：改走「万能解析器」

`astrbot_plugin_parser`（万能解析器 v1.5.7）对**所有消息**自动解析 URL
（B站/抖音/小红书/YouTube/X…），比 bridge 自己拼「标题/摘要」准。

`akasha-bridge/bridge_core.py`：

- 新增 `_link_url(data)`：回查 `localType==49` 的 appmsg，只取 `<url>`，且必须是 http(s)
- `add_to_buffer` 的链接分支优先用 `_link_url()` 的**原始 URL** 作为消息正文；
  拿不到 URL 才回落旧的 `_resolve_link_card()` 摘要

视频（对方直接发 mp4）没有 URL，仍走 `process_video_message()` 的抽帧 + VL 描述，
这条保留作为兜底。

## 自测

```bash
# 链接 URL 提取（stub 数据，不联网）
akasha-bridge/.venv/Scripts/python.exe -X utf8 _restore-20261103/test_link_url.py

# 语音标签剥离
astrbot/.venv/Scripts/python.exe -X utf8 _restore-20261103/test_tts_tag_leak.py
```
