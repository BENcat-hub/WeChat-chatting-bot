# 即梦（Jimeng）生图接入——实测结论与待办

> 记录于 2026-10-15 18:40（Asia/Shanghai）。用户给了「即梦 4.0 / 4.6」的 API token，要求把
> private_companion 的在线生图模型换过去。本文件是实测取证结果，供下一步决策使用。
> Token 已另存 `secrets/jimeng_api_key.txt`（不再挂在 AstrBot 的 provider_sources 里）。

## 1. 一句话结论

**这个 token 单独无法调用即梦 4.0 / 4.6。**
- 它在火山引擎体系里是「格式合法且真实存在」的凭证；
- 但它**对火山方舟任意模型都没有权限**（文字模型、图片模型全部 404）；
- 而即梦 4.0/4.6 的官方接口（智能视觉平台）**要求 AK/SK 签名鉴权**，Bearer 直传被拒。

## 2. 实测证据（可复现）

对 `POST https://ark.cn-beijing.volces.com/api/v3/images/generations`（Bearer 鉴权）逐项打表：

| 用的 key | 结果 |
| --- | --- |
| 用户给的 token | `404 InvalidEndpointOrModel.NotFound`（**通过鉴权**，止步于模型解析） |
| `sk-bogus` | `401 AuthenticationError: The API key format is incorrect` |
| 随机 UUID `12345678-1234-...` | `401 AuthenticationError: The API key doesn't exist` |
| 同形状随机串 `AAAAAAAAA.<44B>.<66B>` | `401 ... format is incorrect` |

→ 401 与 404 的区别说明：**用户 token 是真实存在的凭证**，只是拿不到模型。
（随机同形状串仍被判格式错误，说明格式校验包含内部校验位，不是随便凑形状就能过。）

同一 token 在这些模型 ID 上全部 404（等于「不存在或你没有权限」）：

- 图片：`doubao-seedream-4-0-250828`（4.0 真实 ID）、`doubao-seedream-4-5-251128`、
  `doubao-seedream-5-0-260128`、`doubao-seedream-5-0-lite-260128`、`doubao-seedream-5-0-flash-260915`、`doubao-seedream-5-0-pro-260628`、
  `seedream-4-0`、`seedream-4-6`、`seedream-4.6`、`jimeng-4.6`
- 文字：`doubao-seed-1-6-250615`、`doubao-seed-1-6-flash-250715`、`doubao-1-5-pro-32k-250115`、
  `doubao-seed-1-8-251015`、`doubao-pro-32k`、`deepseek-v3-250324`
- 国际站 `https://ark.ap-southeast.bytepluses.com/api/v3/images/generations` 与北京站表现一致（同样 404）。

即梦官方接口（视觉智能平台）：

- 4.0 接口文档（b18 已核对）：`POST https://visual.volcengineapi.com?Action=CVSync2AsyncSubmitTask&Version=2022-08-31`，
  header 需 `Region: cn-north-1` / `Service: cv`，**鉴权=火山引擎签名（AK/SK）**，body `req_key=jimeng_t2i_v40`。
- 4.6 是「即梦AI-图片生成4.6」（docs 85621 / JimengAI），产品说明：基于 Seedream4.0 训练的即梦同源生图版本，
  同样需要先在智能视觉控制台**开通服务**；第三方聚合调用里它的 req_key 形如 `jimeng_seedream46_cvtob`。
- 用 `Authorization: Bearer <token>` 直连该接口 →
  `400 {"Error":{"CodeN":100024,"Code":"InvalidAuthorization","Message":"Invalid 'Authorization' header..."}}`；
  不带鉴权 → `CodeN 100025 InvalidCredential`。即 **Bearer 不是该接口的鉴权方式**。

## 3. 为什么方舟上不去

火山方舟的 API Key 支持「资源项目隔离 + 按 Model ID 细粒度授权」（docs 82379/1361424）。
该 token 对所有模型 404，典型原因是：**它被授予的权限范围里没有这些模型，或者它属于另一个资源项目 /
另一个产品（智能视觉）**，而不是「Model ID 写错了」——4.0 的 ID 是文档里确认存在的。

另注：即梦 4.6 不在方舟的 Seedream 型号线上。方舟当前是
`doubao-seedream-5-0-*`（pro/flash/lite）、`doubao-seedream-4-5-251128`、`doubao-seedream-4-0-250828`；
「即梦4.6」是智能视觉平台的独立产品。

## 4. 用户可选的两条路（需要用户拍板/给料）

**路线 A（方舟，最省事，插件原生支持）**
1. 在火山方舟控制台把生图模型开通，并确认该 API Key 的权限包含这些 Model ID（或权限改成「全部」）；
2. 把控制台里模型的确切 **Model ID 或推理接入点 ID（ep-…）** 发给我；
3. 我把插件生图配置改成：`external_image_api_platform=doubao`，
   `EXTERNAL_IMAGE_API_BASE_URL=https://ark.cn-beijing.volces.com/api/v3`，模型填对应 ID。
   （插件已有 `doubao` 平台=「豆包/火山方舟」，走 `/images/generations`，Bearer 鉴权。）

**路线 B（视觉智能，真正的即梦 4.0/4.6）**
1. 用户提供 **AccessKeyID + SecretAccessKey**（不是现在这个 token）；
2. 因为插件只会 Bearer 调用，需要我加一个本地小服务：做火山 V4 签名 → 暴露成
   OpenAI 兼容的 `/v1/images/generations`，再把插件指到它（req_key：4.0 = `jimeng_t2i_v40`，4.6 待确认）。

**路线 C（不改）** 继续用现在的硅基流动 `Qwen/Qwen-Image-Edit-2509`（主）/ `Qwen/Qwen-Image`（备）。

## 5. 相关配置落点（改动时用）

插件配置：`astrbot/data/config/astrbot_plugin_private_companion_config.json`

- `external_image_api_platform` / `backup_external_image_api_platform`（平台，允许 `doubao`）
- `EXTERNAL_IMAGE_API_BASE_URL` / `EXTERNAL_IMAGE_API_KEY` / `EXTERNAL_IMAGE_API_MODEL`
- `BACKUP_EXTERNAL_IMAGE_API_BASE_URL` / `BACKUP_EXTERNAL_IMAGE_API_KEY` / `BACKUP_EXTERNAL_IMAGE_API_MODEL`
- 注意：这些键在文件里**出现两次**（顶层一份、嵌套配置一份），改的时候两处都要改，
  否则面板与运行态会不一致。

## 6. 探针脚本

`scripts/_probe_jimeng_api.py`（Bearer/Ark/视觉智能三路探测）、`scripts/_probe_ark_models.py`（型号与权限）、
`scripts/_probe_ark_control.py`（真假 key 对照，就是上表 401/404 的实验）。
