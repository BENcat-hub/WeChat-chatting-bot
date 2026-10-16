# 生图质量客观评测与已采纳改动（2026-10-16 凌晨）

本文件记录「不用主 agent 上下文也能复现」的评测方法，以及每次实验的结论。
所有实验产物在 `logs/image-eval*.jsonl`，图片在 `logs/jimeng-out/eval/`。

## 1. 免费模型评测工具链

| 脚本 | 作用 | 产物 |
| --- | --- | --- |
| `scripts/eval_gen.py` | 生成 A/B：`--arm 1024x1024 --repeat 3`、`--prepend/--avoid-extra/--pos-extra/--prompt`、`--deliver WxH`（超采样后降采样） | `logs/jimeng-out/eval/*.png`、`logs/image-eval.jsonl` |
| `scripts/eval_vl_score.py` | 免费 VL 模型盲评 6 轴（head_proportion / hands / face / style / anatomy / finish，1–10 分） | `logs/image-eval-vl.jsonl` |
| `scripts/eval_vl_watermark.py` | 检测伪社媒水印（微博/B站/@账号/日期戳） | `logs/image-eval-watermark.jsonl` |
| `scripts/free_llm.py` | 本机免费模型网关客户端（`http://127.0.0.1:18899/v1`，key = `%APPDATA%\dsh-desktop\harness\our-free-model\settings.json` 的 `forwardKey`） | 直接打印 |

可用免费模型：`nvidia/nemotron-3.5-super-vl:free`（视觉，实测可用）、`kilo-auto/free`、`stealth/glyph-cluster`、`liquid/lfm-2.5-2.6b:free`。
OpenCode 路由的 `*-free`（`mimo-v2.6-flash-free`、`deepseek-v4.1-flash-free`、`nemotron-3.5-lightning-free` …）一律 502 `only from within OpenCode`，别用。

坑：VL 模型有 reasoning token，`max_tokens` 要给到 2500，否则 `content` 为空、JSON 解析失败；Windows 控制台是 GBK，脚本里要 `sys.stdout.reconfigure(encoding="utf-8")`，否则打印模型输出的中文/符号会 `UnicodeEncodeError` 崩掉整轮扫描。

## 2. 即梦官方文档要点（4.0 接口文档）

- `prompt`：中英文均可，**建议 ≤800 字符**，过长有概率出图异常或不生效；除引号外不建议特殊符号（方括号段落标题也算，提交前已压平）。
- `size` / `width,height`：面积 ∈ [1024², 4096²]，宽高比 ∈ [1/16,16)；**建议生成 2K 以上，过小分辨率较大概率人脸效果不佳**。
- `scale`：文本影响度，默认 0.5；**实测 4.6（`jimeng_seedream46_cvtob`）带 `scale` 提交成功但 GetResult 直接 HTTP 500 / code 50500**，故服务端不发该参数（见 `scripts/jimeng_image_server.py`）。
- `force_single`：对延迟/价格敏感建议 true（本地服务按 `n<=1` 自动开）。
- `image_urls`：≤10 张、JPEG/PNG、≤15MB、≤4096²、宽高比 [1/3,3]；输出张数上限 15 − 输入图数。

## 3. 实验结论

### 3.1 超采样（2048 生成 → 1024 交付）：收益很小，暂不采纳
n=3 盲评：`super2k` 总分 45.0 vs `native1k` 44.0（face 9.0 vs 8.33，其余基本持平）。代价 +6~8s、3MB 原图。→ 保持 1024 生成。

### 3.2 比例词前置：采纳
n=3 盲评：`baseline` 43.67 / `propfirst`（前置 "Petite slender teen girl, small head, about 7 heads tall…"）45.67 / `propfirst-portrait`（1024x1280 同词）45.67。head_proportion 6.0 → 6.67，anatomy 6.33 → 7.0。→ 写进 `image_runtime.py`（见下）。

### 3.3 伪社媒水印：**主要质量缺陷，已修**
扫描 12 张基线图：**6/12 带水印**（微博 logo + @账号、B站水印、日期戳、@YanaseShin 等，集中在底部/右下）。
加固提示词（负面点名 weibo/bilibili/social media watermark、username handle、date stamp…，正面加 "Clean unmarked picture…"）后：**评测 0/6、生产链路 0/3**。

### 3.4 写词模型从 pro 换成 flash：采纳（延迟大降、质量不降）
生产链路耗时拆解（同一个 `astrbot-*.log`）：
- 图片上游生成稳定 **≈18.5s**；
- 「排障测试开始 → 生图开始」这段（写词模型 + 参考图选择）原本 **35s / 129s / 85s**，是主要瓶颈。

把 `PHOTO_PROMPT_PROVIDER_ID` 从 `xiaomi-token-plan/mimo-v2.6-pro` 改成 `xiaomi-token-plan/mimo-v2.6-flash`（配置两处：`astrbot_plugin_private_companion_config.json:156` 与 `:912`）后：
- 前置阶段降到 **10.8s / 37.4s**，整张 **31.3s / 57.7s**（原 55–148s）；
- n=2 vs n=3 盲评：flash 总分 **47.5** vs pro **45.0**（未变差，样本小，差异在噪声内）。

## 4. 已采纳的生产改动

1. `astrbot/data/config/astrbot_plugin_private_companion_config.json:156` 与 `:912`：`PHOTO_PROMPT_PROVIDER_ID` → `xiaomi-token-plan/mimo-v2.6-flash`（写词模型提速，质量不降）。

`astrbot/data/plugins/astrbot_plugin_image_companion/image_runtime.py` `_apply_photo_generation_prompt_format()`：

- `image_runtime.py:11039` `proportion_hint`（比例前置词）→ 拼进 natural_language / traditional 两种格式的正面提示词开头；
- `image_runtime.py:11048` `watermark_positive`（"Clean unmarked picture…"）；
- `image_runtime.py:11049` `watermark_negative`（水印负面词表）→ 追加到负面提示词末尾（也就是本地服务按 800 字符截断时会保留的尾部）。

验证：面板排障自拍测试 3 次，`logs/jimeng-server-requests.jsonl` 里请求提示词已含上述两段，出图 `0/3` 水印，比例正常。

## 5. 仍未解决的缺陷（后续实验方向）

1. 手指/手部 5–7 分（拇指与杯柄融合、放在桌上的手缺指节）。
2. 头身比 5–8 分波动，跪姿/坐姿时更容易偏大头。
3. 鲸尾与腰部的衔接读起来不自然（角色设定固有难点）。
4. 插件链路单张耗时 55–148s，而直连服务只要 15–27s，差值需要拆解（写词模型耗时？下载/post 处理？）。
5. ~~上游偶发 `URLError WinError 10060` 超时 → 本次生图失败（需下一条消息重试）。~~ 已修：`jimeng_image_server.py` 的 `volc_call(..., attempts=3)` 对网络错误与 429/5xx 做 3 次退避重试（4xx 不重试），重启后一次 80s 的成功出图即为重试救回。

## 6. 提示词预算重构（2026-10-16 01:0x，已上线）

问题：真正的最终拼装点是 `photo_prompt_context.py` 的 `_assemble()`，它在
`image_runtime._apply_photo_generation_prompt_format()` **之后** 才把四组分区拼起来。
所以生产提示词一直在 4200 字符左右，被上游 800 字符上限截断，而"身份契约/姿势轮换"
正好落在被丢掉的中段。

改法（`photo_prompt_context.py`）：

- `_PROMPT_CHAR_BUDGET = 786`（原 790，第 20 节改成三段式后留出分隔符与生产侧 +4 字符的余量）、
  `_TAIL_NEGATIVE_BUDGET = 260`；
- 新增 `_critical_requirements_tail(negative)`：把**必须存活**的要求固定在尾部——
  `Small head, about 7 heads tall, long legs.` / 蓝发蓝瞳身份锚 / `Keep the reference face,
  hair colour, whale-fin ears and whale tail.` / 随机 `Pose override: framing …; pose …` /
  `Hands with five clear fingers, not oversized.` / `No watermark, logo, handle or caption.` /
  `Avoid …`（负面词 260 字符尾部）；
- natural_language 分支不再拼 `[分区标题]`，改为**三段式**：`head`（头预算 = 786 − 尾部长度，按边界裁剪）
  + `\n\n` + 关键要求段 + `\n\n` + `Avoid …` 否定段，最后再兜一次 800（**不要**退回单段平铺，
  理由见第 20 节）。

实测（排障自拍）：提示词 `4201/4178` → `767/762/771/770/751/749/776/748`，`prompt_clamped`
事件消失；水印 `0/4`；鲸尾重新出现；补上 `Long blue hair with light-blue gradient tips,
blue eyes.` 后蓝发 `3/3`（此前一轮出现过 1/2 黑发漂移）。

## 7. 手部加固定稿 + `clamp_prompt` 上界 bug（2026-10-16 01:2x）

手部三臂实验（各 n=3，同一场景/参考图，免费 VL 盲评总分）：

| 臂 | 提示词差异 | 总分 | hands | anatomy | head |
| --- | --- | --- | --- | --- | --- |
| A_current | 现状 | 45.0 | 6.67 | 5.67 | 6.67 |
| B_handpos | 只加正面手部描述（五根细指/指节/指甲） | 46.67 | 6.33 | 7.33 | 7.0 |
| **C_handneg** | 正面 + **具体手部负面词** | **49.33** | 7.0 | 7.33 | 7.33 |

结论：正面描述基本无效，**负面词表有效**。已采纳到
`photo_prompt_context.py:_TAIL_NEGATIVES`（`long fingers / elongated fingers / oversized hand /
claw hand / fused fingers / hidden hands / extra fingers` + chibi/头身比 + 水印词），
并且排在尾部最末——尾部保留裁剪会把它留下来。

顺手修掉一个真 bug：`jimeng_image_server.py:clamp_prompt()` 旧实现在尾部（`Avoid …`）
较长时 `budget = max(int(limit*0.6), limit - len(tail))` 会超预算，实测产出 **823 字符**，
上游直接 400 `invalid param: prompt can not be more than 800 chars`，那一轮实验 3/3 全废。
现在改成 `budget = max(120, limit - len(tail))` 并在返回前兜底再裁一次；三个实验提示词
clamp 后为 `674 / 675 / 737`，全部 ≤ 800。

## 8. 改动前后生产链路对比（排障自拍，盲评）

| 组 | n | 总分 | head | hands | face | style | anatomy |
| --- | --- | --- | --- | --- | --- | --- | --- |
| before_budget（01:04–01:07） | 4 | 48.0 | 7.25 | 6.5 | 8.75 | 9.25 | 7.75 |
| **after_budget（01:13–01:22）** | 3 | **50.0** | 8.0 | 7.0 | 9.0 | 9.67 | 7.67 |

没有任何一项变差，比例与手部都涨。明细见 `logs/jimeng-out/eval/vl-budget-ab.txt`。

## 9. 轮换机制没被预算裁剪掉（回归检查）

`scripts/eval_vl_describe.py` 让免费 VL 模型逐张描述出图（服装/姿势/场景/发色），
最近 6 张排障自拍的结论是 **6 张 6 套不同服装**（露脐+短裤 / 女仆裙 / 围裙背带裙 /
衬衫+马甲+短裙 / 带鲸尾女仆裙 / 荷叶边女仆上衣），姿势也各不相同（站立镜面自拍、
双臂高举、趴着托腮、坐地举手机、近景自拍）。说明服装轮换与姿势轮换在裁剪后仍然生效。

## 10. 前置耗时定位与修复（已完成）

先用 `image_companion_bridge.py` 的打点排除桥接层（`reference_paths=0.0s read_refs=0.0s`），
再用日志时间轴定位到真正吃时间的是 `image_runtime._analyze_photo_reference_intent_async()`
——**每张自拍/人像都要额外等一次 LLM 意图分析**（`task=photo_reference_intent`），而规则层此时
已经给出保守结论 `identity`，LLM 只是重复确认。实测该空档 **24.8s / 155s**（provider 慢时差 6 倍），
并且与「刚 reload 插件」无关（reload 后重测仍为 0.9s）。

改动（`image_runtime.py:15138` 附近、`image_runtime.py:15167`）：
- 自拍/人像（`selfie/portrait/自拍/人像`）直接返回规则层结论，不再调 LLM；
- 其它工作流保留 LLM 意图分析，但包 `asyncio.wait_for(..., timeout=20.0)`，超时回退规则层。

效果（排障自拍，同一条路径）：

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 「测试开始 → 生图开始」空档 | 24.8s / 155s | **0.9s** |
| 整张墙钟 | 58.7s / 186s | **22.3s** |
| 上游出图 | 18.6s | 19.2s |

参考图仍照常生效（`reference_used=True`、`参考图提交 reference_count=1`），身份与比例正常。

## 11. 超采样大样本定论：不采纳

n=6/臂（同一提示词、同一人设裁剪参考图；1024 原生 vs 2048 生成后 Lanczos 降到 1024）：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| native1k | **6.83** | **7.0** | **8.83** | **9.17** | **6.83** | **8.67** | **47.33** |
| super2k | 6.17 | 6.67 | 8.33 | 8.83 | 6.33 | 8.33 | 44.67 |

超采样**每一轴都更差**，成本还更高（20.3s / 2.9MB vs 14.8s / 0.93MB）。结论：保持 1024 原生，
不引入超采样。明细 `logs/jimeng-out/eval/vl-ss2.txt`。

## 12. 仍然待办

1. 手部仍是最弱项（6.5–7.0）：正面描述+具体负面词有小幅收益，但依赖随机性；可考虑结构性约束
   （减少手持物体、让手放在不遮挡的位置）。
2. 鲸尾与腰部衔接仍被反复标为不自然（16 次提及）；上一轮「尾巴在身后、从后腰自然长出」措辞无效。
3. 上游偶发 `URLError 10060` / HTTP 500 50500（已有 3 次退避重试，重试成功则用户无感）。
4. 写词模型（`mimo-v2.6-flash`，`task=photo_prompt`）自身耗时未单独测量；若要再压时间，
   可做「关掉写词、直接用人设模板」的对照实验。

## 13. 手部写词要求 14 + 鲸尾负面词：两次 A/B 都是阴性（2026-10-16 01:3x–01:4x）

**手部（写词要求第 14 条）**：让写词模型在自拍里追加手部要求（手要在画面里、别写"双手紧紧握住"、
必须拿东西时写"一只手从侧面轻轻拿着、手指分开"）。生产各 n=3 盲评：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| after_budget（旧） | 7.33 | **7.0** | 8.67 | 9.33 | 8.0 | 8.33 | 48.67 |
| hands14（新） | 7.33 | **7.0** | 9.0 | 9.33 | 8.0 | 9.0 | **49.67** |

hands 轴完全没动（7.0 vs 7.0），finish 略好（n=3 的 1 分≈噪声）。结论：**保留**（无害、措辞更具体），
但不指望它修手。明细 `logs/jimeng-out/eval/vl-hands14.txt`。

**鲸尾衔接**：在负面词里追加 `mermaid tail replacing her legs, tail fused with her legs or waist`，
并把负面词裁剪上限 250→300。各 n=3：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hands14 | 7.0 | **7.0** | 9.0 | 10.0 | 7.33 | 9.0 | **49.33** |
| tail14 | 7.0 | 6.0 | 8.67 | 9.0 | 7.67 | 8.67 | 47.0 |

两臂的缺陷列表里都仍有「Tail attachment looks unnatural / Mermaid tail…」，说明**措辞没解决衔接问题**，
总分还掉了 2.3 → **已回退**（`image_runtime.py` 负面词恢复三项、裁剪上限回 250），只保留
`identity_hint` 里「尾巴从后腰长出、腿保持人类」的身份锚。明细 `logs/jimeng-out/eval/vl-tail14.txt`。

**二次修手 pass：不采纳（2026-10-16 01:4x）**。把已生成的图当参考图、用「只重画手」的提示词再跑一次
（`eval_gen.py --ref <已生成图> --prompt <修手词>`，14–19s/次）。优点是**构图与人设几乎完全保留**（同一张
脸的同一姿势），但 hands 轴没提升：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| before（2 张源图） | 8.0 | **7.0** | 9.0 | 10.0 | 8.0 | 9.0 | 51.0 |
| after（4 张修手图） | 7.25 | **6.75** | 8.75 | 9.75 | 7.25 | 8.5 | 48.25 |

注意 before 是我从高分图里挑的两个源（有选择偏差，n 也不对等），所以这只能说明「没有明显收益」，
但也足以否决「为修手再加一次生成」的性价比。明细 `logs/jimeng-out/eval/vl-handfix.txt`。

**下一步（正在跑）**：手部是**场景结构性**问题还是提示词问题？两臂对照——A「举着手机自拍」vs
B「手机架起来、双手空闲可见」（另外同提示词换 `jimeng-4.0` 看模型差异）。

## 14. 手部是结构性问题：让双手空闲（已采纳，2026-10-16 01:4x–02:0x）

三臂（同一 identity/比例/负面词，各 n=3，1024x1024，人设参考图）：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hold46 举着手机自拍 | 6.0 | 7.0 | 8.0 | 9.0 | 6.67 | 8.0 | 44.67 |
| hold40 同上但 `jimeng-4.0` | 5.67 | 7.0 | 8.0 | 9.0 | 6.67 | 8.33 | **44.67** |
| prop46 手机架起来、双手空闲可见 | **7.33** | **7.33** | **8.67** | **9.67** | **8.0** | **8.67** | **49.67** |

- **结构性约束有效**：不让她攥着手机，头身比 +1.3、anatomy +1.3、hands +0.33，总分 +5。
- **`jimeng-4.0` 与 4.6 没有差别**（44.67 vs 44.67），且这次 4.0 反而更慢（19–31s vs 18.6s）→ 保持 4.6。
- 生产链路复核（排障自拍，改成双手空闲措辞后 n=6 池化 vs 举手机 n=3）：**49.5 vs 46.33**，
  head 7.67/7.0、anatomy 7.67/7.0、style 9.83/9.0，hands 两臂都是 6.67。结论：采纳；手部本身仍没救
  （VL 仍常写 "elongated/claw-like fingers"），但整体观感与比例更好。
- 副作用与修复：改用三脚架后模型会在支架上加**假商标/文字**（VL 缺陷里出现 `ImmemKoff`、墙上文字）。
  把措辞改成「手机架在**无标识**的小三脚架上」，并把负面词扩到
  `brand logo on objects, printed label`、正面句改成 `No watermark, logo, label, handle or caption on the image or on any object.`
  → 复测水印扫描 **0/3**（改前那一批 3 张里至少 1 张被扫出、VL 又额外提到 2 张有文字）。
- 明细：`logs/jimeng-out/eval/vl-handsB.txt`、`vl-handsfree-prod.txt`、`vl-handsfree6.txt`。
- 注意 NL 波动：同一批 hold46 图两次盲评分别 44.67 / 46.33，**n=3 的 1–2 分差异不可当真**。

## 15. 全类型覆盖 + 连续生成稳定性（2026-10-16 02:0x）

**其他 kind 也受保护**：

| 测试类型 | 墙钟 | 最终提示词 | 伪水印扫描 |
| --- | --- | --- | --- |
| 自拍 `image_generation_selfie` | 22–60s（n=12 平均 28.7s） | 792–798 字符（即梦服务器侧 752–778） | 0/3（"无标识三脚架"修好后） |
| 纯文生图 `image_generation_text2img` | 92.7s（其中上游 89.6s） | 797 | 0/1 |
| 自动 `image_generation`（落到自拍） | 35.3s | 794 | 0/1 |

结论：800 字符预算与「无水印/无商标道具」尾部对三类入口都生效；本地即梦服务器侧实际提交长度 752–778，
离 800 有 20+ 字符余量，**没有一次生产 clamp**（最近一次 clamp 是 01:27 的评测臂，1128→715）。

**连续生成 soak**：同一排障自拍连打 12 次，**12/12 成功**，墙钟 22.1–39.5s（平均 28.7s，min 22.1 / max 39.5），
无一次需要重试、无 5xx、无超时。说明退避重试补丁后的链路在夜间负载下是稳的。
明细 `logs/jimeng-out/eval/soak-handsfree.json`。

**正在跑**：机位（eye-level / high-angle / low-angle）对头身比的影响；鲸尾「露出多少」对 anatomy 的影响
（base / partial / none 三臂）。

## 16. 机位与鲸尾：两个弱信号（2026-10-16 02:1x）

**机位**（各 n=3，同提示词只改机位措辞）：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| high-angle 俯拍 | **7.0** | **7.0** | 8.67 | 9.33 | 6.33 | 8.67 | **47.0** |
| eye-level 平视中景 | 6.67 | 6.33 | 8.33 | 8.67 | 6.67 | 8.33 | 45.0 |
| low-angle 微仰拍 | 6.67 | 6.0 | 8.0 | 9.0 | 6.67 | 8.0 | 44.33 |

原先猜「俯拍会放大头」，实测**不成立**（俯拍的头身比分数最高），差异全在噪声范围内 → **不改机位策略**。

**鲸尾露出程度**（各 n=3）：

| 臂 | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| none 不出现尾巴 | **7.33** | 7.0 | 8.67 | **9.33** | **7.67** | 8.33 | **48.33** |
| base 尾巴挂在后腰 | 6.67 | 7.0 | 8.67 | 8.67 | 7.0 | 8.33 | 46.33 |
| partial 大部分在身后、部分出画 | 6.67 | 6.67 | 8.33 | 8.67 | 7.0 | 8.0 | 45.33 |

弱信号：**不画尾巴时分数最高（+2）**，「大部分藏起来」反而最差（模型仍必须画出衔接点，还少了尾巴的构图
支撑）。差异在 n=3 噪声内，但方向与「尾巴衔接是结构性难点」一致。**不改人设**（鲸尾是角色签名特征，
删掉要用户决定）；只记录代价：尾巴大约值 -1～-2 分，且缺陷几乎总出现在尾巴与裙摆/后腰的衔接处。

## 17. 上游「额度/权限拒绝」与本地冷却闸门（2026-10-16 02:20 起）

**症状**：02:20:03 起，即梦上游对**所有**请求返回

```
火山接口 HTTP 401（action=CVSync2AsyncSubmitTask / CVSync2AsyncGetResult，第1/3次）：
{"code":50400,"data":null,"message":"Access Denied: Access Denied" / "Access Denied: api forbidden",
 "request_id":"20261016022444636313E5654D3FD5B953","status":50400,"time_elapsed":"214.383µs"}
```

**证据（`logs/jimeng-server-requests.jsonl`，530 行）**：

- 全夜 `request` 202 次、`done` 187 次、`error` 15 次；**逐小时成功率 00 时 98% / 01 时 96% / 02 时 91%**，
  说明签名与凭据本身没问题，02:20 之前一切正常。
- 02:20 之前只有 400（参数类）与 1 次 `WinError 10060` 网络错误；**401 只出现在 02:20 之后，共 4 条**。
- 最后一次成功是 **02:19:44**，随后连轮询 `CVSync2AsyncGetResult` 都 401 —— 两种 action 一起被拒。
- `secrets/volcengine_ak_sk.txt` 未变动；签名 host/service/region（`visual.volcengineapi.com` / `cv` /
  `cn-north-1`）未变动 → 判断为**上游侧额度或权限被拒**，最可能是免费/试用调用量触顶
  （整夜 187 次成功 + 15 次失败 ≈ 202 次调用）。

**已做的工程加固**（`scripts/jimeng_image_server.py`，2026-10-16 02:24 重启生效 PID 51784）：

- `note_access_denied()` / `access_denied_snapshot()` / `clear_access_denied()`：遇到 401 或响应体含
  `50400` / `Access Denied` 时**不再重试**，并开启 `ACCESS_DENIED_COOLDOWN_SECONDS = 300` 冷却窗口。
- 冷却窗口内新请求**不碰上游**，直接回 502：

  ```json
  {"error": {"type": "upstream_access_denied", "retry_after": 300,
             "message": "jimeng upstream access denied (retrying suppressed): ..."}}
  ```

  实测：第 1 次真打上游耗时 0.6s → 冷却生效；第 2 次 **0.0s** 快速失败，JSONL 记 `cooldown_block`。
- 新增 `GET /v1/status`（也接受 `/status`）：返回冷却是否生效、剩余秒数、首次/最近时间与原始 detail，
  排障不用再翻 JSONL。

**待办 / 观察**：冷却过期后第一次请求会自动重新探测上游；若仍连续 401，需要到火山引擎控制台确认该 AK 的
「图片生成（CVSync2Async）」额度是否用尽、试用是否到期。下一个探测：**换 `req_key` 是否也 401**
（`jimeng_t2i_v40` 纯文生图 / `jimeng-4.0`）——若文生图仍可用，说明是图生图能力级封禁，生产可临时降级为
无参考图生成；若一起 401，则是账号级封禁。

## 18. 修掉「上游故障被报成参考图缺失」的诊断缺陷（2026-10-16 02:25–02:45，已上线）

**症状**：上游 401 期间点排障页的「图片生成链路测试」，面板显示

```
detail = “我会画给你看”任务失败（reference_unavailable）。
error_category = unknown, retryable = false, used_reference = false
```

而同一时刻的 AstrBot 日志明明写着：

```
[PrivateCompanion] 在线图片 API 参考图响应: status=502
  preview={"error": {"message": "jimeng upstream access denied (retrying suppressed): ..."}}
[PrivateCompanion] 在线图片 API 参考图生图失败,停止纯文回退: ... HTTP 502：上游生图服务临时失败或网关中断。
```

→ 结论被带偏成「参考图不可用」，实际是**上游被封**。同一缺陷在 00:08:30 也出现过一次（非本次改动引入）。

**根因（两处，都在归类阶段丢信息）**：

1. `astrbot_plugin_image_companion/main.py:212` 的 `ImageCompanionExtensionAPI._generation_failure_code(outcome)`：
   旧的判断顺序里 `if any(marker in text for marker in ("参考图", "reference")): return "reference_unavailable"`
   排在状态码判断之前，而**参考图通路的失败说明里必然含「参考图」字样**，于是任何走参考图接口的失败
   （HTTP 502/401/429/超时）都被吸进 `reference_unavailable`。
2. `astrbot_plugin_private_companion/diagnostic_envelope.py`：`classify_diagnostic_error()` 只认 "not available"
   不认 "unavailable"，所以 `...（reference_unavailable）` 落到 `unknown`；`retryable` 的 provider 分支
   也只认 `429/rate limit/临时/network/connection`。

**修复**（两处都改，`py_compile` + 用例通过）：

1. `main.py` 的 `_generation_failure_code` 改成「上游优先」顺序（新增 `import re`）：
   - 先判风控标记 `access denied / api forbidden / 封禁 / 风控 / forbidden` → `provider_access_denied`
     （本地服务在上游 401 时回的是 502，只有靠正文标记才能区分「被拒」和「临时抖动」）；
   - 再判 `re.search(r"http\s*(\d{3})", note)`：`401/403 → provider_access_denied`、
     `429 → provider_rate_limited`、`>=500 → provider_unavailable`；
   - 再判 `上游生图服务临时失败 / 网关中断 / upstream` → `provider_unavailable`；
   - **最后**才落到原有 `参考图/reference → reference_unavailable`（保留旧的合法路径）。
   - 用例：`tests/test_reference_import_contract.py::test_generation_failure_code_puts_upstream_failure_before_reference`
     用真实日志文本断言（502 样本 → `provider_unavailable`；含 `Access Denied code 50400` → `provider_access_denied`；
     `参考图接口失败`（无状态码）仍 → `reference_unavailable`）。
2. `diagnostic_envelope.py`：`provider_access_denied → authorization`（去查凭据/资质，别当临时抖动）、
   `reference_unavailable → configuration`（不再是 unknown）、
   provider 类 `retryable` 增加 `provider_unavailable / provider_rate_limited`（仍不含 `provider_access_denied`）。

**线上复核**（02:45，排障页实测）：`detail=…（provider_unavailable）。error_category=provider retryable=True
next_step=Check provider status and model configuration; use a fallback provider if needed. duration_ms=2885`
—— 与旧行为（unknown / false / reference_unavailable）对比，排障方向已正确。

**顺带记录：如何在无人值守时调用 AstrBot 面板接口**（排障页测试 / 插件热重载都要鉴权，浏览器不在场时）：

```python
# astrbot/data/cmd_config.json → dashboard.jwt_secret 用来自己签一个 HS256 token
import jwt, datetime
payload = {"username": "astrbot",
           "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)}
print(jwt.encode(payload, "<dashboard.jwt_secret>", algorithm="HS256"))
# 之后带 Authorization: Bearer <token>
#   POST /api/plugin/reload                      {"name": "astrbot_plugin_image_companion"}
#   POST /api/plug/astrbot_plugin_private_companion/page/troubleshooting/test
#        {"type": "image_generation_selfie", "prompt": "", "use_reference": true, "timeout_seconds": 120}
```

（`cmd_config.json` 里的 `dashboard.password` 明文是过期副本，直接拿它登录会得到「用户名或密码错误」，
用 pbkdf2 校验；所以走 jwt_secret 自签最省事。另外 AstrBot 会监听插件目录文件变化自动重载，
改完插件文件后日志里会出现 `Detected file changes for plugin ...; reloading.`，不必手动重载。）


## 19. 额度/权限被拒必须回 403：让排障结论从「上游抖动」变成「查额度」（2026-10-16 02:50–02:55，已上线）

**问题**：本机生图服务（`scripts/jimeng_image_server.py`）在即梦上游 401/50400「Access Denied」
时回 **HTTP 502**。插件（`astrbot_plugin_image_companion/main.py:_generation_failure_code`）会把 5xx
统一渲染成「上游生图服务临时失败或网关中断」，于是排障页给出：

```
error_code=internal_error  error_category=provider  retryable=True
next_step=Check provider status and model configuration; use a fallback provider if needed.
```

即「临时故障，可以重试」——而真实原因是账号额度/资质被拒，重试只会继续烧调用。

**改法（两处）**：

1. `scripts/jimeng_image_server.py`
   - 新增 `is_access_denied_detail(text)`：命中 `http 401 / http 403 / 50400 / access denied / api forbidden`；
   - `volc_call()` 复用它判定「拒绝访问」并立刻抛出（不重试）+ `note_access_denied()`；
   - 冷却闸门与异常分支改为回 **HTTP 403** + `{"type": "upstream_access_denied", "retry_after": N}`（原 502）。
     `volc_call` 里的注释写明理由：插件把 5xx 一律渲染成「上游临时失败」，只有**状态码**能穿过插件进入排障结论。
2. `astrbot/data/plugins/astrbot_plugin_private_companion/page_api.py`（约 `:7219`）
   - 排障规则表新增首条 `provider_access_denied`（中文类目「上游拒绝访问」、`retryable=False`、
     建议「先查账号额度与资质，额度恢复前不要反复重试」），否则 `error_code` 落到兜底的 `internal_error`。

**复核（线上排障页实测，02:53）**：

```
error_code=provider_access_denied  error_category=authorization  retryable=False
next_step=Check the account authorization or credential configuration, then test again.
detail=“我会画给你看”任务失败（provider_access_denied）。
```

（`diagnostic_envelope.classify_diagnostic_error` 里 `403 -> authorization` 的既有规则同时生效，
两条链路结论一致。）

**配套**：`scripts/ref_upgrade_experiment.py` 的「等上游恢复」增加**真实探针**——只有 2 次 `/v1/status`
显示冷却已清 **且** 一次最小 t2i 真出图成功，才开始四臂实验；状态接口本身失败不再被当成「已恢复」。
理由是冷却窗口只反映本地记录，额度恢复与否必须真打一次才知道（失败会重新触发冷却，这是预期）。

## 20. class 式用例第一次真正跑起来，抓出一条被预算重构弄坏的契约（2026-10-16 03:0x，已修）

**背景**：本仓库 venv 没有 pytest，我做了 `scripts/run_plugin_tests.py` 兼容层。它原来只遍历
**模块级** `test_*` 函数，`unittest.TestCase` 形式的类用例被**静默跳过**（模块报 0 个用例，
看上去「全绿」其实一个都没执行）。补上类用例支持后又补了两件事，否则会出现大量与产品代码无关的假失败：

- 转发 pytest 风格的 `setup_method` / `teardown_method`（裸 unittest 只认 `setUp`，不转发就会
  `AttributeError: ... object has no attribute ...`）；
- 手动调用 `setUpClass` / `tearDownClass`（unittest 只在「套件级」运行时调它，逐个 `test.run()` 会跳过，
  本例会让 `self.runtime` 不存在）。

效果：同一份代码，全仓库数字从 **5562 passed / 461 failed** 变成 **5798 passed / 225 failed**
（多出来的通过全是这两类假失败转正）。

**被抓住的真问题**：`test_migrated_photo_prompt_reliability.py` 的
`test_natural_language_exclusion_stays_out_of_positive_reference_section` 断言
「服装排除词不得出现在正向段」，它用 `final_prompt.split("\n\nAvoid ", 1)[0]` 切分正/负向。
第 6 节的预算重构把 natural_language 拼装改成了**单段平铺**（`f"{head} {tail}"`），
`\n\nAvoid ` 这个切分标记消失 → 排除词句（`Respect the current request's explicit wardrobe exclusions: sleepwear and pajamas.`）
被算进「正向段」，用例失败。

**归因方法（可复用）**：没有 git 的情况下，把**改造前的 `_assemble` 实现**写成补丁函数、
monkeypatch 回模块再跑同一条用例，结果 **旧实现 0 失败 / 新实现 1 失败** → 确认是自己引入的回归，
不是既有分歧。脚本落在 `logs/.pytest-tmp/check_nl_exclusion.py`、`logs/.pytest-tmp/dump_nl_prompt.py`。

**修法**（`photo_prompt_context.py` 的 natural_language 分支）：把尾部再切一次，让否定句**另起一段**：

```python
marker = "Avoid "
split_at = tail.rfind(marker)
critical, avoid_clause = tail[:split_at].rstrip(), tail[split_at:]
prompt = f"{head}\n\n{critical}\n\n{avoid_clause}".strip()
```

这样既恢复了下游依赖的 `\n\nAvoid ` 契约，也让「Avoid …」不再是正向段里的一句话
（单段平铺时模型更容易把否定句当正向描述处理）。拼装长度仍受 800 字符约束：实测该用例
`len = 794`、`\n\nAvoid ` 存在；`--only natural_language_exclusion` 由失败转通过（模块 19 passed / 3 failed）。

**剩余 3 条失败不在今晚改动范围**：都属于 **traditional（分区）格式**路径
（`test_partitioned_prompt_budget_preserves_tail_wardrobe_and_negative_constraints`、
`test_structured_scene_budget_and_recent_continuity_keep_core_facts`、
`test_negative_outfit_request_is_separated_and_cannot_choose_forbidden_preset`）。
今晚只动了 natural_language 分支，traditional 分支代码逐字未改（它断言 `len(final_prompt) > 1800`，
恰好证明走的是不受 800 字符约束的分区路径），所以判定为**既有**契约分歧，留到早上决定改代码还是改测试。

**配套调整**：`_PROMPT_CHAR_BUDGET` 790 → **786**。三段式每处 `\n\n` 比原来的空格多 1 字符，
而生产日志里 `prompt_chars` 比 `_assemble()` 返回值还多 4 个字符（02:19 最后一次成功时是 798），
留出余量后仍稳定 < 800（上游硬上限「不能超过 800」是允许等于 800 的，但不想贴着走）。
上游恢复后的无人值守驱动 `scripts/ref_upgrade_experiment.py --phases ref,size,prod` 会在实验之后
自动跑 **生产链路烟测**（面板排障接口 ×2），并从 `logs/astrbot-*.log` 抓最后一个 `prompt_chars=` /
`prompt_format=` / `kind=` 写进 `logs/jimeng-out/eval/ref-upgrade-driver.jsonl`，用来确认预算没有回退。

## 21. 尾部要求去重：同一句身份约束不再写两遍（2026-10-16 03:2x，已上线）

**怎么发现的**：用离线脚本 `logs/.pytest-tmp/check_t2i_prompt.py` 把「画一只在窗台上睡觉的橘猫」
（`kind=text2img`，无参考图）跑过 natural_language 拼装，最终提示词里同时出现：

- 头部（来自 `image_runtime._apply_photo_generation_prompt_format` 自己的拼装）：
  `Create a single coherent image showing Petite slender teen girl, small head, about 7 heads tall,
  long legs, 画一只在窗台上睡觉的橘猫, 阳光洒进来 Keep the reference face, hair colour and
  whale-fin ears, her whale tail grows from her lower back above …`
- 尾部（`photo_prompt_context._critical_requirements_tail` 无条件追加）：
  `… Long blue hair with light-blue gradient tips, blue eyes. Pose override: framing - close
  upper-body shot with a blurred background; pose - standing in profile by the window holding the
  curtain. …`

即：**比例句与身份句各写了两遍**（两处措辞还不一样），而且一只猫的画面被追加了
「站在窗边撩窗帘」的人设姿势。根因是两层拼装各自带预算、互不知道对方写了什么：
`image_runtime` 的短请求里身份句留在头部，`photo_prompt_context` 的尾部再加一遍。

**改法**（`photo_prompt_context.py`）：

```python
def _critical_requirements_tail(negative: str, head_lower: str = "") -> str:
    def carried(*markers: str) -> bool:
        return bool(head_lower) and any(marker in head_lower for marker in markers)
    parts: list[str] = []
    if not carried("about 7 heads tall", "7 heads tall"): parts.append("Small head, about 7 heads tall, long legs.")
    if not carried("light-blue gradient tips", "long blue hair"): parts.append("Long blue hair with light-blue gradient tips, blue eyes.")
    # …identity / pose / hands / watermark 同样各自一个 carried() 判断
```

`_assemble()` 的 natural_language 分支改成**先裁头部、再按裁完的头部去重**（顺序很关键：
若按未裁剪的文本判断，可能出现「头部尾部被裁掉、尾部又因去重没写」的两头落空）：

```python
joined = " ".join(text for _label, text in groups if text)
tail = _critical_requirements_tail(negative)                       # 最坏情况，先占预算
head_budget = max(200, _PROMPT_CHAR_BUDGET - len(tail))
head = _clip(joined, head_budget, preserve_tail=False)
deduped = _critical_requirements_tail(negative, head.lower())      # 只对「裁完还活着」的要求去重
if len(deduped) < len(tail):
    tail = deduped
    head_budget = max(200, _PROMPT_CHAR_BUDGET - len(tail))        # 省下的预算还给场景描述
    head = _clip(joined, head_budget, preserve_tail=False)
    tail = _critical_requirements_tail(negative, head.lower())
```

**验证**：

- 纯文生图橘猫：`prompt_chars=776`，头部的 `about 7 heads tall` 之后**不再重复**比例句
  （只剩 `Long blue hair … Pose override …`，因为该请求的头部被裁到身份句中途，姿势要求仍需尾部补上）；
  `\n\nAvoid ` 契约仍在（三段式）。
- 生产排障自拍（`logs/.pytest-tmp/verify_live_prompt.py`，一次面板调用 + 抓 `生图开始` 日志）：
  `prompt_chars` **796 → 792**，`prompt_format=natural_language`，`kind=explicit`，
  面板仍返回 `provider_access_denied / authorization / retryable=false`（上游封禁未解）。
  省下的字符被头部自动吃掉（`head_budget` 随尾部缩小而变大），所以总长贴着预算、场景描述更完整。
- 回归基线**未变**：`astrbot_plugin_image_companion` 已知 5 条失败与 03:20 全量跑一致
  （`test_anima_master_adapter::test_upstream_prepared_payload_contract` 需环境变量 `ANIMA_MASTER_SOURCE`；
  `test_generation_contracts.VersionedContractTests::test_managed_reference_ticket_is_one_shot_and_scope_bound`
  是 Windows `TEMP` 8.3 短名；`test_migrated_photo_prompt_reliability` 的 traditional 分区 3 条）。
  实跑 3 个相关模块 = **27 passed / 4 failed**，失败集合与基线完全相同。

**留给早上的决定（未改）**：`kind=text2img` 的头部仍被 `image_runtime` 塞进人设前缀
（`Create a single coherent image showing Petite slender teen girl … Keep the reference face …`），
所以「画一只猫」的画面里仍然带着这个女孩。这可能是产品有意为之（人设图统一风格），也可能是漏了
按 workflow 分流的判断；属于产品语义决定，不在无人值守时段擅自改。

## 22. 冷却闸门会自我续期：额度恢复后本地仍会拦（2026-10-16 03:3x，已修）

**根因（读代码+离线自测确证）**：`scripts/jimeng_image_server.py` 里 `clear_access_denied()`
**定义了但从未被调用**，而冷却窗口的 `until` 每次 `note_access_denied()` 都会被续期。于是形成死锁：
额度恢复之后，任何一次请求只要冷却还 active 就被闸门拦掉（不打上游），而没有一次真打上游就
永远不会走到「成功 → 解除冷却」——闸门把自己锁在里面。看门狗的 `live_probe()` 同样会被拦
（或真打一次 401 又把窗口续上 300s）。

**改法 1：闸门内每分钟放一个真请求**（`ACCESS_DENIED_PROBE_SECONDS = 90.0`）

```python
if cooling["active"]:
    if not allow_gate_probe():          # 距上次真打上游 < 90s
        log_event({"stage": "cooldown_block", …})
        self._send(403, {"error": {…, "type": "upstream_access_denied", "retry_after": …}})
        return
    log_event({"stage": "cooldown_probe", "model": model, "seconds_left": …})   # 放行，真打一次
```

**改法 2：成功即解除**——`volc_call()` 末尾（拿到 2xx）调 `clear_access_denied("volc_call ok")`，
`clear_access_denied()` 同时把 `last_probe` 复位为 0。

**验证**：`scripts/check_access_gate.py` 离线自测 **13/13 通过**（未封禁放行、冷却内首次放行、
紧接第二次拦截、成功后立刻解除且 count 归零、`is_access_denied_detail` 5 例分类不回归）。
线上实测（重启后 launcher pid 43472 / listener pid 47196，日志 `logs/jimeng-20261016-033225.log`，
`logs/pids.json` 已更新）——`logs/jimeng-server-requests.jsonl` 的 stage 序列：

| 时刻 | stage | 说明 |
| --- | --- | --- |
| 03:32:39 | `request` → 上游 401 | 重启后第一次真打，写入冷却 |
| 03:32:41 | `cooldown_probe` (secs=298.1) | 冷却内首次仍放行探测 |
| 03:32:44 | `cooldown_block` (secs=296.9) | 距上次探测仅 3s → 快速失败，不打上游 |
| 03:34:19 | `cooldown_probe` (secs=201.9) | 距上次探测 98s ≥ 90s → 再放一个真请求 |

`GET /v1/status` 同时显示 `count=3 first_ts=03:32:39 last_ts=03:34:19`、`detail` 为
`火山接口 HTTP 401（action=CVSync2AsyncSubmitTask，第1/3次）…{"code":50400 … "Access Denied"}`，
即三次探测真的打到了上游（封禁仍未解），但窗口内的其余请求都是 0 成本快速失败。

## 23. 缺陷频率统计器：手部缺陷率在结构调整后减半（2026-10-16 03:2x）

**新工具** `scripts/eval_vl_defects.py`：免费 VL 模型逐张审图，tag 词表固定为
`hands / tail_attachment / head_ratio / watermark / extra_limbs / clothing / face / background`，
输出每张的 JSON（`{"defects":[{"tag","note"}],"clean":bool}`）与「缺陷出现次数（按频率）」表；
支持 `--tag`、`--limit`、`--newer-than`、`--older-than`、`--dry-run`、`--model`、`--source`，
明细写 `logs/jimeng-out/eval/vl-defects.jsonl` 与 `defects-<tag>.json`。它和六轴打分器互补：
打分器给「好不好」，这个给「坏在哪、坏几次」。

**排障自拍 12 张 vs 12 张**（以 `20261016_015800` 为界，前后各取 12 张）：

| 缺陷 | 结构调整前（01:40–01:52） | 结构调整后（01:58–02:05） |
| --- | --- | --- |
| hands | 5/12（41.7%） | 2/12（16.7%） |
| watermark | 0/12 | 1/12（8.3%） |
| tail_attachment | 0/12 | 1/12（8.3%） |
| 完全无缺陷 | 7/12 | 9/12 |

**怎么读这张表**：分界点落在「双手空闲 + 无标识三脚架 + 强化水印措辞」这一批改动（第 14 节），
**不是**第 21 节的尾部去重（那是 03:2x，之后还没有新的生产出图，因为上游封禁）。
也就是说：把「举着手机自拍」换成「双手空闲、看得见指节」之后，手部缺陷率大约减半
（与第 14 节六轴盲评里 head_proportion/anatomy 各 +1.3 分同向）。
同口径看 24 张最新生产图的整体分布：hands 20.8%、watermark 12.5%、其余为 0，完全无缺陷 16/24。

**目前仍排第一的缺陷依旧是手**（第 5、13、14 节的三次 A/B 已证明：写词、负面词、二次修手
三条路都不显著），下一步只剩「换更强的出图模型 / 上游恢复后重跑原生 1280 实验」这两条外部变量。

## 24. 离线拼装审计：抓住两个「要求会在预算里消失」的洞（2026-10-16 03:4x，已修）

**动机**：整夜的改动几乎都在拼装层（预算、尾部关键要求、两层去重），但它平时只能等上游出图才能验；
上游被封时完全没法验。新脚本 `scripts/audit_prompt_assembly.py` 复用插件自带的
`_PhotoReliabilityHarness`，离线跑 10 个用例（t2i 短/长、自拍短/长请求/场景预设/礼服预设、
explicit 自拍、edit 改图、两个 traditional 格式）＋ 20 次自拍轮换，断言：
`natural_language` 最终提示词 ≤ 800、含 `\n\nAvoid ` 三段式、比例句不重复、含水印要求、
带参考图的请求含身份锚与完整姿势、轮换组合数 ≥ 6。支持 `--seed/--rounds`（多种子找随机抽样
引起的契约失败），明细写 `logs/jimeng-out/eval/prompt-audit*.json`。

**洞 1：水印要求在两层裁剪之间掉进缝里。** 审计第一版在 `selfie-短` 上抓到「整条提示词不含
任何 watermark 字样」。机制：`image_runtime` 先在头部写了 `No watermark, logo, label, handle or
caption on the image or on any object`，`_assemble` 的 `head_budget` 裁剪把它切掉；而尾部
`_critical_requirements_tail()` 的 `carried("no watermark")` 是拿**未最终定稿**的头部判断的，
于是同一句在尾部也被跳过；`Avoid` 段里那串水印词又因为 `preserve_tail=True` 从左侧被裁掉。
三处都不留 → 提示词里一个字不剩。修法两处（`photo_prompt_context.py`）：

1. 水印句**永不去重**：`parts.append("No watermark, logo, handle or caption.")` 无条件追加
   （40 字符买保险；同期生产图伪水印率 12.5%，见第 23 节）；
2. `_TAIL_NEGATIVES` 重排成「被裁掉的优先级」：`chibi/比例类 → 手部词 → 水印词`，
   水印词永远排在最后，`preserve_tail=True` 从左侧丢词时最后才轮到它们。

**洞 2：被裁成半截的 `Pose override:` 会被当成「头部已经有了」。** 20 次自拍轮换里有 13 次最终
提示词只剩 `Pose override:`（甚至只有 `Pose`）——`head_budget` 正好切在姿势句里，而去重只看
`"pose override" in head`，于是尾部那条完整的机位/姿势被跳过，结果模型看不到任何机位或姿势。
修法：只有头部**同时**含 `framing -` 与 `pose -` 才认为它带上了姿势，否则尾部补一条完整的。
修后 20/20 都含完整 `Pose override: framing - …; pose - …`，不同组合 16 种（≥6）。

**顺带修**：审计脚本自己读轮换提示词时写成了 `payload.get("final_prompt") or prompt or ""`，
`prompt` 是上一轮的残留变量，导致姿势统计被污染（同样的 `or prompt` 反模式值得在别处也查一遍）。

**验证**：`--rounds 6`（6 个种子 × 10 用例 × 20 次轮换）全部契约通过，最终提示词长度
`678–794`；新增三条回归用例（水印句必存、半截姿势必重写、去重后仍 ≤800）；
`scripts/run_plugin_tests.py` 全量回归数字见 `docs/IMAGE-OPTIMIZATION-REPORT-20261016.md` 第 5 节。

## 25. 纯文生图（t2i）仍带着人设身份句与随机姿势覆盖（2026-10-16 04:0x，未改，待用户定夺）

**现象**（`logs/.pytest-tmp/check_t2i_prompt.py`，04:53 实跑，`kind=text2img`、`reference=False`、
`prompt_chars=781`）：

```
Create a single coherent image showing Petite slender teen girl, small head, about 7 heads tall,
long legs, 画一只在窗台上睡觉的橘猫, 阳光洒进来 Keep the reference face, hair colour and whale-fin
ears, her whale tail grows from her lower back above the hips and her legs stay human,
Pose override: framing - close upper-body shot with a blurred background; pose - crouching …
Long blue hair with light-blue gradient tips, blue eyes. … Avoid … watermark … .
```

三个可疑点：

1. **主体被覆盖**：用户要「画一只猫」，头一句却是「Petite slender teen girl …」，猫只是中间一小段；
   最终大概率画出「人物 + 猫」而不是一张猫图。
2. **悬空引用**：`Keep the reference face, hair colour and whale-fin ears` 出现在**没有参考图**
   （`reference=False`）的请求里 —— 让人「保持」一个根本没传的参考人脸。
3. **无关姿势覆盖**：`Pose override: framing - close upper-body shot …` 对静物/动物请求没有意义，
   还会挤掉用户描述。

**为什么没改**：`image_runtime.py:11000 _apply_photo_generation_prompt_format(self, prompt_text, *, prompt_format="")`
拿不到「是否有参考图 / 是否 t2i」的信息，`image_runtime.py:11091-11106` 里这段人设尾句是无条件拼的。
要按 workflow 分流就得从调用方（`image_runtime.py:12020` 一带）透传一个标志位，会同时动到自拍主路径，
风险与收益不匹配；而且「画一只猫」到底是「画猫」还是「画这个角色和猫」属于产品意图，需要用户确认。

**建议的两条路线**（都不需要推翻现有拼装）：① 保持现状（人设永远在场，只是把悬空引用改成
「a girl with long blue hair …」的描述句，去掉「reference」一词）；② 给 t2i 与静物/动物请求增加
「无人设」模式：跳过人设头句/身份尾句/姿势覆盖，只用比例句 + 用户描述 + 水印/手部负面词。

## 26. 上游封禁是「按模型」的：4.6 被拒，**4.0 可用** → 生图已恢复（2026-10-16 04:0x，已上线）

**怎么发现的**（`astrbot/.venv` 跑的两个鉴权层探针）：

- `logs/.pytest-tmp/probe_volc_auth.py`（只发签名头、不进请求体）证明签名本身有效：
  未签名 → `401 InvalidAccessKey`（headers 里带 `X-Date`）；AK/SK 签名正确 → 走到业务层并回
  `401 code=50400 Access Denied`（带 `request_id`）。所以「AK 被删 / 签名写错」都被排除，
  拒绝发生在**服务端鉴权之后、按 action/req_key 授权**的那一层。
- `logs/.pytest-tmp/probe_volc_reqkeys.py`（真实 `SubmitTask`，逐 key 探测，间隔 3s）：

  | req_key | 结果 |
  | --- | --- |
  | `jimeng_seedream46_cvtob`（4.6，**生产默认**） | `401 / code 50400 Access Denied` |
  | `jimeng_t2i_v40`（4.0 文生图） | **`200` → `task_id`（可用！）** |

  结论：**拒绝是按模型/req_key 维度的，账号整体没被封**；`jimeng_t2i_v40` 可以立刻顶上来当回退。

**上线改动**（`scripts/jimeng_image_server.py`）：

- 新增 `FALLBACK_REQ_KEYS = {REQ_KEY_JIMENG_46: (REQ_KEY_JIMENG_40,), REQ_KEY_JIMENG_40: (REQ_KEY_JIMENG_46,)}`，
  `req_key_denied: dict[str, float]`（`REQ_KEY_DENIED_TTL_SECONDS = 600`）记录「哪个 req_key 刚被拒」。
- `volc_call()` 成功时 `clear_req_key_denied(req_key, ...)`；拿到 401/50400 时
  `mark_req_key_denied(req_key, detail)`，再把冷动闸门也标上。
- `submit_task()` / `generate()` 里按 `_req_key_candidates(primary, *, allow_fallback)` 排序候选：
  **首先跳过 TTL 内被标记拒绝的 key**，然后依次尝试；回退成功打 `fallback_ok`，回退后又一次被拒才 `fallback_failed`。
- `allow_fallback=False` 的调用（同一次请求里避免双重回退 / 探针）不走回退。
- `/v1/status` 新增 `req_key_denied` 字段（`{req_key: seconds_left}`），便于看门狗/驱动判断。
- 请求日志新增 stage：`req_key_denied` → `fallback` → `fallback_ok` → `done`（`logs/jimeng-server-requests.jsonl`）。

**离线自测**：`scripts/check_req_key_fallback.py`（13 项：标记/清除/TTL 过期/snapshot 为空/候选顺序/双拒绝）
全部通过（`=== 全部通过 ===`）。

**端到端验证**（04:06，重启后 pid 50820，重启流程同第 22 节）：

| 验证 | 请求 model | 实际 req_key | 结果 |
| --- | --- | --- | --- |
| 纯文生图 `/v1/images/generations` | `jimeng-4.6` | `jimeng_t2i_v40` | `HTTP 200` |
| 参考图 `/v1/images/edits`（人设参考图 563x307） | `jimeng-4.6` | `jimeng_t2i_v40` | `HTTP 200` |
| 生产链路（排障页 `image_generation_selfie`，带参考图） | 插件默认 4.6 | `jimeng_t2i_v40` | `success=true`，`phase=completed`，21.5s |

请求日志那一段长这样（`logs/jimeng-server-requests.jsonl`）：

```
04:06:37 request           jimeng_seedream46_cvtob
04:06:38 req_key_denied    jimeng_seedream46_cvtob
04:06:38 fallback          jimeng_t2i_v40
04:06:51 fallback_ok       jimeng_t2i_v40
04:06:52 done              jimeng_t2i_v40
```

`/v1/status` 的 `last_upstream_success_ts` 也随之从空变成 `2026-10-16T04:06:51+08:00`：
看门狗 `scripts/jimeng_ban_watch.py` 与恢复驱动 `scripts/ref_upgrade_experiment.py` 都在 04:07 观测到
`recovered`（`via=status.last_upstream_success_ts` / `periodic_probe ok`），驱动器已自动开始
参考图四臂实验（第一臂 `crop563` 4 张里成 3 张，1 张 `429/50430` 限流）。

**注意（重要）**：4.0 与 4.6 能力有别，回退是「先恢复可用」而不是「等价」：
4.0 的 `jimeng_t2i_v40` 走的是文生图/图生图 + `binary_data_base64`，没有 4.6 的 `cvtob` 三图参考链；
质量/一致性差异待 04:0x 之后的实验给出数字。另外上游仍会 `429/50430` 限流，不要放大并发。

### 26.1 顺着这次故障捡到一个天然 A/B：同一提示词/参考图，4.6（封禁前）vs 4.0（回退）

`scripts/eval_gen.py` 的记录里 `model` 字段记的是**请求的模型名**（`jimeng-4.6`），不是实际生效的
`req_key`，所以 04:0x 那批 `ref` 臂在 `logs/image-eval.jsonl` 里也写着 `jimeng-4.6`。实际生效值要看
`logs/jimeng-server-requests.jsonl`：04:07–04:12 全是 `request jimeng_seedream46_cvtob → fallback → fallback_ok jimeng_t2i_v40`。
（要区分「请求的模型」和「实际模型」时，以服务端请求日志为准。）

> **已修（04:43）**：`scripts/eval_gen.py` 现在把服务端返回的 `req_key` 一并落盘，并映射出
> `model_effective`（`jimeng_seedream46_cvtob`→`jimeng-4.6`、`jimeng_t2i_v40`→`jimeng-4.0`）。
> 实测一行：`"model":"jimeng-4.6" … "req_key":"jimeng_t2i_v40","model_effective":"jimeng-4.0"`。
> 旧的 `model` 字段保留（向后兼容），但**读数据要认 `model_effective`**。

于是参考图四臂天然分成两组可比数据：

| 组 | 时间 | 实际 req_key | 各臂有效张数 |
| --- | --- | --- | --- |
| 封禁前 | 02:15–02:19 | `jimeng_seedream46_cvtob`（4.6） | crop563 4 / fullbody 4 / threequarter 4 / upperbody 1（02:20 封禁打断） |
| 回退后 | 04:07–04:1x | `jimeng_t2i_v40`（4.0） | crop563 3（1 张 429）/ fullbody 4 / threequarter 3+ |

这正好可以回答两个问题：① 参考图换成全身/四分之三是否真的提升一致性；② 回退到 4.0 后一致性掉了多少。
为此新增 `scripts/eval_vl_identity.py`（把「参考图 + 生成图」一起喂给免费 VL，让它只判**身份一致性**
——脸型/发色/发型/瞳色/服装/鲸鳍耳，忽略姿势、机位、背景与画质——输出 `identity` 1–10、
`same_character`、`used_reference`，并按臂聚合；日志 `logs/image-eval-identity.jsonl`）。
理由：六轴打分器只会说「这张好不好看」，判断不了「还是不是同一个角色」，而后者才是参考图有没有
真正生效、以及该换哪张参考图的判据。

### 26.2 参考图取景/分辨率：评测提示词上差 5 分，**生产提示词上零差异** → 已回滚（2026-10-16 04:2x）

**问题**：回退到 4.0 后该不该换一张人设参考图？生产用的是 `persona_yubao_reference.png`（563×307，
只有脸和一点背景的裁切），同一个人设原图是 941×1672 的全身设计图。

**评测提示词下的四臂对照**（同一提示词、只换参考图；4 次独立盲评 pass，seed 打乱顺序）：

| 参考图 | 分辨率 | ref40 | ref40b | ref40c | refup | **均值** | 4.0 下的 style 均值 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `persona_yubao_reference.png`（现行） | 563×307 | 38.67 | 37.00 | 40.33 | 40.00 | **39.00** | 5.5 |
| `ref_upperbody.png` | 1060×1360 | — | 43.50 | 40.50 | 41.67 | **41.89** | 6.5 |
| `webui_f2a201d…jpg`（全身设计图） | 941×1672 | 43.00 | 47.00 | 43.25 | 42.50 | **43.94** | 7.3 |
| `ref_threequarter.png`（同一张图的近全身裁切） | 928×1664 | 47.33 | 45.00 | 44.33 | 39.67 | **44.08** | 7.5 |

**现行参考图在 4 次 pass 里每一次都是最低**（37.0–40.3），另外三张在 39.7–47.3。臂内 pass 间波动
3.0–7.7 分，所以单次 pass 的臂间差不可信、但「每一次都最低」是可信的。再看封禁前的 4.6：

| 参考图 | 4.6（ref46，n=4） |
| --- | --- |
| crop563 | 45.25 |
| fullbody | 46.50 |
| threequarter | 44.50 |

4.6 下三臂只差 2.0 分 → **4.6 基本不吃参考图取景，4.0 很吃**（4.0 没有 `cvtob` 三图参考链，更依赖
单张参考图给的信息量；现行那张只有 563×307，脸以外的信息几乎为零）。

**于是把生产参考图换成全身设计图**（同一张画，非换人设；同时改顶层键 ×2 + catalog `persona.source` ×2，
`POST /api/plugin/reload`），跑 4 张生产排障自拍对比旧参考图的 3 张：

| 组 | n | 六轴总分 | head | hands | face | style | anatomy | finish |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 旧参考图（crop563） | 3 | **45.67** | 5.67 | 7.00 | 8.67 | **9.00** | 7.00 | 8.33 |
| 新参考图（全身图） | 4 | **45.50** | 6.50 | 6.75 | 8.75 | 8.00 | 7.50 | 8.00 |

**生产提示词下两者打平（45.5 vs 45.67），评测提示词上的 +5 分完全不迁移**。缺陷统计两边都干净
（新 4/4 完全无缺陷、旧 2/2；旧组第 3 张因免费网关 502 没评上）。人眼复核里，新参考图 4 张里有 **2 张**
是「平面二次元角色贴在写实房间照片上」的拼贴感（style 轴也确实是 9.0 → 8.0），旧参考图那批则是整幅
插画。**推测机制：泄漏的不是分辨率而是参考图的背景** —— 全身设计图是纯白底，模型把它当成「切出来
的角色」再贴到提示词给的写实场景里；旧的脸部裁切自带室内窗景，模型倾向于整幅插画化。

**结论：已回滚**（4 处路径改回 `persona_yubao_reference.png`，再 reload；配置备份
`astrbot_plugin_private_companion_config.json.bak-20261016-0425`）。回滚后 1 张烟测通过。
**教训**：`scripts/eval_gen.py` 的评测提示词与生产提示词（排障自拍那条明确要求「保持参考图中的人物
身份**和外观一致**」）对参考图的敏感度不同，评测结论必须回到生产提示词上复验才能改配置。

### 26.3 4.0 下的原生分辨率：1536 看着最好，但没到能改默认值的程度

同一提示词、纯文生图（无参考图）、3 个原生尺寸各 3–4 张：1024 **40.25**（n=4）、1280 **41.67**（n=3）、
1536 **45.00**（n=3）。逐张 34–48 分，臂内跨度（1024 是 42/41/44/34）几乎吃掉臂间差距 → 噪声级，
**不改默认分辨率**（1536 还更贵更慢）。与 4.6 时代的超采样结论（native1k 47.33 vs super2k 44.67）方向一致。

**顺带确认 4.0 的整体代价**：同提示词的评测臂在 4.6 时代普遍 45–50 分（如 `budget-ab after` 50.0、
`handsfree6` 49.5），4.0 回退后同样条件落在 39–45 分区间；生产排障自拍 45.5/45.67 对封禁前 02:0x 的
48–50，即**约 −3~5 分（−6%~−10%）**，这是「能用」的代价，不是「白拿」的。

---

## 27. 下传截断把「姿势覆盖 + 身份锚」整段丢掉：超过 800 字符的提示词一直在裸奔（2026-10-16 04:4x，已修）

**怎么发现的**：查 4.0 回退后的请求日志时顺手统计 `stage=prompt_clamped`（截断记录），
发现 277 次请求里 **157 次需要截断**（提示词 735–1128 字符）；再看截断记录的 `head`/`tail`：
`head` 里**没有** `Pose override`，`tail` 里只有 `Avoid ...`。于是拿一条 876 字符的真实形态提示词
离线回放 `clamp_prompt`，确认：**原文含 `Pose override:` 与身份锚句，截断到 751 字符后两者全部消失**
（本地回放与服务器 `sent` 字段逐字一致，排除了日志假象）。

**机制**：旧 `clamp_prompt` 只把**最后的否定句**摘出来单独保留，其余按「前 800 字符」硬切。
而生产提示词的排布是「场景描写 → 风格句 → 身份锚句 → `Pose override: framing - …; pose - …`
→ `Avoid …`」——一旦场景描写把头部预算吃满，**夹在中间的身份锚与姿势覆盖就整段落在切点之外**。
后果：长提示词请求（用户写长场景、或历史遗留的长拼装）等于没有姿势控制、也没有身份锚定。

**修法**（`scripts/jimeng_image_server.py`）：新增 `_sentence_spans()` / `_requirement_suffix()`，
clamp 从「只保否定句」改成**整段保住结尾的要求句**（身份锚 + `Pose override:` + `Avoid`），
被压缩的改成中间的场景描写；并新增 `MIN_HEAD_CHARS = 160`，保证场景至少留 160 字符。
要求句总量超过 `800 - 160` 时（例如超长负面词）退回「只保否定句」的老策略，仍然不超限。
同时日志补了 `stage=prompt_clamped` 的 **`sent` 字段（完整下发文本）**——旧的 `head`/`tail`
之间有 ~200 字符没记，「姿势句到底有没有被截掉」只能靠猜，这次就是被这个坑绊了一下。

**验证**（`scripts/check_prompt_clamp.py`，新脚本）：

- 合成用例 7 条：短提示词原样返回；876 / 1200 / 4669 字符（生产形态）截断后**都仍含
  `Pose override`、身份锚、`Avoid`**；超长负面词走老兜底；无句读长文本不切半截；全部幂等、无损前缀；
- 日志回放 158 条 `prompt_clamped` + 29 条完整短提示词：0 失败；**修复前 3 条 823 字符超限记录**
  只统计不判失败（正是当初促成那个兜底的历史证据）；
- 端到端（真实 4.0 请求）：937 → 678 字符，`Pose override` / `framing` / `whale-fin ears` /
  `preserve them exactly` / `Avoid` / `watermark` **全部保留**（修复前同形态输入里 `Pose override` 为缺失）；
  同一条截断后提示词出图，机位与姿势与要求一致（蹲在窗台边够绿植、三分之四侧面）→ 行为层也验证通过。

> 影响面提醒：生产排障自拍在提示词预算重构后稳定在 757–797 字符，**基本不触发截断**；
> 真正会踩到的是「用户写了很长的场景描述」这类请求（日志里 selfie 类历史最长 4669 字符、text2img
> 4 条里 2 条 >800）。所以这个修复不是给默认路径提速，而是把长提示词从「丢要求」拉回「丢场景」。

**顺带量到的收益（clamp 修复 A/B）——结论：结构上正确，质量上量不到收益**。

我先用「04:43 的修复后图 vs 04:21 的修复前图」比，得到 40.25 → 45.5（style 6.0→9.0），看着像个大胜。
但 04:55 把**同样的 4 张修复后图**再评一次，得到 **48.25** —— 同一批文件两次 pass 差 3 分。
于是补做了同 pass 对照：用 `--legacy-prompt-out` 复刻修复前的截断文本（1128 → 715 字符，
**不含** `Pose override` / `日系二次元插画风` / `Keep natural adult proportions`，只含场景 + 半截身份句 + `Avoid`），
用它生成 4 张，与修复后的 4 张**在同一次盲评里**打分：

| 组 | n | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 修复前（legacy 文本） | 4 | 7.75 | 7.5 | 8.75 | 9.25 | 6.5 | 9.0 | **48.75** |
| 修复后 | 4 | 8.0 | 7.0 | 8.5 | 9.25 | 6.75 | 8.75 | **48.25** |

→ **没有可测收益（同 pass 内 0.5 分，噪声级）**；先前那个「+5.25」是**跨 pass 的打分漂移**，
不是提示词修复的功劳（style 6.0 的那次读数也属于漂移，本次两臂都是 9.25）。

那为什么还保留这个修复：它改的是**契约正确性**，证据是结构性的而不是打分——
`sent` 字段证明要求句不再被丢（修复前 876 字符的提示词截断后 `Pose override` 直接消失），
出图行为也对得上（同一条截断后提示词，机位与姿势与要求一致，而修复前那句话根本没发出去）。
**教训**：VL 盲评的绝对值在不同 pass 之间会漂 ±3 分，**任何 A/B 都必须在同一次盲评里跑**，
否则会把噪声当成收益（本节第一版就是这么错的，留在这里当反面教材）。

> **测量真相要记一笔**：评测脚手架自己的提示词是 1128 字符（`eval_gen.py` 默认 SCENE+IDENTITY+STYLE+POSE），
> 所以**今夜所有 >800 字符的评测臂都跑在截断后的提示词下**（缺风格句/比例句/姿势句）。
> 臂间比较仍然成立（两边同样降级），但那些绝对分不代表「完整提示词」的水平；
> ≤800 字符的对照（如 `budget-ab`、生产自拍 757–797）不受影响。

---

## 28. 两条方法学结论：VL 盲评跨 pass 漂 ±3 分；「鲸尾正面子句」无效（2026-10-16 04:5x）

### 28.1 同一批图片，两次盲评差 3 分 —— 绝对值只能同 pass 比

同一组 4 个文件（`clampfix-*` + `reqkeycheck-*`）：

| 盲评 pass | 时间 | 总分 | style |
| --- | --- | --- | --- |
| 第一次（`vl-clampfix.txt`） | 04:43 | 45.5 | 9.0 |
| 第二次（`vl-tailab.txt`，与另一臂混在一起盲评） | 04:52 | 48.75 | 9.0 |
| 第三次（`vl-clampsame.txt`，与 legacy 臂混评） | 04:55 | 48.25 | 9.25 |

**3 分的漂移**（≈6% 总分），来源是免费 VL 模型自身：`nvidia/nemotron-3.5-super-vl:free` 每次调用都可能
换后端实例/温度，而且**同一批图的评分顺序与邻居也会影响**（这三 pass 的盲评顺序都不同）。
后果很实际：**跨 pass 比较绝对分，会把噪声当成收益**（§27 第一版就是这么错的）。
规则：任何 A/B 都必须在**同一次盲评调用**里把两臂一起喂进去；单臂数字只反映「这批图自己好不好」。

### 28.2 给鲸尾加正面子句：无效，且「美人鱼」抱怨是设计带来的必然误报

动机：VL 几乎每张都报 `mermaid tail coexists with human legs` / `tail attachment unclear`（缺陷统计里
`tail_attachment` 是常驻项）。试了正面子句（`--pos-extra`）：
`Her whale tail is a soft tail accessory attached at the lower back, and she has two normal human legs and feet.`
（提示词 1128 → 1241 字符），同 pass 盲评 4 vs 4：

| 组 | n | head | hands | face | style | anatomy | finish | 总分 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 不加子句 | 4 | 7.75 | 7.25 | 8.75 | 9.0 | 7.25 | 8.75 | **48.75** |
| 加鲸尾子句 | 4 | 8.0 | 7.0 | 9.0 | 9.0 | 7.0 | 9.0 | **49.0** |

0.25 分 = 噪声，且**两臂的缺陷文本里「美人鱼尾巴 + 人类腿」的抱怨一条不少**（「Pants worn with mermaid
tail」「Transition from human lower body to whale tail lacks anatomical connection」）。
结论：**不改提示词**；同时把 `tail_attachment` / `mermaid tail` 类缺陷当成**已知的评分器误报**
（人设本来就是「人类腿 + 后腰鲸尾配件」，VL 的世界模型把它读成解剖矛盾），
以后看缺陷率时**不要**把它当成模型退化信号——真正该盯的是 `hands` / `head_ratio` / `watermark`。

> 顺带说明：这条也解释了为什么「鲸尾露出度」实验是弱信号（§2 表格）——评测器对这个特征的判断本身就不稳。

---

## 29. 最大的质量缺口：最终提示词里**根本没有画风句**（2026-10-16 05:0x）

### 29.1 事实：配置写着「二次元」，提示词里一个画风词都没有

生产拼装出的最终提示词（797 字符，`selfie-长请求`，natural_language）逐词扫描：
`style` / `anime` / `illustration` / `cel` / `line art` / `3D` / `render` / `shading` / `插画` / `画风`
**全部 0 次命中**。整条提示词只有「场景 + 身份锚 + 姿势 + 手 + 水印 + Avoid」，没有任何风格约束。

代码上是两条路都断了：

1. **写作模型那条**：`_photo_generation_prompt_format_instruction()`（`proactive_message.py:13287`）
   在 natural_language 分支里要求模型覆盖「…构图与**风格**」，但只是「要求」，模型
   （`PHOTO_PROMPT_PROVIDER_ID`，当时是 `xiaomi-token-plan/mimo-v2.6-flash`）实际不写；
2. **确定性那条**：`_photo_style_prompt_en()`（`proactive_message.py:11879`）里明明有
   `"2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling"`，
   但它的两个使用点（`proactive_message.py:14618` / `:14625`）都在 `if not image_prompt:` 里
   ——**只有写作模型什么都没产出时才用**。

于是画风完全由「参考图 + 模型默认审美」决定：脸部裁切参考图会把它拉向 2D，而模型默认**偏半 3D**，
两边打架 → 这正是六轴里 `style` 轴长期最低/最抖、缺陷文本反复出现
「Mixed art styles: realistic human hand in foreground vs anime character」的原因。

### 29.2 同一次盲评的 A/B：加一句画风，总分 +5.5

对照设计：两条提示词**只有画风句之差**，都用生产用的脸部裁切参考图、1024²、4.0（`jimeng_t2i_v40`）、
n=4，**同一次 `eval_vl_score` 调用**（遵守 §28.1 的规则）：

| 轴 | base（668 字符，无画风句） | style2d（786 字符，+画风句） |
| --- | --- | --- |
| head_proportion | 7.25 | 7.75 |
| hands | 6.0 | 6.5 |
| face | 8.5 | 8.75 |
| **style** | **6.25** | **9.25** |
| anatomy | 6.5 | 7.0 |
| finish | 7.75 | 8.5 |
| **总分（均值）** | **42.25** | **47.75** |

加的正是生产代码里已有的那句
`2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling.`

机制在缺陷文本里直接可见：base 臂 4 张里 3 张被报
「Mixed art styles: realistic human hand in foreground vs anime character」/
「Mixed media style with real photo hand and photorealistic background」（其中两张 style 只给 4 分、3 分），
style2d 臂 4 张**没有一条**这类缺陷；`eval_vl_defects.py` 也显示 style2d 臂 3/4 完全无缺陷。
（base 臂的缺陷统计只跑到 1 张就被免费网关 502 打断，但这 1 张就已报 `watermark, hands`。）

出图数量：base 4/4、style2d 4/4，全部走回退模型 `jimeng_t2i_v40`，单张 18.5–19.6s。

### 29.3 为什么不趁夜直接改：这是**预算取舍**，不是纯新增

自然语言分支的预算逻辑（`photo_prompt_context.py:815-844`）：
`head_budget = max(200, _PROMPT_CHAR_BUDGET - len(tail))`，`_PROMPT_CHAR_BUDGET = 786`，
上游硬上限 800。生产自拍实测提示词 **746–800 字符**，即头部几乎没有余量；
而画风句要 60（短版）~117（代码里那句全句）字符。所以「永远带上画风句」必然要**挤掉**别的东西：

- 挤头部场景 = 用户那句「在海边散步、风大、侧脸近景」被裁（头部裁剪是 `preserve_tail=False`，从右侧丢）；
- 挤 `Avoid` 段 = 手部负面词/水印词被裁（§24 修过：水印词必须留最后，手部词是实测有效的）；
- 挤身份锚 = 参考图一致性下滑。

三个都是产品取舍，不是 bug 修复，而且 05:0x 无人值守时误裁生产提示词的风险高于收益
（今晚刚因为「静默裁掉要求句」踩过一次，见 §27）。**结论：只记录 + 给方案，等白天决定。**

### 29.4 给白天的三个方案（按推荐度）

1. **给画风句留固定预算并压缩头部**（推荐）：把 `_PROMPT_CHAR_BUDGET` 降到 ~720 并把
   `_STYLE_CLAUSE = "Flat 2D anime illustration, cel shading, clean line art."`（57 字符）
   无条件拼进 tail（和「水印句永不去重」同一层、同一理由）；代价是场景描述短 ~60 字符。
   验证手段已就绪：`scripts/audit_prompt_assembly.py`（10 用例 + 20 次轮换、断言 ≤800）、
   `scripts/run_plugin_tests.py astrbot_plugin_image_companion`、以及 §29.2 的同 pass A/B 复跑。
2. **写作模型那条路补强**：在 `_photo_generation_prompt_format_instruction()` 的 natural_language
   分支里给出画风句**字面模板**（而不是只说「覆盖风格」），让写手直接抄；非确定性，但零预算成本。
3. **只在有余量时加**（不推荐）：`if len(joined) + len(style_clause) <= head_budget:` 才追加。
   实现最省事，但会让画风**时有时无**（§29.2 的 style 差 +3.0，肉眼可见），反而更糟。

> 建议顺序：1 + 2 一起做；做完跑一次 §29.2 的同 pass A/B 复验（预期 style ∈ [9, 10]、总分 ≈47–48）。

### 29.5 ⚠️ 补记（05:1x–05:2x）：画风句**在生产链路上**做了一次真 A/B —— 长版亏，短版赚

§29.2 是在**评测提示词**（668/786 字符，预算无压力）上测的。05:1x 我在**生产链路**上复现了同一实验，
结论**部分相反**——这正是「评测友好 ≠ 生产友好」的典型案例，必须记下来。

对照设计（两轮都遵守 §28.1「A/B 必须同一次盲评」）：

- 被测对象 = 面板排障自拍（`POST /api/plug/.../troubleshooting/test {"type":"image_generation_selfie"}`），
  即**真的生产拼装 + 真参考图 + 真调度**；
- 画风句加在 `_critical_requirements_tail()` 里（`photo_prompt_context.py`，身份句之后），
  改完 `POST /api/plugin/reload {"name":"astrbot_plugin_image_companion"}` 重载（返回「重载成功。」），
  再用服务端请求日志 `logs/jimeng-server-requests.jsonl` 的 `stage=request`→`sent` 字段确认
  新句**真的下发**了（`style=True`，prompt_len 750–754，未触发 800 截断）；
- base 臂 = 回滚后（04:31–04:35）生成的 4 张生产自拍，**与加句臂同一次 `eval_vl_score` 调用**；
- 每臂 n=4，1024²，实际模型 `jimeng_t2i_v40`。

**第一轮：长版画风句（112 字符，= §29.2 那句完整的 `2D anime illustration style, clean detailed character art, cel-shaded rendering, soft colors, slice-of-life feeling.`）→ 亏 3.5 分**

| 轴 | base（无画风句） | style_after（长版） |
| --- | --- | --- |
| **head_proportion** | 7.0 | **4.25** |
| hands | 6.25 | 6.75 |
| face | 8.25 | 8.0 |
| style | 7.75 | 9.0 |
| **anatomy** | 7.5 | **5.5** |
| finish | 8.25 | 8.0 |
| **总分** | **45.0** | **41.5** |

8 张被评的图里，加长版那 4 张**全部**被报「oversized head relative to body (chibi proportions)」，
3 张还被报「extremely thin, noodle-like limbs / unnatural leg-to-tail transition」。
即：**多花的 112 字符把它自己赚来的 style 分从「比例/解剖」上赔回去了**（头部预算被压 + 措辞偏可爱）。
→ 这一版**已回退**，不留代码。

**第二轮：短版画风句（51 字符，`2D anime illustration, cel-shaded.`）→ 赚 1.75 分**

| 轴 | base（无画风句） | style_short |
| --- | --- | --- |
| head_proportion | 7.5 | 7.5 |
| hands | 7.0 | 5.75 |
| face | 8.5 | 8.75 |
| **style** | 7.75 | **9.75** |
| anatomy | 7.75 | 8.0 |
| finish | 8.25 | 8.75 |
| **总分** | **46.75** | **48.5** |

短版保住了全部比例/解剖分（头身比不再塌），style 直接给到 9.75，缺陷文本里
「3D-rendered appearance rather than pure 2D anime」这类抱怨消失；唯一退的是 `hands`
（7.0 → 5.75，其中 1 张手部只给 3 分）。手部轴本来就抖（§13/§23），但这个退化要盯着。

**为什么长版会亏、短版会赚（机制）**：`head_budget = max(200, 786 - len(tail))`，
tail 每多 1 字符就从头部（场景 + 比例/身份句的右侧）少 1 字符；长版多吃的 61 字符正好压到
「Small head, about 7 heads tall, long legs」与场景交界处，再叠加 `slice-of-life` 的可爱暗示
→ 直接变 chibi。短版 51 字符在余量内，不动比例契约。

**两条可复用的教训**：

1. **评测提示词必须用生产长度**。同一个画风句在 668 字符的评测提示词上 +6.75，在 750–800 字符的
   生产提示词上（长版）−3.5。评测如果为了「干净对照」把提示词压短，就会推荐**有害**的改动。
2. **tail 里的每一句都在偷头部的预算**。「加在尾部、尾部署保」≠ 免费：尾部越长，头部的场景与
   比例契约被裁得越多。以后往 tail 加句子必须复跑生产 A/B，而不是只看评测 A/B。

**第三轮（池化，n=8 vs n=4，同一次盲评）+ 最终决定**

把短版扩到 8 张（又跑 4 张，同一提示词）后，base 臂在同一次调用里给到 48.75，
短版 8 张 = **46.5** → 短版这一轮是 **−2.25**。三轮汇总：

| 轮次 | 臂 | base 总分 | 加句总分 | Δ | style 轴 Δ | hands 轴 Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 长版 | n=4 vs 4 | 45.0 | 41.5 | **−3.5** | +1.25 | +0.5 |
| 短版 r1 | n=4 vs 4 | 46.75 | 48.5 | **+1.75** | +2.0 | −1.25 |
| 短版 r2 | n=8 vs 4 | 48.75 | 46.5 | **−2.25** | +0.63 | −0.75 |

读法：**总分在 ±2 之间翻符号 → 短版对总分没有可测收益**（base 臂自身跨 pass 就漂 2 分，见 §28.1）；
唯一跨三轮**同向**的是 `style` 轴（+0.63~+2.0，每次都更好）与 `hands` 轴（短版两轮都 −0.75~−1.25）。
结构性缺陷侧另有一份反证：`eval_vl_defects.py` 对短版 8 张跑到 7 张时（第 8 张碰上免费网关 502），
**6/7 完全无缺陷、0 条 hands**（唯一 1 条是 `watermark`）——即六轴里 `hands` 的小幅下滑
没有变成结构缺陷，更像「2D 化后手部细节被简化」的观感差异。

**决定：保留短版（51 字符 `2D anime illustration, cel-shaded.`）**，理由是它稳定兑现了**配置里写明的
「二次元」意图**（`style` 轴 7.75–8.75 → 9.0–9.75，缺陷文本里「3D-rendered appearance rather than
pure 2D anime」消失），而总分差落在仪器的噪声区间内、结构缺陷未增加。**这不是「测到收益」，
而是「没有测到损失 + 对齐既定画风意图」**，所以留了一条一句话可回退的路：
删掉 `photo_prompt_context.py` 里 `_critical_requirements_tail()` 中那段 `if not carried("anime illustration"…)`
即可（回退后 `audit_prompt_assembly.py` 与生产自拍都不需要其它改动）。长版（117 字符全句）**不回退余地**——
它在生产上确定亏 3.5 分且 4/4 张变 chibi，**已彻底删除**。

> ⚠️ 人工目视的边界（别把它当结论）：短版 8 张里我也看到 2–3 张「头偏大、身体偏细」（如
> `generated_photos/private_companion_troubleshooting_20261016_051019_7461de58.png`、
> `…_052316_5ef5415d.png`），基线臂里也有偏 Q 版的帧（`…_043157_243ead22.png` 是近景大头构图）——
> 两臂的**机位分布不同**，单张目视不能拿来判比例；而 `head_proportion` 轴这次也没把差异量出来
> （基线 7.0–7.5 vs 短版 7.0–7.5）。比例问题仍然只能靠「固定机位 + 同 pass 多张」的实验来判。
