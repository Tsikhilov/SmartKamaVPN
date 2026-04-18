#!/usr/bin/env python3
"""Проверяем inbounds пользователей и обновляем если нужно."""
import sys, json
sys.path.insert(0, "/opt/SmartKamaVPN")
import config, Utils.marzban_api as mapi, requests

NEW_TAGS = [
    "nl-mob-ws-1", "nl-mob-ws-2",
    "nl-mob-reality-ms", "nl-mob-reality-ap",
    "ru-mob-reality-ms", "ru-mob-reality-ap",
]
OLD_TAGS = ["mob-ws-1", "mob-ws-2", "mob-reality-ms", "mob-reality-ap"]

token = mapi._get_access_token()
base  = config.MARZBAN_PANEL_URL
hdrs  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Все пользователи
resp = requests.get(f"{base}/api/users", headers=hdrs, params={"limit": 100}, timeout=10)
users = resp.json().get("users", [])
print(f"Всего пользователей: {len(users)}\n")

for u in users:
    name = u["username"]
    status = u.get("status")
    cur_inbounds = u.get("inbounds") or {}
    vless = list(cur_inbounds.get("vless", []))
    print(f"  {name} [{status}]: {vless}")

    # Нужно ли обновить?
    vless_clean = [t for t in vless if t not in OLD_TAGS]
    to_add = [t for t in NEW_TAGS if t not in vless_clean]
    if to_add:
        vless_clean += to_add
        new_inbounds = {**cur_inbounds, "vless": vless_clean}
        r = requests.put(
            f"{base}/api/user/{name}",
            headers=hdrs, json={"inbounds": new_inbounds}, timeout=15
        )
        print(f"    → обновлён (+{to_add}): HTTP {r.status_code}")
    else:
        print(f"    → уже актуален")
