#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SERVICE_PATH = Path("/etc/systemd/system/smartkama-autotune.service")
TIMER_PATH = Path("/etc/systemd/system/smartkama-autotune.timer")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install SmartKama autotune systemd timer")
    p.add_argument("--base-dir", default="/opt/SmartKamaVPN")
    p.add_argument("--python-bin", default="/opt/SmartKamaVPN/.venv/bin/python")
    p.add_argument("--on-calendar", default="*-*-* 04,16:00:00")
    p.add_argument("--randomized-delay-sec", default="15m")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    service_content = f"""[Unit]
Description=SmartKama Autotune Stack
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory={args.base_dir}
ExecStart={args.python_bin} {args.base_dir}/scripts/server_autotune_stack.py --full
Nice=5
IOSchedulingClass=best-effort
IOSchedulingPriority=7

[Install]
WantedBy=multi-user.target
"""

    timer_content = f"""[Unit]
Description=Run SmartKama Autotune periodically

[Timer]
OnCalendar={args.on_calendar}
Persistent=true
RandomizedDelaySec={args.randomized_delay_sec}
Unit=smartkama-autotune.service

[Install]
WantedBy=timers.target
"""

    print("[autotune-timer] service path:", SERVICE_PATH)
    print("[autotune-timer] timer path:", TIMER_PATH)
    print("[autotune-timer] schedule:", args.on_calendar)

    if args.dry_run:
        print("\n--- service ---\n")
        print(service_content)
        print("\n--- timer ---\n")
        print(timer_content)
        return 0

    SERVICE_PATH.write_text(service_content, encoding="utf-8")
    TIMER_PATH.write_text(timer_content, encoding="utf-8")

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "smartkama-autotune.timer"])
    run(["systemctl", "restart", "smartkama-autotune.timer"])

    status = run(["systemctl", "status", "smartkama-autotune.timer", "--no-pager"], check=False)
    print(status.stdout)
    if status.stderr:
        print(status.stderr)

    next_run = run(["systemctl", "list-timers", "smartkama-autotune.timer", "--no-pager"], check=False)
    print(next_run.stdout)

    return 0


if __name__ == "__main__":
    sys.exit(main())
