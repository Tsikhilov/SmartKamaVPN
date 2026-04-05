#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional

import requests


DEFAULT_BOT_DB = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
TG_API = "https://api.telegram.org"


def _read_latest(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute(
        "SELECT value FROM str_config WHERE key=? ORDER BY rowid DESC LIMIT 1",
        (key,),
    ).fetchone()
    return str(row[0]).strip() if row and row[0] else ""


def _parse_chat_id(raw: str) -> Optional[int]:
    text = (raw or "").strip()
    if not text:
        return None

    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list) and data:
                return int(data[0])
            return None
        except Exception:
            return None

    first = text.split(",", 1)[0].strip()
    if not first:
        return None
    try:
        return int(first)
    except Exception:
        return None


def _api_get_me(token: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        resp = requests.get(f"{TG_API}/bot{token}/getMe", timeout=timeout)
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            username = (data.get("result") or {}).get("username") or "unknown"
            return True, f"username={username}"
        return False, f"http={resp.status_code} desc={data.get('description', 'unknown')}"
    except Exception as exc:
        return False, str(exc)


def _api_send_message(token: str, chat_id: int, text: str, timeout: int = 15) -> tuple[bool, str]:
    try:
        resp = requests.post(
            f"{TG_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=timeout,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code == 200 and data.get("ok"):
            msg_id = (data.get("result") or {}).get("message_id")
            return True, f"message_id={msg_id}"
        return False, f"http={resp.status_code} desc={data.get('description', 'unknown')}"
    except Exception as exc:
        return False, str(exc)


def _check_bot(label: str, token: str, chat_id: int, send_test: bool) -> bool:
    ok_me, info_me = _api_get_me(token)
    if ok_me:
        print(f"[{label}] getMe OK ({info_me})")
    else:
        print(f"[{label}] getMe FAIL ({info_me})")
        return False

    if not send_test:
        return True

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = f"SmartKama Telegram self-check ({label}) at {ts}"
    ok_send, info_send = _api_send_message(token, chat_id, text)
    if ok_send:
        print(f"[{label}] sendMessage OK ({info_send})")
        return True

    print(f"[{label}] sendMessage FAIL ({info_send})")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram API self-check for SmartKama bots")
    parser.add_argument("--bot-db", default=DEFAULT_BOT_DB)
    parser.add_argument("--check-client", action="store_true")
    parser.add_argument("--send-test-message", action="store_true", default=True)
    parser.add_argument("--no-send-test-message", action="store_false", dest="send_test_message")
    parser.add_argument("--strict-client", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    conn = sqlite3.connect(args.bot_db)
    try:
        admin_token = _read_latest(conn, "bot_token_admin")
        client_token = _read_latest(conn, "bot_token_client")
        admin_raw = _read_latest(conn, "bot_admin_id")
    finally:
        conn.close()

    admin_chat_id = _parse_chat_id(admin_raw)
    if not admin_token:
        print("[admin] token missing in str_config.bot_token_admin")
        return 1
    if admin_chat_id is None:
        print("[admin] chat_id missing or invalid in str_config.bot_admin_id")
        return 1

    admin_ok = _check_bot("admin", admin_token, admin_chat_id, args.send_test_message)
    if not admin_ok:
        return 1

    if not args.check_client:
        print("[client] skipped")
        return 0

    if not client_token:
        msg = "[client] token missing in str_config.bot_token_client"
        if args.strict_client:
            print(msg)
            return 1
        print(msg + " (warning)")
        return 0

    client_ok = _check_bot("client", client_token, admin_chat_id, args.send_test_message)
    if client_ok:
        return 0

    return 1 if args.strict_client else 0


if __name__ == "__main__":
    sys.exit(main())
