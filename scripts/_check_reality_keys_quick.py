#!/usr/bin/env python3
"""Quick check of REALITY inbound keys in Marzban xray config."""
import json, subprocess

CONFIG_PATH = "/var/lib/marzban/xray_config.json"
XRAY_BIN = "/var/lib/marzban/xray-new"

with open(CONFIG_PATH) as f:
    config = json.load(f)

for inbound in config.get("inbounds", []):
    ss = inbound.get("streamSettings", {})
    if "realitySettings" not in ss:
        continue
    rs = ss["realitySettings"]
    tag = inbound.get("tag", "unknown")
    pk = rs.get("privateKey", "")
    if not pk:
        print(f"  BROKEN: {tag} - NO privateKey!")
        continue
    # Try to derive public key from private key
    try:
        result = subprocess.run(
            [XRAY_BIN, "x25519", "-i", pk],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            print(f"  BROKEN: {tag} - x25519 failed: {result.stderr.strip()}")
        else:
            lines = result.stdout.strip().split("\n")
            pub = None
            for line in lines:
                if "public" in line.lower():
                    pub = line.split(":")[-1].strip()
                    break
            if pub:
                print(f"  OK: {tag} - pubkey={pub[:16]}...")
            else:
                print(f"  BROKEN: {tag} - no public key in output: {result.stdout.strip()}")
    except Exception as e:
        print(f"  BROKEN: {tag} - error: {e}")
