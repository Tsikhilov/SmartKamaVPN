#!/usr/bin/env python3
"""
SmartKamaVPN — добавление прямого VLESS TCP inbound без маскировки.

Этот inbound работает напрямую (VLESS + TCP + TLS), без Reality/WS/gRPC overlay.
Обеспечивает максимальную скорость при прямом подключении.

Для мобильных операторов использует фрагментацию TLS ClientHello.

Запуск на сервере:
    python3 scripts/server_add_direct_inbound.py [--port PORT] [--remark REMARK]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

XUI_DB = "/etc/x-ui/x-ui.db"
DEFAULT_PORT = 8443
DEFAULT_REMARK = "DIRECT-SPEED"
CERT_FILE = "/etc/letsencrypt/live/sub.smartkama.ru/fullchain.pem"
KEY_FILE = "/etc/letsencrypt/live/sub.smartkama.ru/privkey.pem"
SNI_HOST = "sub.smartkama.ru"


def log(*parts):
    print("[direct-inbound]", *parts)


def get_max_inbound_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(id) FROM inbounds").fetchone()
    return int(row[0]) if row and row[0] else 0


def inbound_exists(conn: sqlite3.Connection, port: int) -> bool:
    row = conn.execute("SELECT id FROM inbounds WHERE port=?", (port,)).fetchone()
    return row is not None


def build_stream_settings() -> str:
    """VLESS + TCP + TLS — прямое подключение без маскировки."""
    return json.dumps({
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {
            "serverName": SNI_HOST,
            "minVersion": "1.2",
            "maxVersion": "1.3",
            "cipherSuites": "",
            "certificates": [{
                "certificateFile": CERT_FILE,
                "keyFile": KEY_FILE,
            }],
            "alpn": ["h2", "http/1.1"],
            "settings": {
                "allowInsecure": False,
                "fingerprint": "chrome",
            },
        },
        "tcpSettings": {
            "acceptProxyProtocol": False,
            "header": {"type": "none"},
        },
    }, ensure_ascii=False)


def build_settings(tag: str) -> str:
    return json.dumps({
        "clients": [],
        "decryption": "none",
        "fallbacks": [],
    }, ensure_ascii=False)


def build_sniffing() -> str:
    return json.dumps({
        "enabled": True,
        "destOverride": ["http", "tls", "quic", "fakedns"],
        "metadataOnly": False,
        "routeOnly": False,
    }, ensure_ascii=False)


def build_allocate() -> str:
    return json.dumps({
        "strategy": "always",
        "refresh": 5,
        "concurrency": 3,
    }, ensure_ascii=False)


def add_direct_inbound(db_path: str, port: int, remark: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        if inbound_exists(conn, port):
            log(f"inbound on port {port} already exists, skipping")
            return 0

        new_id = get_max_inbound_id(conn) + 1
        tag = f"inbound-{new_id}"
        stream = build_stream_settings()
        settings = build_settings(tag)
        sniffing = build_sniffing()
        allocate = build_allocate()

        conn.execute(
            """INSERT INTO inbounds (
                id, user_id, up, down, total, remark, enable,
                expiry_time, listen, port, protocol,
                settings, stream_settings, tag, sniffing, allocate
            ) VALUES (?, 1, 0, 0, 0, ?, 1, 0, '', ?, 'vless', ?, ?, ?, ?, ?)""",
            (new_id, remark, port, settings, stream, tag, sniffing, allocate),
        )
        conn.commit()
        log(f"created DIRECT VLESS TCP+TLS inbound id={new_id} port={port} remark={remark}")
        return new_id
    finally:
        conn.close()


def restart_xray():
    log("restarting xray via x-ui")
    proc = subprocess.run(["x-ui", "restart-xray"], capture_output=True, text=True)
    if proc.returncode != 0:
        log("WARNING: restart-xray failed:", proc.stderr)
    else:
        log("xray restarted successfully")


def main():
    parser = argparse.ArgumentParser(description="Add direct VLESS TCP+TLS inbound for max speed")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--remark", default=DEFAULT_REMARK, help=f"Remark (default: {DEFAULT_REMARK})")
    parser.add_argument("--db", default=XUI_DB, help=f"x-ui DB path (default: {XUI_DB})")
    parser.add_argument("--no-restart", action="store_true", help="Don't restart xray after adding")
    args = parser.parse_args()

    new_id = add_direct_inbound(args.db, args.port, args.remark)
    if new_id > 0 and not args.no_restart:
        restart_xray()

    log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
