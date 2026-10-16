"""List stored private-user records keyed by anything containing the friend id."""
import json
from pathlib import Path

STORE = Path(r"<REPO_ROOT>\astrbot\data\plugin_data\astrbot_plugin_private_companion\companions.json")
SUB = "1000000013"

data = json.loads(STORE.read_text(encoding="utf-8"))
users = data.get("users") or {}
print("total users:", len(users))
for key, value in users.items():
    if SUB not in str(key):
        continue
    fields = {k: value.get(k) for k in (
        "identity_subject_id", "identity_platform_kind", "identity_adapter_instance_id",
        "identity_bot_id", "umo", "last_inbound_umo", "nickname", "enabled",
    ) if isinstance(value, dict) and k in value}
    created = value.get("created_at") if isinstance(value, dict) else None
    print(f"\nKEY={key!r}\n   {fields}\n   created_at={created}")
print("\naliases:", {k: v for k, v in (data.get("private_user_aliases") or {}).items() if SUB in str(k) or SUB in str(v)})
print("\nkeys sample:", list(users.keys())[:12])
