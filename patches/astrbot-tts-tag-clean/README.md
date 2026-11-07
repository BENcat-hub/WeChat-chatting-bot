# AstrBot TTS 标签清洗补丁

问题：`<pc_tts>…</pc_tts>` / `[[PCTTS:…]]` 会被 TTS provider 当成正文朗读
（edge_tts 直接读标签，微信「语音转文字」把它显示成 `PC下划线TTS…`）。

改法：在 AstrBot 三个 TTS 入口把标签剥掉再交给 provider：

- `astrbot/core/pipeline/result_decorate/stage.py` → `get_audio(clean_tts_text(comp.text))`
- `astrbot/core/astr_agent_run_util.py` → `get_audio(clean_tts_text(text))`
- `astrbot/core/provider/sources/edge_tts_source.py` → `kwargs = {"text": clean_tts_text(text), ...}`

新增文件 `astrbot/core/utils/tts_text_clean.py`（本目录有副本）。

**升级 AstrBot 会覆盖 site-packages，必须重跑**：

```
astrbot\.venv\Scripts\python.exe -X utf8 patches\astrbot-tts-tag-clean\apply_patch.py
```

验证（离线，不需要网络）：

```
astrbot\.venv\Scripts\python.exe -X utf8 _restore-20261103\test_tts_clean2.py
# '<pc_tts>这张卡我多看了两眼</pc_tts>' -> '这张卡我多看了两眼'
# 'PC下划线TTS你好' -> '你好'
```

2026-11-07 实测：三条入口均已挂上，`py_compile` 通过，重启后 AstrBot 正常加载 13 插件。