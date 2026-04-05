#!/usr/bin/env python3
"""Write sing-box 1.13.x configs and validate them."""
import json, subprocess, sys

hy2 = {
    "log": {"level": "info", "timestamp": True, "output": "/var/log/singbox-hy2.log"},
    "inbounds": [{
        "type": "hysteria2",
        "tag": "nl-hy2",
        "listen": "0.0.0.0",
        "listen_port": 8443,
        "obfs": {"type": "salamander", "password": "SmKm_Obs2026_s@l4m4nd3r!"},
        "users": [],
        "tls": {
            "enabled": True,
            "certificate_path": "/var/lib/marzban/certs/sub.smartkama.ru/fullchain.pem",
            "key_path": "/var/lib/marzban/certs/sub.smartkama.ru/privkey.pem",
            "alpn": ["h3"]
        }
    }],
    "outbounds": [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"}
    ],
    "route": {"final": "direct"}
}

tuic = {
    "log": {"level": "info", "timestamp": True, "output": "/var/log/singbox-tuic.log"},
    "inbounds": [{
        "type": "tuic",
        "tag": "nl-tuic5",
        "listen": "0.0.0.0",
        "listen_port": 9445,
        "users": [],
        "congestion_control": "bbr",
        "auth_timeout": "3s",
        "zero_rtt_handshake": False,
        "heartbeat": "10s",
        "tls": {
            "enabled": True,
            "certificate_path": "/var/lib/marzban/certs/sub.smartkama.ru/fullchain.pem",
            "key_path": "/var/lib/marzban/certs/sub.smartkama.ru/privkey.pem",
            "alpn": ["h3"]
        }
    }],
    "outbounds": [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"}
    ],
    "route": {"final": "direct"}
}

import os
os.makedirs("/opt/singbox", exist_ok=True)

for name, cfg in [("hy2-server.json", hy2), ("tuic5-server.json", tuic)]:
    path = f"/opt/singbox/{name}"
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
    r = subprocess.run(["sing-box", "check", "-c", path], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"[OK]  {name} valid")
    else:
        print(f"[ERR] {name}: {r.stderr.strip()}")
        sys.exit(1)

print("All configs OK")
