#!/usr/bin/env python3
"""Quick test: sub_links + sub_page handler flow."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Utils import utils
from Utils import api
from config import PANEL_PROVIDER, THREEXUI_PANEL_URL, MARZBAN_PANEL_URL

print(f"PANEL_PROVIDER={PANEL_PROVIDER}")
print(f"THREEXUI_PANEL_URL={THREEXUI_PANEL_URL}")
print(f"MARZBAN_PANEL_URL={MARZBAN_PANEL_URL}")

# Get kamil's UUID
uuid = "c3b90bf0-fa36-43a2-b7c0-61a0ec28cc60"

print(f"\n--- api.find(uuid={uuid[:8]}...) ---")
try:
    result = api.find(uuid=uuid)
    if result:
        print(f"  Found: sub_id={result.get('sub_id')}, name={result.get('name')}")
    else:
        print("  NOT FOUND")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print(f"\n--- utils.sub_links({uuid[:8]}...) ---")
try:
    links = utils.sub_links(uuid)
    for k, v in links.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print(f"\n--- QR code test ---")
try:
    test_url = "https://sub.smartkama.ru/test"
    qr = utils.txt_to_qr(test_url)
    print(f"  QR generated: type={type(qr).__name__}, len={len(qr) if qr else 0}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("\nDONE")
