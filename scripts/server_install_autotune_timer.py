#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SERVICE_PATH = Path("/etc/systemd/system/smartkama-autotune.service")
TIMER_PATH = Path("/etc/systemd/system/smartkama-autotune.timer")
ENV_PATH = Path("/etc/default/smartkama-autotune")


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install SmartKama autotune systemd timer")
    p.add_argument("--base-dir", default="/opt/SmartKamaVPN")
    p.add_argument("--python-bin", default="/opt/SmartKamaVPN/.venv/bin/python")
    p.add_argument("--guard-mode", choices=["diagnose", "autofix", "smoke", "all"], default="smoke")
    p.add_argument("--schedule-mode", choices=["calendar", "interval"], default="interval")
    p.add_argument("--on-calendar", default="*-*-* 04,16:00:00")
    p.add_argument("--on-boot-sec", default="10m")
    p.add_argument("--on-unit-active-sec", default="30m")
    p.add_argument("--randomized-delay-sec", default="15m")
    p.add_argument("--runtime-max-sec", default="20m")
    p.add_argument("--lock-file", default="/run/smartkama-autotune.lock")
    p.add_argument("--env-file", default=str(ENV_PATH))
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    exec_start = (
        f"/usr/bin/flock -n {args.lock_file} "
        f"{args.python_bin} {args.base_dir}/scripts/server_autotune_stack.py --full --guard-mode {args.guard_mode}"
    )

    service_content = f"""[Unit]
Description=SmartKama Autotune Stack
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory={args.base_dir}
EnvironmentFile=-{args.env_file}
ExecStart={exec_start}
TimeoutStartSec={args.runtime_max_sec}
Nice=5
IOSchedulingClass=best-effort
IOSchedulingPriority=7
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

    if args.schedule_mode == "interval":
        timer_body = (
            f"OnBootSec={args.on_boot_sec}\n"
            f"OnUnitActiveSec={args.on_unit_active_sec}\n"
        )
        schedule_summary = f"interval: boot+{args.on_boot_sec}, every {args.on_unit_active_sec}"
    else:
        timer_body = f"OnCalendar={args.on_calendar}\n"
        schedule_summary = f"calendar: {args.on_calendar}"

    timer_content = f"""[Unit]
Description=Run SmartKama Autotune periodically

[Timer]
{timer_body}Persistent=true
RandomizedDelaySec={args.randomized_delay_sec}
Unit=smartkama-autotune.service

[Install]
WantedBy=timers.target
"""

    print("[autotune-timer] service path:", SERVICE_PATH)
    print("[autotune-timer] timer path:", TIMER_PATH)
    print("[autotune-timer] env path:", args.env_file)
    print("[autotune-timer] guard mode:", args.guard_mode)
    print("[autotune-timer] schedule:", schedule_summary)

    if args.dry_run:
        print("\n--- service ---\n")
        print(service_content)
        print("\n--- timer ---\n")
        print(timer_content)
        return 0

    env_lines = [
        f"SMARTKAMA_AUTOTUNE_GUARD_MODE={args.guard_mode}",
        f"SMARTKAMA_AUTOTUNE_SCHEDULE_MODE={args.schedule_mode}",
    ]

    SERVICE_PATH.write_text(service_content, encoding="utf-8")
    TIMER_PATH.write_text(timer_content, encoding="utf-8")
    Path(args.env_file).write_text("\n".join(env_lines) + "\n", encoding="utf-8")

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
