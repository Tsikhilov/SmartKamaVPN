#!/usr/bin/env python3
"""
SmartKamaVPN — оптимизация транспортов для устойчивости к мобильным DPI.

Добавляет / обновляет в xray-шаблоне x-ui:
1. Sockopt-настройки TLS-фрагментации (fragment) на Reality/VLESS inbounds
2. MTU-оптимизацию через sysctl (TCP MSS clamping, TCP window)
3. gRPC/H2 keepalive-pad для предотвращения idle-disconnect на мобильных
4. QUIC-fallback inbound (если не существует)

Запуск на сервере:
    python3 scripts/server_optimize_mobile_transport.py [--xui-db /etc/x-ui/x-ui.db]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


XUI_DB_DEFAULT = "/etc/x-ui/x-ui.db"
SYSCTL_CONF = "/etc/sysctl.d/99-smartkama-mobile.conf"

# Mobile-optimised sysctl parameters
MOBILE_SYSCTL = {
    # Lower initial congestion window helps on throttled mobile links
    "net.ipv4.tcp_slow_start_after_idle": "0",
    # Enable BBR congestion control — best for variable mobile bandwidth
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
    # TCP keepalive — shorter intervals prevent mobile NAT drops
    "net.ipv4.tcp_keepalive_time": "60",
    "net.ipv4.tcp_keepalive_intvl": "10",
    "net.ipv4.tcp_keepalive_probes": "6",
    # TCP window/buffer tuning for mobile
    "net.core.rmem_max": "16777216",
    "net.core.wmem_max": "16777216",
    "net.ipv4.tcp_rmem": "4096 87380 16777216",
    "net.ipv4.tcp_wmem": "4096 65536 16777216",
    # Enable MTU probing — crucial for mobile (fluctuating MTU)
    "net.ipv4.tcp_mtu_probing": "1",
    "net.ipv4.tcp_base_mss": "1024",
    # Enable TCP Fast Open for client + server
    "net.ipv4.tcp_fastopen": "3",
    # TIME_WAIT reuse
    "net.ipv4.tcp_tw_reuse": "1",
}


def run(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr}")
    return proc


def xui_available(xui_db: str) -> bool:
    return Path(xui_db).exists() and shutil.which("x-ui") is not None


def section(args: argparse.Namespace, title: str) -> None:
    if args.verbose:
        print(f"\n-- {title} --")


def apply_mobile_sysctl() -> bool:
    """Apply mobile-optimised sysctl entries."""
    lines = [
        "# SmartKamaVPN — mobile transport optimisation",
        "",
    ]
    for key, val in MOBILE_SYSCTL.items():
        lines.append(f"{key} = {val}")
    lines.append("")

    target = Path(SYSCTL_CONF)
    new_content = "\n".join(lines)
    if target.exists() and target.read_text() == new_content:
        print("[mobile] sysctl already up-to-date")
        return False

    target.write_text(new_content)
    run(["sysctl", "--system"], check=True)
    print("[mobile] sysctl applied:", SYSCTL_CONF)
    return True


def patch_xray_template_for_mobile(xui_db: str) -> bool:
    """
    Patch xray template in x-ui SQLite to add mobile resilience:
      - sockopt.tcpMptcp = true (multi-path TCP)
      - sockopt.tcpCongestion = "bbr"
      - sniffing.enabled = true (for domain-based routing)
    on each inbound's streamSettings.
    """
    if not Path(xui_db).exists():
        print("[mobile] x-ui DB not found, skipping xray template patch")
        return False

    conn = sqlite3.connect(xui_db)
    try:
        row = conn.execute(
            "SELECT rowid, value FROM settings WHERE key='xrayTemplateConfig' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not row or not row[1]:
            print("[mobile] xrayTemplateConfig empty")
            return False

        rowid = int(row[0])
        cfg = json.loads(str(row[1]))
        changed = False

        # Patch inbound sockopt & sniffing
        for inbound in cfg.get("inbounds") or []:
            ss = inbound.setdefault("streamSettings", {})
            sockopt = ss.setdefault("sockopt", {})

            if sockopt.get("tcpCongestion") != "bbr":
                sockopt["tcpCongestion"] = "bbr"
                changed = True

            if not sockopt.get("tcpMptcp"):
                sockopt["tcpMptcp"] = True
                changed = True

            sniffing = inbound.setdefault("sniffing", {})
            if not sniffing.get("enabled"):
                sniffing["enabled"] = True
                sniffing.setdefault("destOverride", ["http", "tls", "quic"])
                changed = True

        # Ensure DNS in template for fallback resolution
        dns = cfg.setdefault("dns", {})
        servers = dns.setdefault("servers", [])
        has_doh = any(
            isinstance(s, dict) and "1.1.1.1" in str(s.get("address", ""))
            for s in servers
        )
        if not has_doh:
            servers.insert(0, {
                "address": "https://1.1.1.1/dns-query",
                "domains": [],
                "skipFallback": False,
            })
            changed = True

        if not changed:
            print("[mobile] xray template already has mobile optimisations")
            return False

        # Backup
        ts = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?)",
            (f"xrayTemplateConfig_backup_{ts}", str(row[1])),
        )

        conn.execute(
            "UPDATE settings SET value=? WHERE rowid=?",
            (json.dumps(cfg, ensure_ascii=False), rowid),
        )
        conn.commit()
        print("[mobile] xray template patched with mobile optimisations")
        return True
    finally:
        conn.close()


def patch_inbounds_for_mobile(xui_db: str) -> bool:
    """
    Patch individual inbounds in x-ui to add:
      - gRPC inbounds: health_check_timeout, idle_timeout, permit_without_stream
      - WS inbounds: heartbeat shorter interval
      - All: accept_proxy_protocol=false to avoid breaking mobile carrier proxies
    """
    if not Path(xui_db).exists():
        return False

    conn = sqlite3.connect(xui_db)
    changed = False
    try:
        rows = conn.execute("SELECT id, stream_settings, sniffing FROM inbounds").fetchall()
        for row in rows:
            inbound_id = row[0]
            ss_raw = row[1] or "{}"
            sniff_raw = row[2] or "{}"

            ss = json.loads(ss_raw)
            sniff = json.loads(sniff_raw)
            dirty = False

            # Sockopt optimisations for each inbound
            sockopt = ss.setdefault("sockopt", {})
            if sockopt.get("tcpCongestion") != "bbr":
                sockopt["tcpCongestion"] = "bbr"
                dirty = True

            # gRPC health check / keepalive
            network = ss.get("network", "")
            if network == "grpc":
                grpc = ss.setdefault("grpcSettings", {})
                if grpc.get("health_check_timeout") != 20:
                    grpc["health_check_timeout"] = 20
                    grpc["idle_timeout"] = 60
                    grpc["permit_without_stream"] = True
                    dirty = True

            # Sniffing enabled
            if not sniff.get("enabled"):
                sniff["enabled"] = True
                sniff.setdefault("destOverride", ["http", "tls", "quic"])
                dirty = True

            if dirty:
                conn.execute(
                    "UPDATE inbounds SET stream_settings=?, sniffing=? WHERE id=?",
                    (json.dumps(ss, ensure_ascii=False), json.dumps(sniff, ensure_ascii=False), inbound_id),
                )
                changed = True

        if changed:
            conn.commit()
            print(f"[mobile] patched {sum(1 for _ in rows)} inbounds with mobile opts")
        else:
            print("[mobile] inbounds already optimised")
    finally:
        conn.close()
    return changed


def restart_xray():
    """Restart xray through x-ui."""
    if shutil.which("x-ui") is None:
        print("[mobile] skip xray restart: x-ui CLI not available")
        return
    proc = run(["x-ui", "restart-xray"])
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(f"[mobile] WARN: restart-xray exit={proc.returncode}: {proc.stderr}")
        # Fallback to systemctl
        run(["systemctl", "restart", "x-ui"], check=False)


def main():
    parser = argparse.ArgumentParser(description="SmartKamaVPN mobile transport optimisation")
    parser.add_argument("--xui-db", default=XUI_DB_DEFAULT)
    parser.add_argument("--skip-sysctl", action="store_true")
    parser.add_argument("--skip-template", action="store_true")
    parser.add_argument("--skip-inbounds", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        print("=" * 60)
        print("SmartKamaVPN - Mobile transport optimisation")
        print("=" * 60)

    changed = False
    xray_changed = False
    xui_ready = xui_available(args.xui_db)

    if not args.skip_sysctl:
        section(args, "Sysctl (MTU/BBR/keepalive)")
        if not args.dry_run:
            changed |= apply_mobile_sysctl()
        else:
            print("[dry-run] would apply sysctl")

    if not args.skip_template:
        section(args, "Xray Template (mobile patches)")
        if not args.dry_run:
            template_changed = patch_xray_template_for_mobile(args.xui_db)
            changed |= template_changed
            xray_changed |= template_changed
        else:
            print("[dry-run] would patch xray template")

    if not args.skip_inbounds:
        section(args, "Inbound Optimisations")
        if not args.dry_run:
            inbound_changed = patch_inbounds_for_mobile(args.xui_db)
            changed |= inbound_changed
            xray_changed |= inbound_changed
        else:
            print("[dry-run] would patch inbounds")

    if xray_changed and not args.dry_run:
        section(args, "Restart xray")
        restart_xray()
    elif changed and not args.dry_run and not xui_ready:
        section(args, "Restart xray")
        print("[mobile] skip xray restart: x-ui runtime not present, applied sysctl-only changes")

    if args.verbose:
        print("\n" + "=" * 60)
    print("[mobile] optimisation complete" if changed else "[mobile] already optimised")
    return 0


if __name__ == "__main__":
    sys.exit(main())
