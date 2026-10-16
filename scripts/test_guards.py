r"""发送护栏自测（不真发消息，dry_run 拦在 _post 里）。

用法：akasha-bridge\.venv\Scripts\python.exe scripts\test_guards.py
退出码 0 = 全部通过。
"""
import os
import sys

BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "akasha-bridge")
sys.path.insert(0, os.path.abspath(BRIDGE))

from weemessenger_sender import WeMessengerSender  # noqa: E402

FAILS = []


def check(name, got, want):
    ok = got == want
    print(("PASS  " if ok else "FAIL  ") + f"{name}: got={got} want={want}")
    if not ok:
        FAILS.append(name)


# 1. 白名单：不在名单里的对象被拦，名单里的放行
s = WeMessengerSender(whitelist=["允许的人"], dry_run=True, min_interval_contact=0, min_interval_global=0)
check("白名单拦截非名单对象", s.send_text("文件传输助手", "hi"), False)
check("白名单放行名单对象", s.send_text("允许的人", "hi"), True)

# 2. 黑名单优先于白名单
s = WeMessengerSender(blacklist=["文件传输助手"], dry_run=True, min_interval_contact=0, min_interval_global=0)
check("黑名单拦截", s.send_text("文件传输助手", "hi"), False)

# 3. 限频：同对象两次发送之间会被**排队等待**（不是拒绝），等待时长 = min_interval_contact
import time  # noqa: E402

s = WeMessengerSender(dry_run=True, min_interval_contact=0.6, min_interval_global=0)
t0 = time.time()
check("限频第一条放行", s.send_text("文件传输助手", "hi"), True)
check("限频第二条放行", s.send_text("文件传输助手", "hi again"), True)
elapsed = time.time() - t0
check("限频确实等待了 (>=0.55s)", elapsed >= 0.55, True)
print(f"      elapsed={elapsed:.2f}s")

# 4. 空文本
s = WeMessengerSender(dry_run=True, min_interval_contact=0, min_interval_global=0)
check("空文本拒绝", s.send_text("文件传输助手", ""), False)

print()
if FAILS:
    print(f"{len(FAILS)} 项失败: {FAILS}")
    sys.exit(1)
print("全部通过")
