#!/usr/bin/env python3
"""
Test: simulate pressing "Подписка и приложения" button by calling the
smartkamavpn_sub_page handler logic and then sending the result via Bot API.
"""
import sys, os, json, requests
sys.path.insert(0, "/opt/SmartKamaVPN")
os.chdir("/opt/SmartKamaVPN")

from Utils import utils, marzban_api
from Database.dbManager import UserDBManager
from config import USERS_DB_LOC
import sqlite3

BOT_TOKEN = None
ADMIN_CHAT_ID = 500661557

# Get bot token
conn = sqlite3.connect(USERS_DB_LOC)
row = conn.execute("SELECT value FROM str_config WHERE key='bot_token_client'").fetchone()
if row:
    BOT_TOKEN = row[0]
conn.close()

if not BOT_TOKEN:
    print("ERROR: No bot token found")
    sys.exit(1)

# Get the user's subscription UUID (kamil's)
uuid_val = "c3b90bf0-fa36-43a2-b7c0-61a0ec28cc60"

# Step 1: sub_links
links = utils.sub_links(uuid_val)
sub_url = (links or {}).get('sub_link_auto') or (links or {}).get('sub_link')
print(f"sub_url = {sub_url}")

if not sub_url:
    print("FAIL: no sub_url")
    sys.exit(1)

# Step 2: resolve display name
db = UserDBManager(USERS_DB_LOC)
sub_name = db.get_order_name_by_uuid(uuid_val)
print(f"sub_name = {sub_name!r}")

# Step 3: build short link
from UserBot.bot import _shorten_subscription_url, _resolve_display_sub_id
short_link = _shorten_subscription_url(sub_url, sub_name=sub_name)
display_url = short_link or sub_url
print(f"display_url = {display_url}")

# Step 4: QR code
qr_code = utils.txt_to_qr(display_url)
qr_bytes = qr_code.getvalue() if hasattr(qr_code, 'getvalue') else (qr_code if isinstance(qr_code, bytes) else b'')
print(f"QR generated: {bool(qr_bytes)}, size: {len(qr_bytes)} bytes")

# Step 5: Try sending via Bot API
import html as html_mod
from urllib.parse import quote

happ_deeplink = f"v2raytun://install-config?url={quote(display_url, safe='')}"
streisand_deeplink = f"streisand://import/{quote(display_url, safe='')}"

caption = (
    f"\U0001F310 Ваша подписка\n\n"
    f"\U0001F517 Ссылка:\n<code>{html_mod.escape(display_url)}</code>\n\n"
    f"\U0001F4F1 Скопируйте ссылку и вставьте в VPN-приложение,\n"
    f"или отсканируйте QR-код,\n"
    f"или нажмите кнопку ниже для автоимпорта."
)

# Test 1: try with custom scheme buttons
print("\n=== Test 1: Custom scheme buttons ===")
markup = {
    "inline_keyboard": [
        [{"text": "📲 Открыть в Happ / V2RayTun", "url": happ_deeplink}],
        [{"text": "📲 Открыть в Streisand", "url": streisand_deeplink}],
        [{"text": "🔙 Назад", "callback_data": f"smartkamavpn_sub_open:{uuid_val}"}],
    ]
}

try:
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": ADMIN_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(markup),
        },
        files={"photo": ("qr.png", qr_bytes, "image/png")},
        timeout=10,
    )
    result = resp.json()
    print(f"  Status: {resp.status_code}")
    print(f"  OK: {result.get('ok')}")
    if not result.get("ok"):
        print(f"  Error: {result.get('description')}")
except Exception as e:
    print(f"  Exception: {e}")

# Test 2: try with only https buttons (fallback)
print("\n=== Test 2: HTTPS-only buttons ===")
markup2 = {
    "inline_keyboard": [
        [{"text": "🔗 Открыть ссылку подписки", "url": display_url.split("?")[0]}],
        [{"text": "🔙 Назад", "callback_data": f"smartkamavpn_sub_open:{uuid_val}"}],
    ]
}

try:
    resp2 = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": ADMIN_CHAT_ID,
            "caption": caption,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(markup2),
        },
        files={"photo": ("qr.png", qr_bytes, "image/png")},
        timeout=10,
    )
    result2 = resp2.json()
    print(f"  Status: {resp2.status_code}")
    print(f"  OK: {result2.get('ok')}")
    if not result2.get("ok"):
        print(f"  Error: {result2.get('description')}")
except Exception as e:
    print(f"  Exception: {e}")
