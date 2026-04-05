#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Dict

BOT_DB_DEFAULT = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
ENV_FILE_DEFAULT = "/opt/SmartKamaVPN/.env"


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value

    return values


def _upsert(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO str_config(key, value) VALUES(?, ?)",
        (key, value),
    )


def _resolve_field(
    cli_value: str | None,
    current: Dict[str, str],
    current_key: str,
    env_key: str,
    env_file_values: Dict[str, str],
) -> str:
    if cli_value is not None:
        return str(cli_value).strip()
    current_value = str(current.get(current_key) or "").strip()
    if current_value:
        return current_value
    env_value = str(os.getenv(env_key, "") or "").strip()
    if env_value:
        return env_value
    return str(env_file_values.get(env_key) or "").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set active panel provider in SmartKama bot DB")
    parser.add_argument("--bot-db", default=BOT_DB_DEFAULT)
    parser.add_argument("--env-file", default=ENV_FILE_DEFAULT)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--provider", choices=["3xui", "marzban"], default=None)

    parser.add_argument("--marzban-panel-url", default=None)
    parser.add_argument("--marzban-username", default=None)
    parser.add_argument("--marzban-password", default=None)
    parser.add_argument("--marzban-access-token", default=None)
    parser.add_argument("--marzban-tls-verify", choices=["true", "false"], default=None)
    parser.add_argument("--marzban-inbound-tags", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.bot_db)
    env_file_values = _read_env_file(Path(args.env_file))
    if not db_path.exists():
        print(f"ERROR: bot db not found: {db_path}")
        return 1

    if not args.show and not args.provider:
        print("ERROR: --provider is required unless --show is used")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS str_config (key TEXT PRIMARY KEY, value TEXT)")
        rows = conn.execute(
            """
            SELECT key, value
            FROM str_config
            WHERE key IN (
              'panel_provider',
              'marzban_panel_url',
              'marzban_username',
              'marzban_password',
              'marzban_access_token',
              'marzban_tls_verify',
              'marzban_inbound_tags'
            )
            """
        ).fetchall()
        current: Dict[str, str] = {str(k): str(v or "") for k, v in rows}

        if args.show:
            provider_now = str(current.get("panel_provider") or "3xui").strip().lower() or "3xui"
            print("PANEL_PROVIDER_STATUS")
            print(f"provider={provider_now}")
            for key in sorted(
                [
                    "marzban_panel_url",
                    "marzban_username",
                    "marzban_password",
                    "marzban_access_token",
                    "marzban_tls_verify",
                    "marzban_inbound_tags",
                ]
            ):
                value = current.get(key, "")
                if "password" in key or "token" in key:
                    safe = "***" if value else ""
                else:
                    safe = value
                print(f"{key}={safe}")
            return 0

        provider = str(args.provider).strip().lower()

        updates: Dict[str, str] = {
            "panel_provider": provider,
        }

        if provider == "marzban":
            resolved_panel_url = _resolve_field(
                args.marzban_panel_url,
                current,
                "marzban_panel_url",
                "MARZBAN_PANEL_URL",
                env_file_values,
            )
            if not resolved_panel_url:
                print("ERROR: marzban_panel_url is empty (pass --marzban-panel-url or set MARZBAN_PANEL_URL env)")
                return 1

            resolved_username = _resolve_field(
                args.marzban_username,
                current,
                "marzban_username",
                "MARZBAN_USERNAME",
                env_file_values,
            )
            resolved_password = _resolve_field(
                args.marzban_password,
                current,
                "marzban_password",
                "MARZBAN_PASSWORD",
                env_file_values,
            )
            resolved_access_token = _resolve_field(
                args.marzban_access_token,
                current,
                "marzban_access_token",
                "MARZBAN_ACCESS_TOKEN",
                env_file_values,
            )
            resolved_tls_verify = (
                str(args.marzban_tls_verify).strip().lower()
                if args.marzban_tls_verify is not None
                else (
                    str(current.get("marzban_tls_verify") or "").strip().lower()
                    or str(os.getenv("MARZBAN_TLS_VERIFY", "") or "").strip().lower()
                    or str(env_file_values.get("MARZBAN_TLS_VERIFY") or "false").strip().lower()
                )
            )
            if resolved_tls_verify not in ("true", "false"):
                resolved_tls_verify = "false"
            resolved_inbound_tags = _resolve_field(
                args.marzban_inbound_tags,
                current,
                "marzban_inbound_tags",
                "MARZBAN_INBOUND_TAGS",
                env_file_values,
            )

            updates.update(
                {
                    "marzban_panel_url": resolved_panel_url,
                    "marzban_username": resolved_username,
                    "marzban_password": resolved_password,
                    "marzban_access_token": resolved_access_token,
                    "marzban_tls_verify": resolved_tls_verify,
                    "marzban_inbound_tags": resolved_inbound_tags,
                }
            )

        for key, value in updates.items():
            _upsert(conn, key, value)
        conn.commit()
    finally:
        conn.close()

    print("PANEL_PROVIDER_SET_OK")
    print(f"provider={provider}")
    for key in sorted(updates.keys()):
        value = updates[key]
        if "password" in key or "token" in key:
            masked = "***" if value else ""
            print(f"{key}={masked}")
        else:
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
