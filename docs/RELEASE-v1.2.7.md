## 置顶会话被折叠时定位不到人

用户实测反馈：「找不到人是因为置顶聊天被折叠了」——`陈奥琦` / `桃桃真可爱` / `海蛇` 都是**置顶**会话，微信把整段折成一行「N 个置顶聊天」，侧栏 OCR 里根本没有这些名字，于是 `open_chat` 一直报 `could not locate target row by OCR`。

### 修法
1. 新增 `_sidebar_rows()`（一次 OCR 返回 `(文本, x, y, raw)` 按 y 排序）与 `ensure_pinned_expanded()`。
2. `open_chat` **扫描之前**就先展开置顶，不再等到彻底失败才试。
3. 那一行是「展开/收起」**开关**：已展开时再点会把它折回去（实测导致下一个目标又找不到），所以加 `_PINNED_STATE` 记住状态（`pinned_state_ttl_s` 默认 180s），并在点完发现行数变少时自动再点一次展开回去。
4. 展开判定靠「行数变多 或 那一行本身消失」，并把 `rows_before/rows_after` 写进 `plan["pinned"]` 便于事后诊断。

### 实测（`logs/_bench_open2.py` 同款探针）
- 修前：`陈奥琦` 5689ms 失败、`海蛇` 4868ms 失败、`桃桃真可爱` 1677ms（靠碰巧已在目标）。
- 修后：`陈奥琦` 1761ms、`桃桃真可爱` 905ms、`海蛇` 863ms 全部命中，头部 OCR 分别为 `陈 奥 琦` / `桃 桃 真 可 爱` / `海 蛇`。
- `文件传输助手` 冷路径 3555ms（需要展开一次）、热路径 50ms（`chat already on target (warm session: header verified)`）。

### 顺带说明
- `ensure_pinned_expanded` 返回里 `expanded=False` 但定位成功的情况，是「点了一下反而收起、随即又点开」的中间态记录，不影响结果。
- 之前的重复投递问题（身份闸 / `no_requeue` / 别名宽匹配收紧）已在 v1.2.4–v1.2.5 修完，这次是纯定位增强。
