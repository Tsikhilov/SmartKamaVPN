#!/usr/bin/env python3
"""Test smartkamavpn_sub_page handler by simulating what it does for user 500661557."""
import sys, os
sys.path.insert(0, "/opt/SmartKamaVPN")
os.chdir("/opt/SmartKamaVPN")

from Utils import utils, marzban_api
from Database.dbManager import UserDBManager
from config import USERS_DB_LOC

db = UserDBManager(USERS_DB_LOC)

# Get user's subscriptions from Marzban
print("=== Marzban users ===")
for user in marzban_api._list_users_raw():
    compat = marzban_api._user_to_compat(user)
    username = user.get("username", "?")
    uuid_val = compat.get("uuid", "")
    note = user.get("note", "")
    print(f"  user={username} uuid={uuid_val} note_prefix={str(note)[:60]}")

# Get orders for telegram_id=500661557
print("\n=== Orders for 500661557 ===")
try:
    import sqlite3
    conn = sqlite3.connect("/opt/SmartKamaVPN/Database/smartkamavpn.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM orders WHERE telegram_id=500661557").fetchall()
    for r in rows:
        print(f"  order: {dict(r)}")
    conn.close()
except Exception as e:
    print(f"  Error: {e}")

# Now simulate smartkamavpn_sub_page for each user
print("\n=== Simulating smartkamavpn_sub_page ===")
for user in marzban_api._list_users_raw():
    compat = marzban_api._user_to_compat(user)
    uuid_val = compat.get("uuid", "")
    if not uuid_val:
        continue
    
    print(f"\n--- UUID: {uuid_val} ---")
    
    # Step 1: sub_links
    try:
        links = utils.sub_links(uuid_val)
        sub_url = (links or {}).get('sub_link_auto') or (links or {}).get('sub_link')
        print(f"  sub_url = {sub_url}")
    except Exception as e:
        print(f"  sub_links ERROR: {e}")
        continue
    
    if not sub_url:
        print("  FAIL: no sub_url")
        continue
    
    # Step 2: _resolve_display_sub_id (simplified)
    try:
        order_name = db.get_order_name_by_uuid(uuid_val)
        print(f"  order_name = {order_name!r}")
    except Exception as e:
        print(f"  get_order_name error: {e}")
        order_name = None
    
    # Step 3: short link
    try:
        from UserBot.bot import _shorten_subscription_url
        short = _shorten_subscription_url(sub_url, sub_name=order_name)
        print(f"  short_link = {short}")
    except Exception as e:
        print(f"  NOTE: _shorten import failed ({e}), using raw URL")
        short = sub_url
    
    # Step 4: QR code
    qr = utils.txt_to_qr(short or sub_url)
    print(f"  QR code generated: {bool(qr)}")
    
    print(f"  RESULT: Would send photo with link={short or sub_url}")
