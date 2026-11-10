# 让人格像真人聊天（2026-11-11）

目标：让微信里的回复少一点「AI 味」——不要成段说话、不要每次都完整一句、
不要书面味。做法不是拍脑袋改提示词，而是先用真实聊天量出差距，再分三层改，
每轮都用同一套指标复测。

## 一、先量：真人 vs 机器人（基线）

样本：WeFlow 本地库 5 个私聊各 400 条（`scripts/humanize_metrics.py`），
真人 343 条、机器人 1376 条。

| 指标 | 真人 | 机器人（改造前） |
|---|---|---|
| 平均字数 | 41.3 | 53.0 |
| 中位字数 | 5.0 | 14.0 |
| ≤6 字占比 | 0.606 | 0.188 |
| >30 字占比 | 0.029 | 0.201 |
| 无标点占比 | 0.866 | 0.576 |
| 逗号密度 | 0.15 | 0.53 |
| ≤3 字应答 | 0.292 | 0.055 |
| 叠词率 | 0.166 | 0.128 |
| emoji 率 | 0.006 | 0.071 |

真人长度分布：0-4 字 152 条、5-9 字 117、10-14 字 40——**78% 在 10 字以内**。
机器人中位 14 字、两成超过 30 字，这就是「一眼 AI」的来源。

复测命令：

```bash
# 重新采样并打印对比表
astrbot/.venv/Scripts/python.exe -X utf8 scripts/humanize_metrics.py --per-talker 400

# 让插件真回几条（只发文件传输助手），再按真人基线打分
astrbot/.venv/Scripts/python.exe -X utf8 _restore-20261111/humanize_eval.py \
    --tag v8 --run 7 --gap 18 --offset 24
```

## 二、三层改造

### 第 1 层：提示词（人格级）

`reply_style_prompt` 由 116 字的「每次回复至多三句话…复杂问题可无视」换成
653 字的《怎么说话——像拿手机手打聊天》，带 6 条真人样例与场景对照。
原文最后那句「可以无视这一要求」是长回复的直接许可，已删除。

必须走面板 API 写入，**直接改 `astrbot_plugin_private_companion_config.json`
会在下次启动时被内存镜像回写**：

```bash
python _restore-20261111/apply_style_v2.py --file _restore-20261111/style-block-v4.txt --apply
```

轮次记录（同 5 条话题复测）：

| 版本 | 中位字数 | ≤6 字 | 逗号密度 | 备注 |
|---|---|---|---|---|
| 原版 | 10 | 0.286 | 0.57 | 成段、逗号多 |
| v1 | 8.0 | 0.25 | 0.38 | 加规则与样例 |
| v2 | 9 | 0.4 | 0.0 | 分条正则加逗号 |
| v3 | 16 | 0.0 | 0.17 | 加情景样例 → 反而变长（副作用） |
| v4（结构切短） | 10 | 0.111 | 0.22 | 回到短句 |
| v7（2 字档 + 裸回应） | 8 | 0.364 | 0.0 | ≤3 字应答 0.091 |
| v8（v4 文案 + 2 字档） | 8 | 0.333 | 0.11 | ≤3 字应答 0.222、无标点 0.889 |

### 第 2 层：分条参数（让停顿变成真的两条消息）

- `segmented_proactive_max_segments` 3 → 4，`min_segment_chars` 8 → 2；
- **私聊有独立覆盖**：`segmented_proactive_private_max_segments` / `private_min_segment_chars`，
  只改全局对私聊无效；
- `segmented_proactive_regex` 加逗号：`.*?[。？！~…，,]+|.+$`（真人很少用逗号串长句）。

### 第 3 层：结构强制（不指望模型自觉）

1. `event_dispatch.py::_split_proactive_text` 里加 `_humanize_shorten`（主动消息路径）：
   超 16 字的气泡在逗号/顿号/空格处切开，最多 4 条；带引号书名号的创作摘录不动。
2. `main.py::_clean_segmented_reply_chunks` 里加 `_humanize_split_chunks`（被动回复路径）：
   按**本轮抽到的字数上限**把过长气泡拆成多条 chunk。
3. `main.py::_reply_length_budget_hint` 每轮随机抽一个硬上限写进提示词
   （15% 只回 1~2 字、45% ≤6 字、30% ≤12 字、10% ≤25 字），并把上限记到
   `self._last_budget_cap` 供第 2 条使用。测试时可用环境变量
   `HUMANIZE_FORCE_CAP=6` 强制一个上限。

开关：`humanize_short_bubbles`（默认 True）、`humanize_length_budget`（默认 True）。

## 三、踩过的坑

1. **锚点子串命中**：`"\n        if split_mode == \"words\":"`（8 空格）会命中 12 空格的
   `            if split_mode`，第一版补丁把 `if cleanup_enabled:` 的 body 打断 →
   `IndentationError: expected an indented block after 'if' statement on line 6123`。
   最终按「取原行缩进、从后往前替换」生成代码才对。
2. **热重载不可靠**：`Detected file changes` 之后不一定真的 `Loading plugin`；
   改完插件要 `Stop-Process` 11229 属主再 `pythonw -X utf8 scripts/_astrbot_entry.py run` 重启，
   确认 `Loading plugin astrbot_plugin_private_companion` 且 `Traceback` 为 0。
3. **只写提示词不够**：强制 6 字档实测中位仍 8，服从率约三成；必须配结构切分。
4. **配置文件会被回写**：所有插件设置都走
   `POST /api/plug/astrbot_plugin_private_companion/page/settings/update`（JWT 用
   `cmd_config.json` 的 `dashboard.jwt_secret` 自签 HS256）。
5. **重复话题污染评估**：同一会话连着注入相似话题，机器人会先吐槽「这句你刚说过」，
   导致测出来的长度偏长。`humanize_eval.py` 用 `--offset` 轮换 30 条话题缓解。

## 四、回滚

```bash
# 人格提示词回到原版
python _restore-20261111/apply_style_block.py --restore --apply

# 插件代码回滚（两处备份）
cp astrbot/data/plugins/astrbot_plugin_private_companion/event_dispatch.py.bak-shortbubble-<ts> .../event_dispatch.py
cp astrbot/data/plugins/astrbot_plugin_private_companion/main.py.bak-budget-<ts> / main.py.bak-passivecap-<ts> ...
```
