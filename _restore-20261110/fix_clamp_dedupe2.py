# -*- coding: utf-8 -*-
"""v4：去重判据改成「身份标记的位置是否已落在保留的头部里」。"""
import io, shutil, time
P = "scripts/jimeng_image_server.py"
STAMP = time.strftime("%Y%m%d-%H%M%S")
s = io.open(P, encoding="utf-8").read()
old = '''    if identity and identity[:24] in head:
        # 身份锚已经完整落在保留的头部里，不再重复追加。
        id_part = ""
        budget = max(120, limit - len(tail))
        head = head_text[:budget]
'''
new = '''    marker_positions = [mt.start() for mt in _IDENTITY_HINT_RE.finditer(text)]
    if identity and marker_positions and any(pos < len(head) for pos in marker_positions):
        # 身份标记已经落在保留的头部里（哪怕窗口被截断），就不再重复追加。
        id_part = ""
        budget = max(120, limit - len(tail))
        head = head_text[:budget]
'''
assert s.count(old) == 1, s.count(old)
shutil.copy2(P, f"{P}.bak-idwin3-{STAMP}")
io.open(P, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))
print("patched ->", f"jimeng_image_server.py.bak-idwin3-{STAMP}")
