# 人设参考图与 4K 出图（2026-11-10）

现象：陪伴插件「我会画给你看」生成的穿搭/自拍图与 `纳西妲_人设.jpg` 完全不像；同一轮日志显示
`reference=False reference_exists=False`，且提交尺寸是 `1024x1024`。

## 一、为什么人设图没被用上

两条独立原因，缺一不可：

1. **规范参考图目录为 `[]`（空数组 ≠ None）**
   `image_runtime.py::_photo_persona_reference_image_path()` 的逻辑是：
   - `photo_reference_catalog is None` → 读旧配置 `photo_persona_reference_image_path`；
   - 否则**只从 catalog 里找 `kind == "persona"` 的条目**，旧路径键被彻底忽略。

   两份配置（`astrbot_plugin_image_companion_config.json` 的 `image.` 段、
   `astrbot_plugin_private_companion_config.json` 的根与 `photo_action_config`）里
   `photo_reference_catalog` 都是 `[]`，于是分支走「catalog 存在但无 persona」→ 返回空串。

2. **`load_catalog` 的旧配置迁移依赖一个尚未复制的属性**
   即使 catalog 是 `[]`，`load_catalog(..., legacy_persona=self.photo_persona_reference_image_path)`
   本可以迁移出 persona 条目；但这段代码在 mixin `__init__` 里执行，
   此时 `photo_persona_reference_image_path` 还没从 owner/service 复制过来，
   `getattr(self, ..., "")` 拿到空串 → 迁移结果为空。

修法：直接写一条**规范化条目**进 catalog（等同 WebUI 保存后的形态）：

```json
{
  "id": "persona",
  "kind": "persona",
  "source": "<人设图绝对路径>",
  "note": "默认人设参考图",
  "reference_roles": ["identity"],
  "outfit_category": "",
  "outfit_lock_default": false,
  "scene_categories": [],
  "time_categories": [],
  "preferred_preset": "",
  "metadata_source": "migration",
  "editor_intent": null,
  "excluded_scene_categories": [],
  "excluded_time_categories": [],
  "selection_eligibility": "fallback_allowed"
}
```

要点：
- 条目必须通过 `photo_reference_catalog.validate_and_serialize()`（否则整个目录被判定为只读并跳过）；
- `selection_eligibility` 用 `fallback_allowed`，否则 `matching_only` 在场景/时段不匹配时不会兜底；
- 三处（image 的 `image.` 段、private 的根与 `photo_action_config`）都要写，否则回落路径又把 catalog 换成空的。

验证（日志 `logs/astrbot.out.log`）：

```
参考图库已选图: source=single_candidate reason=only_one_candidate id=persona kind=persona fallback=persona
在线图片 API 尝试参考图接口: endpoint=http://127.0.0.1:8790/v1/images/edits model=jimeng-4.6
生图结束: ok=True reference_used=True note=ok；已使用本地人设参考图；实际提交 1/1 张参考图
```

## 二、为什么一直是 1024x1024

1. 工具文档串写的是「如 1024x1024」，模型照抄这个示例值作为 `image_size` 参数；
2. `proactive_message.py` 的每日穿搭分支硬编码 `image_size="1024x1024"`。

改法：
- 工具文档串改为「默认留空即用配置（4693x3520 竖幅 4K）」；
- `llm_tool_actions.py` 增加 `_photo_size_or_default()`：空值或 `1024x1024` 一律视作未指定，交给
  图片插件按 `external_image_api_size` 出图；
- `proactive_message.py` 每日穿搭的 `image_size` 改为 `""`。

实测：`在线图片 API 参考图提交: size=4693x3520` → 45.8s → 产出 4693×3520 PNG（7.95 MB）。

## 三、涉及文件与回滚

| 文件 | 改动 |
| --- | --- |
| `astrbot/data/plugins/astrbot_plugin_private_companion/main.py` | 工具文档串（image_size 默认说明） |
| `astrbot/data/plugins/astrbot_plugin_private_companion/llm_tool_actions.py` | 新增 `_photo_size_or_default()` 并接管 image_size |
| `astrbot/data/plugins/astrbot_plugin_private_companion/proactive_message.py` | 每日穿搭 image_size 置空 |

回滚：同目录 `.bak-4ksize-<时间戳>` / 配置同目录 `.bak-personacat-<时间戳>`、`.bak-size4k-<时间戳>`。

一次性脚本（不入库）：`_restore-20261110/fix_persona_catalog.py`、`fix_photo_size.py`、
`patch_photo_size_default.py`、`probe_catalog.py`。

## 四、注意

- 改站点包/插件源码后必须重启 AstrBot；`_start_astrbot_hidden.ps1` 在旧实例仍占端口时会报
  `Cannot acquire lock file`，要先把 11229/6185 的属主进程清干净。
- 若换了人设图，改 `source` 即可；`id` 保持 `persona`（同 kind 只允许一条）。
