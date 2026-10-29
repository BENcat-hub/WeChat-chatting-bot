# 日程生成错误 / 缓存命中 / Token 消耗 排查与修复（2026-10-25）

目标（用户）：**排查日程生成错误原因，提高缓存命中率，降低 token 消耗。**

本文记录：现象与实测数据 → 四个层面的根因 → 已落地的修复 → 验证证据 → 待验证项与回滚方式。

---

## 1. 现象与实测数据

数据源：`astrbot/data/plugin_data/astrbot_plugin_private_companion/companions.json → token_usage`
（每条 `recent` 记录含 `task / success / prompt_tokens / completion_tokens / cached_tokens / estimated / error / prompt_chars`）。

2026-10-25 当日（修复前）：

| 指标 | 数值 |
| --- | --- |
| 陪伴插件总调用 | 142 次 |
| 失败 | 138 次（97%） |
| 记账 prompt tokens | 894,894 |
| 其中「估算」tokens | 871,151（97.3%） |
| 缓存命中 | 6,144 / 894,894 ≈ 0.7% |
| `daily_plan` | 39 次调用 / 38 次失败 / prompt 521,008 / completion 11,015 / cached 3,840 |

面板上「97% · 138」不是估算率，而是**失败率 97%、138 次失败**。

## 2. 根因一：日程生成「错误」= DeepSeek 402 欠费

`recent` 里 38 条 `daily_plan` 失败全部是同一句：

```
Error code: 402 - {'error': {'message': 'Insufficient Balance (request_id: …)',
                             'type': 'insufficient_balance_error'…}}
```

`daily_plan` 由 `_ensure_daily_plan(force=True)` 在 07:30 定时与「日程」指令时触发，`generate_daily_plan` 对格式错误/超时会**整份重发 prompt 最多 4 次**，欠费期间每次都是 402，于是「一次生成 = 4 次 × ~10K token 的重发」。

余额已核实（`https://api.deepseek.com/user/balance`，AstrBot 实际使用的 key）：

```
{"is_available": true, "balance_infos": [{"currency": "CNY",
  "total_balance": "49.55", "granted_balance": "0.00", "topped_up_balance": "49.55"}]}
```

→ **账户已充值，402 不再复现**（12:43:50 起出现成功记录）。这部分属于外部原因，代码层面无法修复，只能让它「不放大伤害」。

## 3. 根因二：失败调用按字符估算记入当日预算，反过来掐死日程与主动消息

- `token_budget.py` 在拿不到 usage 时用 `_estimate_token_count`（ASCII/4 + 非 ASCII 1）兜底，实测**高估约 35%**（同一 prompt 估算 13,445–13,543、真实 9,944）。
- 402 这类「计费前就被拒」的调用也会被记入 `totals`/`by_day`，而 1.0M 日限额就是从这里算的 → 871K 假消耗直接把预算吃光，`_llm_daily_budget_remaining()` 归零后，日程与主动消息会被软/硬限额提前 defer 掉。

**修复**：`token_budget.py` 新增

```python
_UNBILLED_LLM_ERROR_MARKERS = (
    "insufficient balance", "insufficient_quota", "error code: 401",
    "error code: 402", "error code: 403", "invalid_api_key",
    "incorrect api key", "unauthorized", "authentication",
    "insufficient permission", "no available provider",
    "未找到可用的 astrbot 默认模型 provider", "余额不足",
)

@classmethod
def _llm_error_is_unbilled(cls, error) -> bool: ...
```

并在 `_record_llm_usage` / `_record_external_llm_usage` 中，失败且命中上述特征时把 `prompt/completion/reasoning/total/cached/cache_read/cache_write` 全部清零后再记账（`calls` / `errors` 仍如实累计，便于看失败率）。

效果：402/401/无 provider 这类「零计费」失败不再消耗日预算；真正发了请求并被计费但没回 usage 的调用（如超时）仍按估算记，保持预算的保守性。

## 4. 根因三：prompt 中段的时间戳打断了缓存前缀（命中率只有 38% 的直接原因）

DeepSeek 的上下文缓存是**前缀匹配**：只有当本次请求的 prompt 前缀与上次一致（≥64 token 起算）才命中。

`planning.py` 的 daily_plan prompt 结构原本是：

1. A 段（静态说明，约 3.6–3.9K token，含「晚安 23:30」等固定作息）
2. **`当前时间：2026-10-25 12:43（上海）`** ← 分钟级，每次都变
3. B 段（硬约束块，含身份/日历/昨天记录，约 2.5K）
4. C/D 段（昨日回顾、灵感参考材料，约 2K）

实测成功那一次 `prompt 9,944 / cached 3,840`——**3,840 token 几乎正好等于 A 段长度**：缓存前缀在 `当前时间` 那一行断掉，后面的 B/C/D 全部无法复用，而且 4 次重试每次都因分钟变化从 0 重新建前缀。

**修复**：`planning.py` 中删除 A 段内的 `当前时间：{now}` 行，改为在**整份 prompt 最末尾**追加一行：

```python
f"\n请求时间戳（只用于判断昼夜、作息节奏和今天已经过到几点，不要写进任何输出字段）：{now}"
```

（`planning.py:1543` 附近；`now`/`now_info` 仍在顶部定义，`hour`/`minute` 等派生变量不受影响。）

效果：同一自然日内，A+B+C+D（约 8.5–9.5K token）整体成为可缓存前缀，只有最后一行时间戳和少量变量在变；**当天第 2 次及以后的重试/重算命中率应显著高于 38%（预期 90%+）**，跨天首次调用仍是冷启动（无法避免）。

## 5. 根因四：插件设置的 `max_tokens` 被 AstrBot 静默丢弃（输出无上限）

`core/star/context.py: llm_generate(**kwargs)` 会把参数透传给 `prov.text_chat(...)`，但
`core/provider/sources/openai_source.py: _prepare_chat_payload()` 里载体只有：

```python
payloads = {"messages": context_query, "model": model}
```

**`max_tokens` / `temperature` 等 kwargs 从未进入 payloads**，因此插件里遍布的 `max_tokens=8 / 80 / 260 / 1400 …`（`event_dispatch.py:5053` 只要 8 token、`planning` 的日程生成要 1500）全部无效，输出只受模型/服务端默认上限约束（实测 `daily_plan` 一次 completion 11,015 token，远超其 1,500 的预算）。

**修复**：在 `openai_source.py` 的 `_prepare_chat_payload()` 中、`payloads = {...}` 之后插入白名单合并（标记 `[dsh-patch]`）：

```python
# [dsh-patch] per-call generation params: 原版只透传 messages/model，
# 调用方传的 max_tokens/temperature 等会被静默丢弃；这里按白名单合并，
# 随后 text_chat 中「非 default_params 键 → extra_body」的逻辑会送出。
for _param_key in (
    "max_tokens", "temperature", "top_p", "presence_penalty",
    "frequency_penalty", "stop", "seed", "reasoning_effort",
):
    _param_value = kwargs.get(_param_key)
    if _param_value is not None:
        payloads[_param_key] = _param_value
```

配套逻辑：`text_chat` 里凡不在 `self.default_params`（= OpenAI SDK `create()` 的签名参数子集）中的键都会被挪进 `extra_body` 发送；`max_tokens` 不在 SDK 签名里，因此一定走 `extra_body` 到服务端。

### 5b. ⚠️ 这个补丁已经**回滚**（2026-10-25 14:24），原因如下

补丁在技术上完全有效：实测 `ProviderOpenAIOfficial.text_chat(prompt="…", max_tokens=8)` 对真实的
`https://api.deepseek.com/v1` 返回

```
TokenUsage(input_other=46, input_cached=0, output=8)      # 8 就是被截住的
completion_text = ""                                       # 但正文是空的
```

**正文为空**说明 `deepseek-flash` 是**带思考的模型**：8 个 token 的预算全被 reasoning 吃掉，真正要输出的
JSON/文本还没开始就截断了。插件里大量 `max_tokens=8 / 80 / 120 / 260`（`event_dispatch.py` 的
`group_air_reply_guard`、`atrelay.py`、`forward_message.py` 等）都是按「无思考模型」写的；一旦强制生效，
这些调用会集体变成空响应，等于把功能打坏。所以：

- 补丁已完整回滚，`openai_source.py` 恢复到原始长度 60,485 字节，`grep dsh-patch` = 0 命中，AST_OK，
  AstrBot 14:24 重启后 6185/11229 正常、err.log 0 字节。
- 结论：**「插件 max_tokens 被丢弃」是事实，但正确修法不是强制它生效，而是给这些短调用关掉思考**
  （`reasoning_effort`/`thinking` 一类的 provider 参数），这需要先确认 deepseek-flash 支持哪种开关，
  属于后续工作。
- 顺带记录了 `openai_source.py:1281` 自带的 `[cache-diag]` 日志：
  `prov=… model=… sys_len=… sys_sha=… tools=… msgs=… tail_len=… tail_hits=…` —— 这是现成的
  「前缀是否稳定」观测点，下一次真实 `daily_plan` 调用时可直接用它验证缓存修复。

### 5c. 最终上线的方案：短调用关掉思考（`[dsh-patch][short-call]`）

先确认 `deepseek-flash` 默认带思考（直连 API 实测，同一问题）：

| 请求 | completion | reasoning_tokens | content |
| --- | --- | --- | --- |
| 默认 | 15 | **13** | `2` |
| `thinking: {"type":"disabled"}` | 5 | 0 | `1+1=2` |
| `reasoning_effort: "none"` | 6 | 0 | `1+1=2。` |

也就是说插件里 `max_tokens=8/80/120/260` 这些判定类调用，预算大半被思考吃掉（这正是之前 8 token 时正文为空的原因）。所以改成**按上限分流**：

```python
# [dsh-patch][short-call] 短调用：插件按「无思考模型」写的 max_tokens<=200 的判定类调用
# 在 deepseek-flash 上会被 reasoning 吃空（实测 max_tokens=8 -> content 为空）。
# 这里对短上限调用：① 生效 max_tokens；② 关掉 thinking（reasoning 对这类判定纯浪费）。
# 大上限调用（>=1000，如日程/摘要）保持原样，避免截断。
_mt = kwargs.get("max_tokens")
if isinstance(_mt, int) and 0 < _mt <= 200:
    payloads["max_tokens"] = _mt
    _base = str(self.provider_config.get("api_base") or "").lower()
    if "deepseek" in _base:
        payloads["thinking"] = {"type": "disabled"}
```

实测（同一 provider 实例、真实 api.deepseek.com）：

```
max_tokens=8   -> TokenUsage(input_other=11, output=1)  text='2'        # 关思考 + 截住
max_tokens=1500 -> TokenUsage(input_other=37, output=72) text_len=61    # 不受影响（仍可思考）
```

收益：判定类短调用不再被思考吃空（功能恢复），reasoning token 归零；大调用（日程/摘要/正文）完全不动，不存在截断风险。

### 5d. 前缀构成实测（缓存到底能覆盖多少）

AstrBot 自带的诊断行 `[cache-diag]`（`openai_source.py:1264`）给出了真实请求的形状：

```
13:21:00 [cache-diag] prov=deepseek model=deepseek-flash sys_len=5257 sys_sha=f243ea05dce413cb tools=51 msgs=2 tail_len=1419
13:21:23 [cache-diag] prov=deepseek model=deepseek-flash sys_len=5257 sys_sha=f243ea05dce413cb tools=51 msgs=2 tail_len=9022
14:14:14 [cache-diag] prov=deepseek model=deepseek-flash sys_len=5257 sys_sha=f243ea05dce413cb tools=51 msgs=2 tail_len=8664
```

- `sys_sha` 三次完全相同 → **system prompt 是稳定的**（5,257 字符）。
- `tools=51` → 每次请求都带 51 个工具定义，是 prompt 里最大的一块静态内容（也是为什么实测 prompt_tokens 9,944 远大于插件 prompt 的 ~2.5K token）。
- 顺序是 `tools → system → user`，所以「工具定义 + system + 插件 prompt 的静态部分」全部位于可缓存前缀内。

再对插件 prompt 本身做离线渲染验证（同一份 live 数据、只改 `now`）：

```
build_daily_plan_prompt(stub, "2026-10-25 14:52")  -> 7021 字符，now 出现 1 次（偏移 7005）
build_daily_plan_prompt(stub, "2026-10-25 23:59")  -> 共同前缀 7016 / 7021 字符
```

即**除末尾 5 个字符（分钟）外逐字节相同**。正文里另外 7 处 `HH:MM` 都是模板里的静态示例/规则（17:00 规则、示例 JSON 里的 09:10/13:40/17:20），不是运行时时间。

顺带审计了「日程细化」（`build_detail_enhancement_prompt_section`）：它的 A 段开头也放 `现在时间：{datetime.now():%Y-%m-%d %H:%M}`，但 A 段本身随「当前段/当前事项」变化，且它前面还有 6~8K 字符的静态约束块（已可缓存），所以**不需要改**——改了也换不来缓存收益，反而要动 prompt 语义。

### 5e 实时 API 验证：同一份日程 prompt 第二次请求命中 96.3%（2026-10-25 14:56）

不再等 07:30，直接用**修复后真实渲染出来的日程 prompt**打 DeepSeek 两次（脚本 `logs/_probe_cache_live2.py`）：

| 调用 | prompt_tokens | cached_tokens | cache_miss | 说明 |
| --- | --- | --- | --- | --- |
| 第 1 次（冷启动） | 4,120 | 256 | 3,864 | 只有最前面 256 token 撞上了当天其它请求的公共前缀 |
| 第 2 次（同一份文本，间隔 3 秒） | 4,120 | **3,968** | **152** | **命中率 96.3%**，未命中的 152 token 是尾部的分钟级时间戳 |

`logs/daily_plan_prompt_rendered.txt` 就是那份 7,021 字符的渲染产物（7,021 字符 ≈ 4,120 token，约 1.7 字符/token）。
结论：修复后的日程 prompt 已经是「一份 4K 的稳定前缀 + 末尾几个会变的字符」，同日重试、纠偏重发都只按 152 token 计费，比修复前的 39% 命中率、以及失败时 100% 未命中，成本差约 25 倍。

## 6. 验证证据

| 项 | 证据 |
| --- | --- |
| 四个文件语法 | `ast.parse` / `py_compile` 全部通过（`COMPILE_OK`） |
| 时间戳已移位 | `grep 当前时间` 在 planning.py 的 A 段已无命中，末尾出现「请求时间戳（只用于判断昼夜…）」 |
| 未计费清零 | 单测桩：402/401/无 provider → True；500/超时/空 → False；记录一条 402 后 `totals.prompt_tokens` 只增加另一条 500 的估算值 |
| 插件改动生效 | AstrBot 14:17:34 重启（PID 61284），`0.0.0.0:6185` + `127.0.0.1:11229` LISTENING，bridge(24128) 重连 ESTABLISHED，`astrbot-20261025-141707.err.log` 0 字节 |
| max_tokens 补丁 | 打过并用真实 DeepSeek 调用验证（`max_tokens=8` → `output=8`），但**因正文被思考吃空已回滚**；现文件 60,485 字节、`grep dsh-patch` 0 命中、AST_OK |
| 短调用关思考补丁 | 真实 API 实测：`max_tokens=8` → `output=1, text='2'`；`max_tokens=1500` → `output=72, text_len=61`（不受影响） |
| 回滚后服务 | AstrBot 14:24 + 14:30 重启，`0.0.0.0:6185` + `127.0.0.1:11229` LISTENING，bridge 重连 ESTABLISHED，err.log 无异常 |

## 7. 待验证 / 后续可优化

1. **缓存命中率**：已在 5e 用真实渲染 prompt 实测到 96.3%（第二次请求 cached 3,968 / 4,120）；剩下仍要盯的是 07:30 那次**真实调度**的 `token_usage.recent` 里 `cached_tokens / prompt_tokens`（预期首次 30–60%、同日后继 ≥ 90%）。
2. **短调用关思考（下一步最大的省钱点）**：插件里 `max_tokens=8/80/260` 这类调用在 `deepseek-flash`
   上会被 reasoning 吃空（实测 8 token → 正文为空），现在只能「不生效」地白跑；要给这些 task 单独关闭思考
   或换非推理模型，才能既省 token 又拿到输出。
3. **计划的真实体量**：`companions.json → daily_plan` 落库后 96,223 字符 ≈ 25K token（10 条，每条平均
   9.6K 字符，含插件补的 lifecycle/actor 等字段），LLM 那次 completion 11,015 token 属于「思考 + 长正文」；
   若要把日程的 token 压下来，方向是让条目写短（prompt 里限定每个 activity 的字数），而不是压 max_tokens。
3. 进一步降 token 的空间（未做）：
   - B 段的「身份 / 日历 / 昨天记录」里有大段可压缩内容，可只发送与今天相关的日历项；
   - `_estimate_token_count` 高估 35%，若把比例校准到实测中位数，可让保守记账更贴近真实；
   - `daily_plan` 4 次纠偏重试可改为「失败后只重发解析错误片段 + 约束」，而不是整份 prompt。

## 8. 回滚

- `token_budget.py`：删除 `_UNBILLED_LLM_ERROR_MARKERS` / `_llm_error_is_unbilled` 及两处清零代码块。
- `planning.py`：把末尾时间戳行移回 A 段（不建议——这会直接回退缓存命中率）。
- `openai_source.py`：删除 `[dsh-patch]` 注释到 `payloads[_param_key] = _param_value` 的整段（注意别动 `self._finally_convert_payload(payloads)`）。

## 6. 2026-10-26 复盘：39% 是结构性比例，不是修复失效

### 6.1 真实调用数据（07:32:29，唯一一次 daily_plan 成功）
| 指标 | 数值 |
| --- | --- |
| prompt_tokens | 9,822 |
| cached_tokens | 3,840 |
| 命中率 | **39%** |
| completion_tokens | 8,538 |
| estimated | False |
| prompt 字符数（cache-diag tail_len） | 15,909（≈1.62 字符/token） |

历史累计 40 次 daily_plan：`prompt 634,208 / cached 35,328 = 5.6%`（含 10-25 的 402 失败样本）。

### 6.2 为什么还是 39%
`cached 3,840` 恰好等于 prompt 的**静态头部**：
开场语 → `source_protocol_block`（四级来源协议）→ `relationship_authority_guard` → `completion_budget_guidance` → 「生成要求」24 条 → JSON 格式示例。
紧接着的 `A｜硬约束` 一开始就是**按天变化的 `calendar_context`**，缓存前缀在此断掉，剩下的 B/C/D（当前状态、昨日摘要、记忆、习惯、最近日程……）约 6,000 token 每次都变。

→ 39% = 静态头部 3,840 / 总 9,822。想让比例上升，只能「扩大可缓存前缀」或「压缩动态尾部」，与 402/时间戳那类 bug 无关。

### 6.3 本次两项改动
1. **A 段重排**（`planning.py`）：把静态的「角色身份、生活背景与世界观」移到动态的「日期与当天性质」之前。硬约束语义不变（仍是 A 级），但可缓存前缀从此处开始延长一段身份文本。
2. **流式路径也加诊断**（`astrbot/core/provider/sources/openai_source.py`，标记 `[dsh-patch][cache-diag-stream]`）：原来 `[cache-diag]` 只在非流式 `text_chat` 里，而聊天回复走 `text_chat_stream`，**完全不可观测、也不进插件账本**。现在流式请求同样打印 `[cache-diag][stream] prov=… sys_len=… sys_sha=… tools=… msgs=… tail_len=…`，不落盘、只打一行日志。

### 6.4 今天的账本（2026-10-26 至 07:47）
| task | calls | err | prompt | cached | 备注 |
| --- | --- | --- | --- | --- | --- |
| yesterday_summary | 1 | 0 | 10,218 | 256 | 冷启动 |
| daily_plan | 1 | 0 | 9,822 | 3,840 | 39% |
| proactive_persona_judge | 3 | 0 | 6,527 | 3,456 | 53% |
| external_event_self_link | 2 | 0 | 2,609 | 2,176 | 83% |
| web_exploration_query/digest | 2 | 0 | 2,172 | 896 | — |
| 合计 | 11 | **0** | 33,180 | 10,624 | 32% |

404 风暴已消失（余额已充、key 已轮换）。

### 6.5 token 到底花在哪（重要）
- 非流式后台任务（有 cache-diag 记录）：7 天合计 **48.5M 字符 ≈ 30M token**，单条最大 **5,255,041 字符**（2026-10-24 10:23，`sys_len=4/tools=0/msgs=1` 即 private_companion 的 `_llm_call` 形态）。
- 只有 18 次请求 `msgs=4`，其余 1,334 次都是单条 user 消息——**巨型上下文被塞进一条消息**，天然无法缓存。
- DeepSeek 面板 10/25：166 次请求 / **95,288,109 token**，平均 574K token/次；而插件账本同日只有 187 次、prompt 94.7 万。两者差 100 倍 → **大头在流式聊天回复**（`text_chat_stream`），此前完全没被记录。

### 6.6 后续可做（按性价比）
1. 用 6.3 的 stream 日志统计聊天侧 `tail_len` 分布，定位是哪个上下文块把 prompt 撑到几十万 token。
2. 给聊天侧 `<private_companion_context>` 拼接加上限（例如 12K 字符），超限只保留最近 N 轮 + 摘要。
3. 把流式调用的 `usage`（已带 `include_usage`）也写进 `token_usage`，让面板数字与账单对得上。
4. `_estimate_token_count` 目前高估约 35%，可按 1.62 字符/token 校准。
