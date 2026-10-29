# DeepSeek 前缀缓存命中率排查与修复（2026-10-20）

适用对象：AstrBot 4.29.1 + private_companion 6.7.0 + memory_companion + affection 1.3.1，
provider 为 `deepseek`（`deepseek-flash` / `deepseek-v4-pro`）。

## 1. 症状

`astrbot/data/data_v4.db` 的 `provider_stats` 表里，主对话请求的缓存命中率长期在
**9%–12%** 摆动，而同一会话里偶尔出现 88%–91% 的行：

```
id=337 miss=16888 hit=1792  → 9.6%
id=338 miss=14753 hit=1920  → 11.5%
id=340 miss=  1475 hit=15360 → 91.2%
```

`hit≈1920` 正好等于 55 个工具的 token 数，说明**只有 tools 块被缓存，
system prompt 与整段历史全部重算**。

## 2. 根因

DeepSeek 的缓存是「请求前缀逐字节匹配」。system prompt 排在最前（
`astrbot/core/provider/sources/openai_source.py` 的 `_prepare_chat_payload`
把 system 插到 messages[0]），因此**只要 system 里有任何一个字符每轮变化，
后面十几 K 的历史全部失效**。

排查发现三处在每轮改写 system 的注入：

| # | 位置 | 变化来源 |
|---|------|----------|
| 1 | `astrbot_plugin_affection/main.py:_inject_emotion_content` | 情绪面板的小数（力比多/攻击性/好感）每轮漂移；原代码想把提示塞进 `req.messages` 最后一条 user，但钩子阶段 `req.messages` 还没构建，于是**永远走兜底分支写进 system_prompt** |
| 2 | `astrbot_plugin_private_companion/tts_enhancement.py` TTS 规则/后处理块 | 用 `plan.materialize_system_block(..., placement=PLACEMENT_DYNAMIC_SYSTEM)` **绕过** `passive_injection_position` 配置，且同一段规则会被 materialize 多次，`_strip_registered_materialized_blocks` 又跳过 `ExactText` 块 → system 里出现 **3 份重复**的 TTS 规则 |
| 3 | `astrbot_plugin_memory_companion/core/service.py` 三个工具契约 | `<MemoryCompanion-Reconstruction-Contract>` 是按「这条消息像不像在回忆」动态决定是否注入的，注入时还带一行动态数字（本轮选出 N 条记忆、最多 K 步）→ 只要用户一句「今天…」system 就变长 450 字符 |

## 3. 修复

原则：**system 只放逐字节稳定的内容；所有「本轮才有」的东西走消息尾部**
（AstrBot 4.29 的 `extra_user_content_parts`，最终拼在本轮 user 消息末尾，
或者 private_companion 的 `PLACEMENT_TURN_TAIL`）。

1. `astrbot_plugin_affection/main.py`
   `_inject_emotion_content` 改为三级通道：
   `req.messages` 最后一条 user → `req.extra_user_content_parts`（`TextPart` +
   `mark_as_temp()`）→ 兜底 system。返回实际落点字符串便于排障。
2. `astrbot_plugin_private_companion/tts_enhancement.py`
   两处 TTS 规则/后处理块的 `materialize_system_block(placement=PLACEMENT_DYNAMIC_SYSTEM)`
   改为 `append_dynamic_tts_fragment(...)`，走 `_place_conversation_prompt_section`
   的配置化路径（`passive_injection_position=prompt` → turn tail），并把实际
   「注入位置」写进注入记录。
3. `astrbot_plugin_private_companion/conversation_injection_plan.py`
   - `_render_system` 删除旧 owned 文本改为**循环删干净**，否则同一 req 被多次
     render 时副本逐轮累积；
   - `_strip_registered_materialized_blocks` 不再跳过 `ExactText` 块（tool contract
     仍跳过），使 TTS 之类的规则块也能被清理。
4. `astrbot_plugin_memory_companion/core/service.py`
   新增 `_append_turn_tail_instruction()`；三个契约（remember / core memory /
   reconstruction）优先追加到 `extra_user_content_parts`，通道不可用时才回落 system。

## 4. 取证工具

- **落盘诊断**（临时补丁，装在 site-packages，AstrBot 升级会被覆盖）：
  `astrbot/.venv/Lib/site-packages/astrbot/core/provider/sources/openai_source.py`
  `text_chat()` 里，payload 构建完成、发出请求之前，把
  `logs/cache-diag/sys-<HHMMSS>-<sha16>.txt`（system 全文）、
  `tail-<HHMMSS>-<sha16>.txt`（最后一条消息末尾 8K 字符）、
  `meta-*.json`（sys_len / tools 列表 / msgs / tail_hits）落盘，各保留最近 40 份。
  用 `ASTRBOT_CACHE_DIAG=0` 关掉。
  日志行：`[cache-diag] prov=… sys_len=… sys_sha=… tools=… msgs=… tail_len=… tail_hits=…`
- **入站注入脚本**：`scripts/cache_diag_inject.py` 直连 OneBot 反向 WS
  （`ws://127.0.0.1:11229/ws`，头 `X-Self-ID` + `X-Client-Role: Event`），
  伪造一个虚拟联系人发私聊事件，走完整插件管线，回复只回给本连接、不会真发微信。

## 5. 验证结果（2026-10-20 08:2x–08:4x）

四次连续私聊（同一虚拟会话），修完后 system 长度与哈希**逐轮完全一致**：

```
sys_len=8165  sha=a6563117520068dd   ← 4 轮全部相同
```

`provider_stats` 对应行：

```
id=348 miss=1707 hit=15744 → 90.2%
id=349 miss=2335 hit=15744 → 87.1%
id=350 miss=2058 hit=15872 → 88.5%
id=351 miss=5162 hit=16000 → 75.6%   ← 重启后首轮（新前缀，冷启动）
```

尾部 dump 里能看到原本污染 system 的内容都挪到了末尾：

```
tail-083614-...txt  tail_hits=MemoryCompanion-Reconstruction-Contract,private_companion_tts
```

## 6. 仍然存在的「冷启动」损耗

- 每次重启/换前缀后的**第一条**请求必然 miss（缓存是前缀哈希，前缀没出现过）。
- 每个新会话（新用户/新角色卡/新 system）都要重新预热一遍。
- 每轮新增的「本轮消息 + 本轮尾部注入」约 1.5–2.5K tokens 不会被缓存，
  这是增量内容的固有成本；想再降只能减少每轮尾部注入的体量。

## 7. 回归测试

```
cd astrbot/data/plugins
set PYTHONPATH=%CD%
..\..\.venv\Scripts\python.exe -m unittest ^
  astrbot_plugin_private_companion.tests.test_conversation_injection_plan ^
  astrbot_plugin_private_companion.tests.test_conversation_injection_plan_stage2 ^
  astrbot_plugin_private_companion.tests.test_module_conversation_injection_plan
# 43 tests OK

cd astrbot/data/plugins/astrbot_plugin_memory_companion
..\..\..\.venv\Scripts\python.exe -m unittest tests.test_active_reconstruction
# 13 tests OK
```

## 8. 后续可查

真实微信会话（`umo=default:FriendMessage:<wxid>`）下一轮对话后，
用下面这条 SQL 对比修复前后：

```sql
select id, created_at, provider_model, token_input_cached, token_input_other,
       round(100.0*token_input_cached/(token_input_cached+token_input_other),1) as hit_pct
from provider_stats
order by id desc limit 20;
```

如果真实会话仍出现 `hit≈1920` 的行，说明还有第四处每轮改写 system 的注入，
用同一个 diag 目录里的 `sys-*.txt` 按时间对齐，直接 diff 两轮的 system 即可定位。
