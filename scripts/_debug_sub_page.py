#!/usr/bin/env python3
"""Debug: check sub_links output and test sub_page URL."""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from Utils import utils, marzban_api

for user in marzban_api._list_users_raw():
    username = user.get("username", "?")
    compat = marzban_api._user_to_compat(user)
    uuid_val = compat.get("uuid", "")
    sub_id_compat = compat.get("sub_id", "")
    sub_url = user.get("subscription_url", "-")
    print(f"--- {username} ---")
    print(f"  uuid={uuid_val}")
    print(f"  sub_id_compat={sub_id_compat}")
    print(f"  subscription_url={sub_url}")
    try:
        links = utils.sub_links(uuid_val)
        print(f"  sub_links result type={type(links)}")
        if links:
            print(f"  sub_page={links.get('sub_page', 'MISSING_KEY')}")
            print(f"  sub_link={links.get('sub_link', 'MISSING_KEY')}")
        else:
            print(f"  sub_links returned: {links!r}")
    except Exception:
        traceback.print_exc()
