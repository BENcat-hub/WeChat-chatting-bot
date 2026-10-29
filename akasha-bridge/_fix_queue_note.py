# -*- coding: utf-8 -*-
"""一次性把 ob_protocol.py 里「已交重发队列」的假话换成事实。"""
from pathlib import Path

p = Path(__file__).with_name("ob_protocol.py")
s = p.read_text(encoding="utf-8")
before = s

pairs = [
    ('log.warning(f"[OB11] 文件未即时验证送达（已交重发队列）: {contact} {os.path.basename(fpath)}")',
     'log.warning("[OB11] 文件未即时验证送达（%s）: %s %s",\n                    _queue_note(contact), contact, os.path.basename(fpath))'),
    ('f"[OB11] ⚠ 文字未验证送达（已交重发队列，稍后自动补发）{contact}: {text[:50]}"',
     '"[OB11] ⚠ 文字未验证送达（%s）%s: %s", _queue_note(contact), contact, text[:50]'),
    ('log.warning(f"[OB11] ⚠ 图片未验证送达（已交重发队列，稍后自动补发）{contact}")',
     'log.warning("[OB11] ⚠ 图片未验证送达（%s）%s", _queue_note(contact), contact)'),
]
for old, new in pairs:
    if old not in s:
        print("MISS:", old[:40])
    s = s.replace(old, new)
p.write_text(s, encoding="utf-8")
print("changed" if s != before else "unchanged")
