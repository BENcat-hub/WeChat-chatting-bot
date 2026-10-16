"""Find the JSON paths that hold the 12:02 image vision summary text."""
import json
from pathlib import Path

STORE = Path(r"<REPO_ROOT>\astrbot\data\plugin_data\astrbot_plugin_private_companion\companions.json")
NEEDLE = "粉发大眼Q版少女"

data = json.loads(STORE.read_text(encoding="utf-8"))
found = []


def walk(node, path):
    if len(found) >= 12:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            walk(value, path + [str(key)])
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            walk(value, path + [f"[{idx}]"])
    elif isinstance(node, str) and NEEDLE in node:
        found.append((path, node))


walk(data, [])
print("paths containing needle:", len(found))
for path, value in found:
    print("\n=== PATH:", " / ".join(path[-6:]), " (full depth %d)" % len(path))
    print("len:", len(value))
    print(value[:1500].replace("\n", " \\n "))
