#!/usr/bin/env python3
"""
SmartKama WhatsApp Proxy Manager
==================================
Manages facebook/whatsapp_proxy Docker container on the production server.

The proxy uses HAProxy to relay WhatsApp traffic.  Users configure
their WhatsApp client with the server IP address (Settings > Storage
and Data > Proxy).

Capabilities:
  - Install / remove WhatsApp proxy container
  - Health check (TCP probe on XMPP port + HAProxy stats)
  - Status reporting for autotune integration

Usage:
  python server_whatsapp_proxy.py install   # Deploy container
  python server_whatsapp_proxy.py status    # Health/status JSON
  python server_whatsapp_proxy.py remove    # Stop & remove container
  python server_whatsapp_proxy.py address   # Print proxy address
"""

import json
import logging
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------
_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_LOGS_DIR = _PROJECT_DIR / "Logs"
_STATE_FILE = _LOGS_DIR / "whatsapp-proxy-state.json"

_CONTAINER_NAME = "smartkama-whatsapp-proxy"
_WA_IMAGE = "facebook/whatsapp_proxy:latest"

# Ports: 80/443 are typically occupied by nginx/Marzban, so we expose
# only ports that WhatsApp can use as alternatives.
# WhatsApp client tries 443, 5222, 80 in order — 5222 (XMPP) is the
# primary alternative when 443 is unavailable.
_HOST_PORTS = {
    5222: 5222,   # XMPP (Jabber) — WhatsApp primary alternative
    587: 587,     # Media relay
    7777: 7777,   # Additional relay
}
_HEALTH_PORT = 5222  # port used for TCP health probe
_STATS_PORT = 8199   # HAProxy stats (localhost only)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [whatsapp-proxy] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("whatsapp-proxy")

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_STATE_FILE)


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------
def _docker(*args, capture=True, check=True, timeout=120) -> subprocess.CompletedProcess:
    cmd = ["docker"] + list(args)
    log.debug("exec: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=timeout,
    )


def _container_running() -> bool:
    try:
        r = _docker("inspect", "-f", "{{.State.Running}}", _CONTAINER_NAME, check=False)
        return r.returncode == 0 and "true" in r.stdout.strip().lower()
    except Exception:
        return False


def _container_exists() -> bool:
    try:
        r = _docker("inspect", _CONTAINER_NAME, check=False)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Install / deploy container
# ---------------------------------------------------------------------------
def install() -> dict:
    """Deploy WhatsApp proxy Docker container.  Returns state dict."""

    # Remove old container if present
    if _container_exists():
        log.info("Removing existing container %s", _CONTAINER_NAME)
        _docker("rm", "-f", _CONTAINER_NAME, check=False)

    cmd = [
        "run", "-d",
        "--name", _CONTAINER_NAME,
        "--restart", "unless-stopped",
    ]

    # Port mappings
    for host_port, container_port in _HOST_PORTS.items():
        cmd += ["-p", f"{host_port}:{container_port}"]

    # Stats port — bind to localhost only for health monitoring
    cmd += ["-p", f"127.0.0.1:{_STATS_PORT}:{_STATS_PORT}"]

    cmd.append(_WA_IMAGE)

    _docker(*cmd)

    # Detect public IP
    public_ip = _get_public_ip()

    state = _load_state()
    state.update({
        "installed": True,
        "public_ip": public_ip,
        "ports": list(_HOST_PORTS.keys()),
        "health_port": _HEALTH_PORT,
        "stats_port": _STATS_PORT,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    _save_state(state)

    log.info("WhatsApp proxy installed: %s  ports=%s", public_ip, list(_HOST_PORTS.keys()))
    return state


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------
def remove() -> dict:
    if _container_exists():
        _docker("rm", "-f", _CONTAINER_NAME, check=False)
        log.info("Container %s removed", _CONTAINER_NAME)

    state = _load_state()
    state["installed"] = False
    state["removed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save_state(state)
    return state


# ---------------------------------------------------------------------------
# Status / health check
# ---------------------------------------------------------------------------
def status() -> dict:
    state = _load_state()
    running = _container_running()

    health = "unknown"
    uptime = None
    if running:
        try:
            r = _docker("inspect", "-f", "{{.State.StartedAt}}", _CONTAINER_NAME)
            uptime = r.stdout.strip()
        except Exception:
            pass
        # TCP probe on XMPP port
        if _tcp_probe("127.0.0.1", _HEALTH_PORT, timeout=5):
            health = "healthy"
        else:
            health = "unhealthy"
    else:
        health = "stopped"

    return {
        "installed": state.get("installed", False),
        "running": running,
        "health": health,
        "public_ip": state.get("public_ip"),
        "ports": state.get("ports", list(_HOST_PORTS.keys())),
        "uptime_since": uptime,
        "installed_at": state.get("installed_at"),
    }


def _tcp_probe(host: str, port: int, timeout: float = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Proxy address (what users enter in WhatsApp)
# ---------------------------------------------------------------------------
def proxy_address(server: Optional[str] = None) -> str:
    state = _load_state()
    server = server or state.get("public_ip")
    if not server:
        raise RuntimeError("WhatsApp proxy not installed or state missing")
    return server


# ---------------------------------------------------------------------------
# Public IP detection
# ---------------------------------------------------------------------------
def _get_public_ip() -> str:
    for url in [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://checkip.amazonaws.com",
    ]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ip = resp.read().decode().strip()
                if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                    return ip
        except Exception:
            continue
    return "0.0.0.0"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "install":
        result = install()
        print(json.dumps(result, indent=2))

    elif cmd == "status":
        result = status()
        print(json.dumps(result, indent=2))

    elif cmd == "address":
        print(proxy_address())

    elif cmd == "remove":
        result = remove()
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
