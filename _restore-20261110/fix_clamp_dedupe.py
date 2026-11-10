# -*- coding: utf-8 -*-
"""修 _identity_rescue 的去重判据：应在「截断后的 head」里判断，而不是整段 head_text。"""
import io, shutil, time
P = "scripts/jimeng_image_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")
src = io.open(P, encoding="utf-8").read()
old = """    identity = _identity_rescue(text, limit)
    if identity and identity in head_text:
        identity = ""
    id_part = f" {identity}" if identity else ""
    budget = max(120, limit - len(tail) - len(id_part))
    head = head_text[:budget]
"""
new = """    identity = _identity_rescue(text, limit)
    id_part = f" {identity}" if identity else ""
    budget = max(120, limit - len(tail) - len(id_part))
    head = head_text[:budget]
    if identity and identity[:40] in head:
        # 身份锚已经在头部保留段里，避免重复。
        id_part = ""
        identity = ""
        budget = max(120, limit - len(tail))
        head = head_text[:budget]
"""
assert src.count(old) == 1, src.count(old)
shutil.copy2(P, f"{P}.bak-idclamp2-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(src.replace(old, new))
print("patched, backup ->", f"jimeng_image_server.py.bak-idclamp2-{STAMP}")
