#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys

SYSCTL_FILE = "/etc/sysctl.d/99-smartkama-latency.conf"

TUNING = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
    "net.ipv4.tcp_fastopen": "3",
    "net.ipv4.tcp_mtu_probing": "1",
}


def run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def write_sysctl_file() -> None:
    lines = ["# Managed by SmartKama latency tuner"]
    for key, value in TUNING.items():
        lines.append(f"{key}={value}")
    content = "\n".join(lines) + "\n"
    with open(SYSCTL_FILE, "w", encoding="utf-8") as f:
        f.write(content)


def apply_runtime() -> None:
    for key, value in TUNING.items():
        code, out, err = run(["sysctl", "-w", f"{key}={value}"])
        if code == 0:
            print("set", f"{key}={value}")
        else:
            print("warn", f"{key}={value}", err or out)


def show_state() -> None:
    for key in [
        "net.core.default_qdisc",
        "net.ipv4.tcp_congestion_control",
        "net.ipv4.tcp_fastopen",
        "net.ipv4.tcp_mtu_probing",
    ]:
        code, out, err = run(["sysctl", "-n", key])
        if code == 0:
            print("state", key, out)
        else:
            print("state", key, "unavailable", err)



def main() -> int:
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid) and geteuid() != 0:
        print("error: run as root", file=sys.stderr)
        return 1

    write_sysctl_file()
    print("wrote", SYSCTL_FILE)

    code, out, err = run(["sysctl", "--system"])
    if code != 0:
        print("warn: sysctl --system failed", err or out)

    apply_runtime()
    show_state()

    code, out, err = run(["sysctl", "-n", "net.ipv4.tcp_available_congestion_control"])
    if code == 0:
        print("available_cc", out)
    else:
        print("available_cc", err)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
