# -*- coding: utf-8 -*-
"""TTS 入口：连被截断的标签残片（如 '</pc'）一起剥掉。

实测 2026-11-10 14:21 nahida-tts.log：
    text='睡不着就别硬躺着了，我陪你说会儿话 </pc。'
即上一条链路把 `</pc_tts>` 截成了 `</pc`，正则要求 `pc_名字` 才能匹配，
残片就漏进了合成。
"""
import io, shutil, time

P = "scripts/nahida_tts_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")
s = io.open(P, encoding="utf-8").read()

old = '''    s = re.sub(r"</?[A-Za-z_][\w:.-]*(?:\s[^>]*)?/?>", "", s)
'''
new = '''    s = re.sub(r"</?[A-Za-z_][\w:.-]*(?:\s[^>]*)?/?>", "", s)
    # 被截断的标签残片：`</pc`、`<pc_tts` 这类没有收尾 `>` 的，单独清一遍
    s = re.sub(r"</?\s*(?:pc|tc)(?:_[A-Za-z0-9_]*)?\b[^<>]*$", "", s)
    s = re.sub(r"</?\s*(?:pc|tc)(?:_[A-Za-z0-9_]*)?\b[^<>]*>", "", s)
'''
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new)
shutil.copy2(P, f"{P}.bak-trunc-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("ok; backup", STAMP)
