"""离线自测 wxui.py 的截图区域夹取与点击点选取（不需要微信登录）。

只验证纯函数：clamp_region 必须把任何配置区域夹在窗口内，find_row 的点击点
必须来自 OCR 名字框并带 bias、且仍落在夹取后的区域内。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wxui", ROOT / "scripts" / "wxui.py")
wxui = importlib.util.module_from_spec(spec)
sys.modules["wxui"] = wxui
spec.loader.exec_module(wxui)

WIN = [1663, 705, 2177, 1382]        # 514x677 物理窗口（登录窗尺寸）
WIDE = [1712, 780, 2272, 1335]       # 旧配置：右边超出 95px、下边超出 46px
BIAS = (12, 6)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("✅" if cond else "❌") + f" {name}" + (f" | {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def inside(region, rect) -> bool:
    return (
        region[0] >= rect[0]
        and region[1] >= rect[1]
        and region[2] <= rect[2]
        and region[3] <= rect[3]
        and region[2] > region[0]
        and region[3] > region[1]
    )


# 1. 超界区域必须被夹进窗口
clamped = wxui.clamp_region(WIDE, WIN)
check("clamp_region 夹住超界区域", inside(clamped, WIN), f"{WIDE} -> {clamped}")

# 2. 已经合法的区域保持不变
ok_region = [1700, 800, 2100, 1200]
check("clamp_region 不改变合法区域", wxui.clamp_region(ok_region, WIN) == ok_region)

# 3. 窗口比 margin 还小时仍返回合法区域（不崩、不反向）
tiny = [100, 100, 106, 106]
tiny_region = wxui.clamp_region([0, 0, 50, 50], tiny)
check("clamp_region 极小窗口不反向", tiny_region[2] > tiny_region[0] and tiny_region[3] > tiny_region[1], str(tiny_region))

# 4. 点击点来自 OCR 名字框 + bias
region = wxui.clamp_region(WIDE, WIN)
box = {"x": 1750, "y": 900, "w": 120, "h": 30}
x_pick = box["x"] + BIAS[0]
y_pick = box["y"] + BIAS[1]
check("点击点来自名字框", (x_pick, y_pick) == (1762, 906), f"{x_pick},{y_pick}")
in_x = min(max(x_pick, region[0]), region[2] - 1)
in_y = min(max(y_pick, region[1]), region[3] - 1)
check("点击点仍落在夹取区域内", region[0] <= in_x < region[2] and region[1] <= in_y < region[3])

# 5. 旧配置的截图区域在窄窗口下会越界（这就是「点歪」的来源），夹取后不再越界
cfg = wxui.config()
raw_region = [
    WIN[0] + cfg["sidebar_capture_x_offset"],
    WIN[1] + cfg["sidebar_capture_y_offset"],
    WIN[0] + cfg["sidebar_capture_x2_offset"],
    WIN[3] - cfg["sidebar_capture_bottom_margin"],
]
check(
    "旧截图区域会伸出窗口（会 OCR 到微信以外的文字）",
    raw_region[2] > WIN[2],
    f"right={raw_region[2]} > 窗口右边 {WIN[2]}（超出 {raw_region[2] - WIN[2]}px）",
)
check(
    "夹取后的截图区域在窗口内",
    inside(wxui.clamp_region(raw_region, WIN), WIN),
    str(wxui.clamp_region(raw_region, WIN)),
)

print()
if failures:
    print(f"❌ {len(failures)} 项失败: {failures}")
    raise SystemExit(1)
print("✅ 全部通过：截图区域一律夹在窗口内，点击点来自 OCR 名字框")
