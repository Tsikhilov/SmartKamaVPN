#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def build_sysctl_content() -> str:
    lines = ["# Managed by SmartKama latency tuner"]
    for key, value in TUNING.items():
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def write_sysctl_file() -> bool:
    content = build_sysctl_content()
    current = None
    try:
        with open(SYSCTL_FILE, "r", encoding="utf-8") as f:
            current = f.read()
    except FileNotFoundError:
        current = None

    if current == content:
        return False

    with open(SYSCTL_FILE, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def runtime_matches() -> bool:
    for key, expected in TUNING.items():
        code, out, _ = run(["sysctl", "-n", key])
        if code != 0 or out.strip() != expected:
            return False
    return True


def apply_runtime() -> None:
    for key, value in TUNING.items():
        code, out, err = run(["sysctl", "-w", f"{key}={value}"])
        if code == 0:
            print("set", f"{key}={value}")
        else:
            print("warn", f"{key}={value}", err or out)


def collect_state() -> dict[str, str]:
    state: dict[str, str] = {}
    for key in [
        "net.core.default_qdisc",
        "net.ipv4.tcp_congestion_control",
        "net.ipv4.tcp_fastopen",
        "net.ipv4.tcp_mtu_probing",
    ]:
        code, out, err = run(["sysctl", "-n", key])
        if code == 0:
            state[key] = out
        else:
            state[key] = f"unavailable:{err}"
    return state



def main() -> int:
    parser = argparse.ArgumentParser(description="SmartKama network latency tuner")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid) and geteuid() != 0:
        print("error: run as root", file=sys.stderr)
        return 1

    file_changed = write_sysctl_file()
    if file_changed:
        print("wrote", SYSCTL_FILE)
    else:
        print("already up-to-date", SYSCTL_FILE)

    runtime_ok = runtime_matches()
    if file_changed:
        code, out, err = run(["sysctl", "--system"])
        if code != 0:
            print("warn: sysctl --system failed", err or out)
        runtime_ok = runtime_matches()

    if not runtime_ok:
        apply_runtime()
    else:
        print("runtime already up-to-date")

    state = collect_state()

    code, out, err = run(["sysctl", "-n", "net.ipv4.tcp_available_congestion_control"])
    if code == 0:
        available_cc = out
    else:
        available_cc = err

    if args.verbose:
        for key, value in state.items():
            print("state", key, value)
        print("available_cc", available_cc)
    else:
        print(
            "network_state",
            f"qdisc={state.get('net.core.default_qdisc', '')}",
            f"cc={state.get('net.ipv4.tcp_congestion_control', '')}",
            f"fastopen={state.get('net.ipv4.tcp_fastopen', '')}",
            f"mtu_probe={state.get('net.ipv4.tcp_mtu_probing', '')}",
            f"available_cc={available_cc}",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
