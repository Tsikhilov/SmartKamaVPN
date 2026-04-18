#!/usr/bin/env python3
"""Minimal test: send QR + buttons via Bot API without importing bot.py."""
import sys, os, json, requests, sqlite3, html as html_mod
from urllib.parse import quote

os.chdir("/opt/SmartKamaVPN")
sys.path.insert(0, "/opt/SmartKamaVPN")

from Utils import utils

USERS_DB = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
conn = sqlite3.connect(USERS_DB)
BOT_TOKEN = conn.execute("SELECT value FROM str_config WHERE key='bot_token_client'").fetchone()[0]
CHAT_ID = 500661557

# kamil's uuid
uuid_val = "c3b90bf0-fa36-43a2-b7c0-61a0ec28cc60"
links = utils.sub_links(uuid_val)
sub_url = links.get('sub_link_auto') or links.get('sub_link')
print(f"sub_url = {sub_url}")

# Find/create short link
row = conn.execute("SELECT token FROM short_links WHERE target_url=?", (sub_url,)).fetchone()
if row:
    token = row[0]
else:
    token = "kamil"
    conn.execute("INSERT OR IGNORE INTO short_links (token, target_url, created_at) VALUES (?, ?, datetime('now'))", (token, sub_url))
    conn.commit()
short_link = f"https://sub.smartkama.ru/{token}?app=1"
print(f"short_link = {short_link}")

# QR
qr = utils.txt_to_qr(short_link)
qr_bytes = qr.getvalue() if hasattr(qr, 'getvalue') else (qr if isinstance(qr, bytes) else b'')
print(f"QR size = {len(qr_bytes)}")

conn.close()

# Build caption
caption = (
    f"\U0001F310 Ваша подписка\n\n"
    f"\U0001F517 Ссылка:\n<code>{html_mod.escape(short_link)}</code>\n\n"
    f"\U0001F4F1 Скопируйте ссылку и вставьте в VPN-приложение,\n"
    f"или отсканируйте QR-код."
)

# Deeplinks
happ_dl = f"v2raytun://install-config?url={quote(short_link, safe='')}"
streisand_dl = f"streisand://import/{quote(short_link, safe='')}"

# Test 1: Custom scheme buttons
print("\n=== Test 1: Custom scheme URL buttons ===")
markup1 = json.dumps({"inline_keyboard": [
    [{"text": "\U0001F4F2 Открыть в Happ", "url": happ_dl}],
    [{"text": "\U0001F4F2 Открыть в Streisand", "url": streisand_dl}],
    [{"text": "\U0001F519 Назад", "callback_data": f"smartkamavpn_sub_open:{uuid_val}"}],
]})
r1 = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
    data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "reply_markup": markup1},
    files={"photo": ("qr.png", qr_bytes, "image/png")}, timeout=10)
j1 = r1.json()
print(f"  ok={j1.get('ok')}, desc={j1.get('description','')}")

# Test 2: HTTPS-only URL button
print("\n=== Test 2: HTTPS URL button ===")
markup2 = json.dumps({"inline_keyboard": [
    [{"text": "\U0001F517 Открыть ссылку подписки", "url": short_link.split("?")[0]}],
    [{"text": "\U0001F519 Назад", "callback_data": f"smartkamavpn_sub_open:{uuid_val}"}],
]})
r2 = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
    data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML", "reply_markup": markup2},
    files={"photo": ("qr.png", qr_bytes, "image/png")}, timeout=10)
j2 = r2.json()
print(f"  ok={j2.get('ok')}, desc={j2.get('description','')}")

# Test 3: No URL buttons, only callback + text
print("\n=== Test 3: Only text message ===")
r3 = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
    json={"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML",
          "reply_markup": {"inline_keyboard": [
              [{"text": "\U0001F519 Назад", "callback_data": f"smartkamavpn_sub_open:{uuid_val}"}]]}},
    timeout=10)
j3 = r3.json()
print(f"  ok={j3.get('ok')}, desc={j3.get('description','')}")

print("\nDone.")
