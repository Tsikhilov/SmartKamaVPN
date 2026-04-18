#!/usr/bin/env python3
"""Test Marzban API auth and user lookup."""
import requests
import sys

MARZBAN_URL = "http://127.0.0.1:8000"

# Try both admin accounts
accounts = [
    ("Tsikhilovk", "Haker05dag$"),
    ("smartkama_admin", "Haker05dag$"),
]

token = None
for username, password in accounts:
    r = requests.post(
        f"{MARZBAN_URL}/api/admin/token",
        data={"grant_type": "password", "username": username, "password": password},
        timeout=10,
    )
    print(f"Auth {username}: {r.status_code} {r.text[:120]}")
    if r.status_code == 200:
        token = r.json().get("access_token")
        print(f"  TOKEN obtained: {token[:20]}...")
        break

if not token:
    print("FAILED to auth with any account")
    sys.exit(1)

# List users
headers = {"Authorization": f"Bearer {token}"}
r = requests.get(f"{MARZBAN_URL}/api/users", headers=headers, timeout=10)
print(f"\nUsers API: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    users = data.get("users", [])
    print(f"Total users: {len(users)}")
    for u in users[:10]:
        uname = u.get("username", "?")
        status = u.get("status", "?")
        expire = u.get("expire", "?")
        used = u.get("used_traffic", 0) / (1024**3) if u.get("used_traffic") else 0
        limit = u.get("data_limit", 0) / (1024**3) if u.get("data_limit") else 0
        print(f"  {uname}: status={status}, used={used:.1f}GB/{limit:.1f}GB, expire={expire}")

# Specific user lookup
for uname in ["kamil-b58c047f", "test-01dcf49b", "1us-f6aaddfb"]:
    r = requests.get(f"{MARZBAN_URL}/api/user/{uname}", headers=headers, timeout=10)
    if r.status_code == 200:
        u = r.json()
        print(f"\n{uname}: status={u.get('status')}, sub_url={u.get('subscription_url','?')[:60]}")
    else:
        print(f"\n{uname}: {r.status_code} {r.text[:80]}")
