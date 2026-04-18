#!/usr/bin/env python3
"""Диагностика v2: смотрим точный формат Reality inbound-ов в /api/hosts."""
import sys, json
sys.path.insert(0, "/opt/SmartKamaVPN")
import config, Utils.marzban_api as mapi, requests

token = mapi._get_access_token()
base  = config.MARZBAN_PANEL_URL
hdrs  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 1. GET /api/hosts — показать ВСЕ ключи и ru-reality-1 запись (Reality inbound)
resp = requests.get(f"{base}/api/hosts", headers=hdrs, timeout=10)
data = resp.json()
print("=== GET /api/hosts ALL keys:", list(data.keys()))
if "ru-reality-1" in data:
    print("\n=== ru-reality-1 (Reality пример):")
    print(json.dumps(data["ru-reality-1"], ensure_ascii=False, indent=2))
if "nl-reality-1" in data:
    print("\n=== nl-reality-1 (Reality пример):")
    print(json.dumps(data["nl-reality-1"], ensure_ascii=False, indent=2))

# 2. Тест PUT только одного Reality entry с пустыми alpn/fingerprint
test_reality = {
    "nl-mob-reality-ms": [{
        "remark": "Test Reality ({USERNAME})",
        "address": "72.56.100.45",
        "port": None,
        "sni": "",
        "host": "",
        "path": None,
        "security": "inbound_default",
        "alpn": "",
        "fingerprint": "",
        "allowinsecure": False,
        "is_disabled": False,
        "mux_enable": False,
        "fragment_setting": None,
        "noise_setting": None,
        "random_user_agent": False,
        "use_sni_as_host": False,
    }]
}
resp2 = requests.put(f"{base}/api/hosts", headers=hdrs, json=test_reality, timeout=20)
print(f"\n=== PUT Reality test (alpn='', fingerprint='') — status: {resp2.status_code}")
print(resp2.text[:300])

# 3. Если 422, пробуем с alpn="none", fingerprint="none"
if resp2.status_code == 422:
    test_reality["nl-mob-reality-ms"][0]["alpn"] = "none"
    test_reality["nl-mob-reality-ms"][0]["fingerprint"] = "none"
    resp3 = requests.put(f"{base}/api/hosts", headers=hdrs, json=test_reality, timeout=20)
    print(f"\n=== PUT Reality test (alpn='none', fingerprint='none') — status: {resp3.status_code}")
    print(resp3.text[:300])
