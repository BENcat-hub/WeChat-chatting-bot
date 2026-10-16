# 第三方组件与许可证

本仓库包含下列第三方组件。它们各自遵循其原始许可证；本项目的 `NOTICE.md` **不改变**这些许可证赋予你的权利，也不为其附加额外限制。
分发或再使用前请自行阅读各组件仓库中的完整许可证原文。

| 组件 | 版本 | 许可证 | 上游 | 在本项目中的位置 / 作用 |
|---|---|---|---|---|
| **WeFlow** | 5.0.0（源码树） | **CC BY-NC-SA 4.0**（署名—非商业性使用—相同方式共享） | https://github.com/hicccc77/WeFlow （作者：cc / yccccccy@proton.me） | `weflow/`。微信本地数据库读取 + HTTP/SSE 接口。**作者与应用信息不得修改**；须保留署名；不得商用；再分发须以相同方式共享 |
| **AstrBot** | 4.28.2 | **AGPL-3.0-or-later** | https://github.com/AstrBotDevs/AstrBot | 通过 pip 安装（不在本仓库源码内），`astrbot/.venv/` 由你自行 `pip install astrbot==4.28.2` 生成。聊天机器人框架本体 |
| **WeeMessenger（小信使）** | — | **MIT** Copyright (c) 2026 KoKoMilk | 见 `weemessenger/WeeMessenger-main/` | `weemessenger/`。可选的微信消息发送器后端（本项目默认不使用） |
| **windows-mcp** | 0.8.8 | **MIT** | PyPI `windows-mcp` | `scripts/wxui.py` 通过它做窗口截图/点击/剪贴板等 Windows UI 自动化。需自行 `pip install windows-mcp` |
| **FFmpeg** | ffmpeg-static 提供 | LGPL/GPL（视构建而定） | https://ffmpeg.org | `weflow/node_modules/ffmpeg-static/ffmpeg.exe`（若随包分发）。供 WeFlow 解码 HEVC/`wxgf` 微信图片 |
| **AstrBot 插件依赖**（见 `astrbot/requirements.txt`） | - | 各自许可证 | - | AstrBot 运行时依赖（aiohttp、openai、apscheduler 等） |

## 关于 WeFlow 的特别说明

WeFlow 是本仓库中唯一采用 **CC BY-NC-SA 4.0** 的组件。该许可证要求：

1. **署名**：必须保留原作者署名与来源链接（`weflow/package.json` 的 `author` / `repository` 字段与 `weflow/LICENSE` 原文不得删除或篡改）。
2. **非商业性使用**：不得用于商业目的。本项目整体也因此**不可用于商业用途**。
3. **相同方式共享**：若你修改并再分发 WeFlow（或其衍生作品），必须以相同许可证（CC BY-NC-SA 4.0）发布你的修改。

`weflow/` 目录中的二进制补丁（见 `patches/weflow/`）属于对该组件的修改，再分发时同样受 CC BY-NC-SA 4.0 约束。

## 关于 AstrBot（AGPL-3.0）的特别说明

AGPL-3.0 是最强的 copyleft 许可证之一，其**网络条款**要求：若你修改 AstrBot 并通过网络向他人提供服务，必须向这些使用者提供修改后的完整源代码。

本项目**没有**修改 AstrBot 本体（`astrbot/` 只是数据目录与虚拟环境），因此正常情况下不触发该义务。但你自研的插件是否被视为衍生作品，取决于你与 AstrBot 之间的调用关系与法律解释——如需对外提供服务，请自行评估。

## 依赖清单

- `astrbot/requirements.txt` — AstrBot 本体版本
- `astrbot/data/plugins/*/requirements.txt` — 各插件的 Python 依赖（如有）
- `akasha-bridge/requirements.txt` — 桥接服务依赖
- `weemessenger/WeeMessenger-main/requirements.txt` — 小信使依赖
- `weflow/package.json` — WeFlow 的 npm 依赖（未随包分发 `node_modules/`，需自行 `npm install`）
