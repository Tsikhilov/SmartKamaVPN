#!/usr/bin/env python3
"""Верификация subscription links через правильный URL из Marzban API."""
import sys, json, base64
sys.path.insert(0, "/opt/SmartKamaVPN")
import config, Utils.marzban_api as mapi, requests

token = mapi._get_access_token()
base  = config.MARZBAN_PANEL_URL
hdrs  = {"Authorization": f"Bearer {token}"}

# Первый пользователь
resp = requests.get(f"{base}/api/users", headers=hdrs, params={"limit": 1}, timeout=10)
user = resp.json().get("users", [])[0]
username = user["username"]
sub_url  = user.get("subscription_url", "")
print(f"Пользователь: {username}")
print(f"Subscription URL: {sub_url}")
print(f"Inbound tags: {user.get('inbounds', {}).get('vless', [])}")
print()

urls_to_try = [sub_url or f"{base}/sub/{username}"]

for url in urls_to_try:
    if not url:
        continue
    try:
        r = requests.get(url, headers={"Accept": "text/plain"}, timeout=10, allow_redirects=True)
        print(f"URL: {url}")
        print(f"Status: {r.status_code}, bytes: {len(r.content)}")
        raw = r.text.strip()
        try:
            decoded = base64.b64decode(raw + "==").decode("utf-8", errors="replace")
            links = [l for l in decoded.splitlines() if l.strip()]
        except Exception:
            links = [l for l in raw.splitlines() if l.strip()]
        print(f"Всего ссылок: {len(links)}")
        MOB_PORTS = {"2083", "2087", "2084", "2088", "48011", "48012"}
        print("\n=== Все ссылки (host:port + name) ===")
        for link in links:
            remark = requests.utils.unquote(link.split("#", 1)[-1]) if "#" in link else "?"
            try:
                hostport = link.split("@", 1)[1].split("?")[0].split("/")[0]
            except Exception:
                hostport = "???"
            port = hostport.split(":")[-1] if ":" in hostport else "443"
            marker = "  *** MOB ***" if port in MOB_PORTS else ""
            print(f"  {hostport:35s}  {remark}{marker}")
    except Exception as e:
        print(f"  ERROR {url}: {e}")
