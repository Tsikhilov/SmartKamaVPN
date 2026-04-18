#!/usr/bin/env python3
"""scripts/_add_mobile_inbounds.py

Adds mobile operator-optimized VPN inbounds to Marzban (SmartKamaVPN).
Works around DPI/throttling from Russian cellular operators (Beeline, MTS, Megafon, Tele2).

New inbounds added:
  mob-ws-1        VLESS+WS via nginx:443 (port 48001) — CDN-compatible WebSocket
  mob-ws-2        VLESS+WS via nginx:443 (port 48002) — CDN-compatible WebSocket #2
  mob-reality-ms  VLESS+Reality port 2083, SNI=microsoft.com (Cloudflare-compat port)
  mob-reality-ap  VLESS+Reality port 2087, SNI=apple.com    (Cloudflare-compat port)

Also enables ru-reality-1,ru-reality-2,ru-reality-3 in the subscription
(already configured in Xray but not yet included in MARZBAN_INBOUND_TAGS).

Usage:
  cd /opt/SmartKamaVPN && .venv/bin/python3 scripts/_add_mobile_inbounds.py
"""

import sys
import json
import sqlite3
import shutil
import subprocess
from pathlib import Path

import requests

sys.path.insert(0, "/opt/SmartKamaVPN")
import config  # noqa: E402 — must be after sys.path insert
import Utils.marzban_api as mapi  # noqa: E402

# ── Constants ────────────────────────────────────────────────────────────────

NGINX_443_CONF = "/etc/nginx/conf.d/smartkama-443.conf"
DB_PATH = "/opt/SmartKamaVPN/Database/smartkamavpn.db"

# TLS certificates (same as existing backup-grpc inbound)
CERT_FILE = "/var/lib/marzban/certs/sub.smartkama.ru/fullchain.pem"
KEY_FILE  = "/var/lib/marzban/certs/sub.smartkama.ru/privkey.pem"

# Reality keys — reuse nl-reality-1 keys (safe with different shortIds per inbound)
REALITY_PRIVATE_KEY = "YMvrrXDxs5nNuJObTdMFB8ttqWe8KGuUJJUyg7xfVF8"
REALITY_PUBLIC_KEY  = "xFtfS45HEb_mYNr1ETW3XTL2D_FdryWahS-KYTNIZGg"

# Fixed WS paths — hardcoded for idempotency (pre-generated random-looking strings)
WS1_PATH = "/api/m1-9e4a7f2c3b15"
WS2_PATH = "/api/m2-d6b3e8a5f912"

# ── New mobile inbound definitions ──────────────────────────────────────────

MOBILE_INBOUNDS = [
    # ── WebSocket via nginx port 443 (mob-ws-1) ─────────────────────────────
    # Port 443 is NEVER blocked by mobile operators (would break all HTTPS).
    # nginx terminates TLS and proxies plain WS to xray on 127.0.0.1:48001.
    # Client sees: wss://sub.smartkama.ru/api/m1-... (looks like normal HTTPS API)
    {
        "tag":      "mob-ws-1",
        "listen":   "127.0.0.1",
        "port":     48001,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "ws",
            "security": "none",
            "wsSettings": {
                "headers": {"Host": "sub.smartkama.ru"},
                "path": WS1_PATH,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ── WebSocket via nginx port 443 (mob-ws-2) ─────────────────────────────
    {
        "tag":      "mob-ws-2",
        "listen":   "127.0.0.1",
        "port":     48002,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "ws",
            "security": "none",
            "wsSettings": {
                "headers": {"Host": "sub.smartkama.ru"},
                "path": WS2_PATH,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ── Reality on port 2083 — Microsoft CDN SNI ─────────────────────────────
    # Port 2083 = Cloudflare panel port; allowed by virtually all Russian mobile operators.
    # SNI: www.microsoft.com — Azure CDN IPs are globally whitelisted; never blocked.
    # Reality mimics the exact TLS fingerprint of Microsoft's real HTTPS endpoint.
    {
        "tag":      "mob-reality-ms",
        "listen":   "0.0.0.0",
        "port":     2083,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "www.microsoft.com:443",
                "fingerprint": "chrome",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "www.microsoft.com",
                    "microsoft.com",
                    "login.microsoftonline.com",
                ],
                "shortIds": ["f3a1b9c2", "d4e6f1a8"],
                "show":     True,
                "xver":     0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ── Reality on port 2087 — Apple CDN SNI ────────────────────────────────
    # Port 2087 = Cloudflare WHM port; allowed by mobile operators.
    # SNI: www.apple.com — Apple CDN IPs never blocked in Russia.
    # fingerprint: safari — most convincing for Apple SNI.
    {
        "tag":      "mob-reality-ap",
        "listen":   "0.0.0.0",
        "port":     2087,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "www.apple.com:443",
                "fingerprint": "safari",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "www.apple.com",
                    "apple.com",
                    "icloud.com",
                ],
                "shortIds": ["a7b3c9d1", "e2f5a6b8"],
                "show":     True,
                "xver":     0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
]

# Tags to add/enable in subscription (new mobile + existing RU reality already in Xray)
NEW_MOBILE_TAGS = ["mob-ws-1", "mob-ws-2", "mob-reality-ms", "mob-reality-ap"]
RU_REALITY_TAGS = ["ru-reality-1", "ru-reality-2", "ru-reality-3"]
ALL_TAGS_TO_ENABLE = NEW_MOBILE_TAGS + RU_REALITY_TAGS


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_xray_config(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{config.MARZBAN_PANEL_URL}/api/core/config",
        headers=headers, timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _put_xray_config(token: str, cfg: dict) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.put(
        f"{config.MARZBAN_PANEL_URL}/api/core/config",
        headers=headers, json=cfg, timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _get_all_users(token: str) -> list:
    headers = {"Authorization": f"Bearer {token}"}
    users: list = []
    offset = 0
    limit = 100
    while True:
        resp = requests.get(
            f"{config.MARZBAN_PANEL_URL}/api/users",
            headers=headers,
            params={"offset": offset, "limit": limit},
            timeout=20,
        )
        resp.raise_for_status()
        page = resp.json()
        batch = page.get("users", [])
        users.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return users


def _update_user_inbounds(token: str, username: str, inbounds: dict) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    resp = requests.put(
        f"{config.MARZBAN_PANEL_URL}/api/user/{username}",
        headers=headers,
        json={"inbounds": inbounds},
        timeout=15,
    )
    resp.raise_for_status()


def _patch_nginx_conf(ws_inbounds_to_add: list) -> None:
    """Insert WS location blocks into nginx 443 conf."""
    backup = NGINX_443_CONF + ".bak.mobile"
    shutil.copy2(NGINX_443_CONF, backup)
    print(f"     nginx backup → {backup}")

    blocks = []
    for ib in ws_inbounds_to_add:
        ws_path = ib["streamSettings"]["wsSettings"]["path"]
        port    = ib["port"]
        tag     = ib["tag"]
        block = (
            f"\n    # Mobile WS [{tag}] — mobile operator bypass (CDN port 443)\n"
            f"    location {ws_path} {{\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header Upgrade $http_upgrade;\n"
            f"        proxy_set_header Connection \"upgrade\";\n"
            f"        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Real-IP $remote_addr;\n"
            f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            f"        proxy_read_timeout 86400s;\n"
            f"        proxy_send_timeout 86400s;\n"
            f"    }}"
        )
        blocks.append(block)

    conf = Path(NGINX_443_CONF).read_text()
    # Insert before the "location = /" block (fallback: before "location /")
    marker = "\n    location = / {"
    if marker not in conf:
        marker = "\n    location / {"
    new_conf = conf.replace(marker, "".join(blocks) + marker, 1)
    Path(NGINX_443_CONF).write_text(new_conf)
    print(f"     Inserted {len(ws_inbounds_to_add)} WS location block(s)")


def _update_db_tags(tags_to_add: list) -> None:
    """Append new tags to marzban_inbound_tags in str_config table."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM str_config WHERE key='marzban_inbound_tags'")
        row = cur.fetchone()
        current = (row[0] or "") if row else ""
        existing = [t.strip() for t in current.split(",") if t.strip()]
        combined = existing + [t for t in tags_to_add if t not in existing]
        new_value = ",".join(combined)
        cur.execute(
            "UPDATE str_config SET value=? WHERE key='marzban_inbound_tags'",
            (new_value,),
        )
        conn.commit()
        print(f"     marzban_inbound_tags → {new_value}")
    finally:
        conn.close()


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 62)
    print("SmartKamaVPN — Mobile Operator Inbounds Setup")
    print("=" * 62)

    # ─ Auth ──────────────────────────────────────────────────────────────────
    token = mapi._get_access_token()
    print("[1] Marzban auth: OK")

    # ─ Step 1: Load current Xray config ──────────────────────────────────────
    cfg = _get_xray_config(token)
    existing_tags = {ib["tag"] for ib in cfg.get("inbounds", [])}
    print(f"[2] Existing inbounds ({len(existing_tags)}): {sorted(existing_tags)}")

    # ─ Step 2: Add new mobile inbounds to Xray ───────────────────────────────
    to_add = [ib for ib in MOBILE_INBOUNDS if ib["tag"] not in existing_tags]
    if not to_add:
        print("[3] All mobile inbounds already exist — Xray config unchanged")
    else:
        cfg["inbounds"] = cfg.get("inbounds", []) + to_add
        _put_xray_config(token, cfg)
        print(f"[3] Xray updated — added: {[ib['tag'] for ib in to_add]}")

    # ─ Step 3: Patch nginx for WS inbounds ───────────────────────────────────
    existing_nginx = Path(NGINX_443_CONF).read_text()
    ws_to_add = [
        ib for ib in MOBILE_INBOUNDS
        if ib["streamSettings"]["network"] == "ws"
        and ib["streamSettings"]["wsSettings"]["path"] not in existing_nginx
    ]
    if ws_to_add:
        _patch_nginx_conf(ws_to_add)
        result = subprocess.run(
            ["/usr/sbin/nginx", "-t"], capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"[4] ERROR: nginx config test failed:\n{result.stderr}")
            print("     Restoring backup...")
            shutil.copy2(NGINX_443_CONF + ".bak.mobile", NGINX_443_CONF)
            return
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
        print("[4] nginx config patched and reloaded OK")
    else:
        print("[4] WS nginx routes already present — no change")

    # ─ Step 4: Update DB inbound tags ────────────────────────────────────────
    print("[5] Updating DB marzban_inbound_tags...")
    _update_db_tags(ALL_TAGS_TO_ENABLE)

    # ─ Step 5: Update existing active users in Marzban ───────────────────────
    users = _get_all_users(token)
    active = [u for u in users if u.get("status") in ("active", "on_hold")]
    print(f"[6] Updating {len(active)} active/on_hold users in Marzban...")

    updated = 0
    errors = 0
    for user in active:
        username = user.get("username", "")
        cur_inbounds = user.get("inbounds") or {}
        if not isinstance(cur_inbounds, dict):
            cur_inbounds = {}

        vless_tags = list(cur_inbounds.get("vless", []))
        needs_update = False
        for tag in ALL_TAGS_TO_ENABLE:
            if tag not in vless_tags:
                vless_tags.append(tag)
                needs_update = True

        if needs_update:
            new_inbounds = {**cur_inbounds, "vless": vless_tags}
            try:
                _update_user_inbounds(token, username, new_inbounds)
                updated += 1
                if updated % 20 == 0 or updated <= 3:
                    print(f"     [{updated}/{len(active)}] {username}")
            except Exception as exc:
                print(f"     ERROR {username}: {exc}")
                errors += 1

    print(f"[6] Users: {updated} updated, {errors} errors, "
          f"{len(active) - updated - errors} already had all tags")

    # ─ Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 62)
    print("COMPLETE. The following inbounds are now active:")
    print()
    print("  Mobile (new):")
    for tag in NEW_MOBILE_TAGS:
        print(f"    ✓ {tag}")
    print()
    print("  RU-Reality (now enabled in subscription):")
    for tag in RU_REALITY_TAGS:
        print(f"    ✓ {tag}")
    print()
    print("  Restart the bot to apply updated MARZBAN_INBOUND_TAGS:")
    print("    systemctl restart smartkamavpn-userbot")
    print("=" * 62)


if __name__ == "__main__":
    main()
