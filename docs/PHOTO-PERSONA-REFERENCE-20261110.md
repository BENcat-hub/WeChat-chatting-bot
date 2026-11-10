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

## 二·五、退回 1024x1024 + 提示词结构改造（同日晚）

用户要求「还是改成 1024x1024，优化提示词」。三处关键发现：

1. **`natural_language` 模式强制英文**：`image_runtime.py:10917` 的格式说明写死「用连贯、具体的
   **英文**写成一段画面描述…末句用 Avoid ...」，所以提示词又长又英文。改用 `traditional`
   （`Positive prompt: ... Negative prompt: ...`，正负分明）。
2. **配置被启动回写覆盖**：直接改 `astrbot_plugin_private_companion_config.json` 无效 —— 插件
   15:30:23 加载时会把文件重写回旧值（实测我的 15:29:11 改动全被还原）。**必须走面板 API**
   `POST /api/plug/astrbot_plugin_private_companion/page/settings/update`（JWT = `cmd_config.json`
   的 `dashboard.jwt_secret` 自签 HS256），与分段开关同一个坑。
3. **754 字符裁剪把身份锚吃掉**：代理按上游 800 上限留「头部 + 结尾要求句」，而插件把中文
   身份描述写在提示词中段 ⇒ `has_identity=true` 但 `identity_in_sent=false`。修法是
   `scripts/jimeng_image_server.py::_identity_rescue()`：以身份标记（同一个人/鲸鳍/渐变长发/
   天蓝色大眼睛…）为中心取 ±窗口，插在头部与要求句之间并计入预算。

当前生效值（API 写入并已落盘）：

| 键 | 值 |
| --- | --- |
| `external_image_api_size` / 端点 `size` | `1024x1024` |
| `photo_generation_prompt_format` | `traditional` |
| `photo_generation_style` | `二次元` |
| `photo_generation_negative_prompt_mode` | `merge` |
| `photo_generation_selfie_fixed_prompt` | 身份锚（蓝渐变长发 / 鲸鳍发饰 / 天蓝眼 / 纤细身形）+ 日常私服 + 日系二次元插画 |
| `photo_generation_*_negative_prompt` | 动物、鲸鱼、尾巴、多余人物、文字水印、写实质感、畸形手指… |

实测（16:16）：`prompt_chars=4932 → sent=761`，`has_identity=true`、`identity_in_sent=true`，
`size=1024x1024`，36.7s 出图；画面出现鲸鳍状发饰、蓝色渐变长发、二次元线条，且无多余动物/水印。

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
