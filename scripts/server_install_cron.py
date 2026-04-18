#!/usr/bin/env python3
"""Install crontab entries for SmartKamaVPN periodic jobs.

Usage:
    python3 scripts/server_install_cron.py --install   # set up all cron entries
    python3 scripts/server_install_cron.py --uninstall # remove SmartKamaVPN entries
    python3 scripts/server_install_cron.py --show      # display current crontab
"""
from __future__ import annotations

import argparse
import subprocess
import sys

PYTHON_BIN = "/opt/SmartKamaVPN/.venv/bin/python"
CRONTAB_PY = "/opt/SmartKamaVPN/crontab.py"
LOG_DIR = "/opt/SmartKamaVPN/Logs"

CRON_TAG = "# SmartKamaVPN-cron"

DEFAULT_SCHEDULES: dict[str, tuple[str, str]] = {
    # (cron expression, crontab.py flag)
    "backup":        ("0 3 * * *",       "--backup"),
    "backup-bot":    ("30 3 * * *",      "--backup-bot"),
    "reminder":      ("0 9,21 * * *",    "--reminder"),
    "anomaly":       ("*/30 * * * *",    "--anomaly"),
    "cleanup":       ("0 4 * * *",       "--cleanup"),
    "payment-check": ("*/5 * * * *",     "--payment-check"),
    "status-channel": ("7 * * * *",      "--status-channel"),
}


def _get_current_crontab() -> str:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else ""


def install() -> None:
    current = _get_current_crontab()
    lines = [line for line in current.splitlines() if CRON_TAG not in line]

    for job_name, (schedule, flag) in DEFAULT_SCHEDULES.items():
        log_file = f"{LOG_DIR}/cron_{job_name.replace('-', '_')}.log"
        entry = f"{schedule} {PYTHON_BIN} {CRONTAB_PY} {flag} >> {log_file} 2>&1 {CRON_TAG}"
        lines.append(entry)

    new_crontab = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    if proc.returncode == 0:
        print("[cron] Installed successfully:")
        subprocess.run(["crontab", "-l"])
    else:
        print("[cron] Failed to install", file=sys.stderr)
        sys.exit(1)


def uninstall() -> None:
    current = _get_current_crontab()
    lines = [line for line in current.splitlines() if CRON_TAG not in line]
    new_crontab = "\n".join(lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    print("[cron] Removed all SmartKamaVPN entries")


def show() -> None:
    print(_get_current_crontab() or "(empty crontab)")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="SmartKamaVPN cron installer")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="Install cron entries")
    group.add_argument("--uninstall", action="store_true", help="Remove cron entries")
    group.add_argument("--show", action="store_true", help="Show current crontab")
    args = p.parse_args()

    if args.install:
        install()
    elif args.uninstall:
        uninstall()
    else:
        show()
