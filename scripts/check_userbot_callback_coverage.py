#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


IGNORED_CALLBACK_PREFIXES = {
    # These callbacks are routed to AdminBot from user-side notifications.
    "bot_user_info",
    "confirm_payment_by_admin",
    "cancel_payment_by_admin",
    "send_message_by_admin",
    "users_bot_send_message_by_admin",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_markup_callback_keys(markups_text: str) -> set[str]:
    keys: set[str] = set()
    for match in re.finditer(r"callback_data\s*=\s*f?(['\"])(.*?)\1", markups_text, flags=re.DOTALL):
        token = (match.group(2) or "").strip()
        if not token:
            continue
        if "{" in token:
            token = token.split("{", 1)[0]
        token = token.split(":", 1)[0].strip()
        if not token:
            continue
        if token in {"None", "del_msg", "back", "cancel"}:
            continue
        if token in IGNORED_CALLBACK_PREFIXES:
            continue
        keys.add(token)
    return keys


def _extract_bot_handled_keys(bot_text: str) -> set[str]:
    handled: set[str] = set()

    for match in re.finditer(r"\bkey\s*==\s*(['\"])([^'\"]+)\1", bot_text):
        handled.add(match.group(2))

    for match in re.finditer(r"\bkey\s+in\s*\(([^\)]*)\)", bot_text):
        chunk = match.group(1)
        for val in re.findall(r"(['\"])([^'\"]+)\1", chunk):
            handled.add(val[1])

    # Also catch tuple literals in forms like: key in ("a", 'b') split across lines.
    for match in re.finditer(r"\bkey\s+in\s*\((.*?)\)", bot_text, flags=re.DOTALL):
        chunk = match.group(1)
        for val in re.findall(r"(['\"])([^'\"]+)\1", chunk):
            handled.add(val[1])

    # Keep backward compatibility for simple double-quoted matches.
    for match in re.finditer(r'\bkey\s*==\s*"([^"]+)"', bot_text):
        handled.add(match.group(1))

    return handled


def _normalize_to_runtime_key(markup_key: str) -> str:
    key = markup_key.strip()
    if key.startswith("smartkamavpn_"):
        return key  # already smartkamavpn_ prefix
    return key


def main() -> int:
    parser = argparse.ArgumentParser(description="Check UserBot callback coverage")
    parser.add_argument("--markups", default="UserBot/markups.py")
    parser.add_argument("--bot", default="UserBot/bot.py")
    args = parser.parse_args()

    markups_path = Path(args.markups)
    bot_path = Path(args.bot)

    markups_text = _read_text(markups_path)
    bot_text = _read_text(bot_path)

    markup_keys = _extract_markup_callback_keys(markups_text)
    handled_keys = _extract_bot_handled_keys(bot_text)

    missing: list[str] = []
    for key in sorted(markup_keys):
        runtime_key = _normalize_to_runtime_key(key)
        if runtime_key not in handled_keys:
            missing.append(f"{key} -> {runtime_key}")

    print(f"buttons_total={len(markup_keys)}")
    print(f"handlers_total={len(handled_keys)}")
    if missing:
        print("missing_handlers:")
        for item in missing:
            print(f"  - {item}")
        return 1

    print("callback_coverage=OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
