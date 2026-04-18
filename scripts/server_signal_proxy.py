#!/usr/bin/env python3
"""
SmartKama Signal TLS Proxy Manager
=====================================
Manages signalapp/Signal-TLS-Proxy Docker Compose stack on production.

Signal TLS Proxy acts as an SNI-based TCP passthrough proxy (nginx stream)
so that Signal clients can reach Signal servers via your domain.

Requirements:
  - Docker + Docker Compose v2
  - A domain name (e.g. signal.example.com) pointing to this server
  - Ports 80 and 443 available (or SNI routing from existing nginx)

Share link for users: https://signal.tube/#<domain>

Usage:
  python server_signal_proxy.py install <domain>   # Clone repo, get cert, start
  python server_signal_proxy.py status              # Health/status JSON
  python server_signal_proxy.py remove              # Stop & remove containers
  python server_signal_proxy.py link                # Print share link
"""

import json
import logging
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
from typing import Optional

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_LOGS_DIR = _PROJECT_DIR / "Logs"
_STATE_FILE = _LOGS_DIR / "signal-proxy-state.json"
_SIGNAL_DIR = _PROJECT_DIR / "signal-tls-proxy"

_REPO_URL = "https://github.com/signalapp/Signal-TLS-Proxy.git"
_HEALTH_PORT = 443
_HTTP_PORT = 80

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [signal-proxy] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("signal-proxy")


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


def _run(cmd: list, cwd=None, capture=True, check=True, timeout=300) -> subprocess.CompletedProcess:
    log.debug("exec: %s", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, capture_output=capture, text=True, check=check,
                          timeout=timeout, cwd=cwd)


def _docker_compose(*args, cwd=None, capture=True, check=True, timeout=300) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose"] + list(args)
    return _run(cmd, cwd=cwd or str(_SIGNAL_DIR), capture=capture, check=check, timeout=timeout)


def _containers_running() -> bool:
    """Check if Signal proxy compose stack containers are up."""
    try:
        r = _docker_compose("ps", "--format", "json", check=False)
        if r.returncode != 0:
            return False
        # docker compose ps --format json outputs one JSON per line
        for line in r.stdout.strip().splitlines():
            try:
                obj = json.loads(line)
                state = obj.get("State", "").lower()
                if state == "running":
                    return True
            except json.JSONDecodeError:
                continue
        return False
    except Exception:
        return False


def _tcp_probe(host: str, port: int, timeout: float = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _get_public_ip() -> str:
    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://checkip.amazonaws.com"]:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ip = resp.read().decode().strip()
                if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
                    return ip
        except Exception:
            continue
    return "0.0.0.0"


def install(domain: str) -> dict:
    """Clone Signal-TLS-Proxy repo, obtain certificate, start containers."""
    if not domain or not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]+\.[a-zA-Z]{2,}$", domain):
        raise ValueError(f"Invalid domain: {domain!r}")

    # Clone or update repo
    if _SIGNAL_DIR.exists():
        log.info("Updating existing Signal-TLS-Proxy repo")
        _run(["git", "pull"], cwd=str(_SIGNAL_DIR), check=False)
    else:
        log.info("Cloning Signal-TLS-Proxy repo")
        _run(["git", "clone", _REPO_URL, str(_SIGNAL_DIR)])

    # Stop existing containers if any
    _docker_compose("down", check=False, timeout=60)

    # Run certificate initialization (non-interactive)
    cert_path = _SIGNAL_DIR / "data" / "certbot" / "conf"
    cert_path.mkdir(parents=True, exist_ok=True)

    # Download TLS options if missing
    options_file = cert_path / "options-ssl-nginx.conf"
    dhparams_file = cert_path / "ssl-dhparams.pem"
    if not options_file.exists():
        log.info("Downloading TLS options")
        _download_file(
            "https://raw.githubusercontent.com/certbot/certbot/refs/heads/main/certbot/src/certbot/_internal/plugins/nginx/tls_configs/options-ssl-nginx.conf",
            options_file,
        )
    if not dhparams_file.exists():
        log.info("Downloading DH params")
        _download_file(
            "https://raw.githubusercontent.com/certbot/certbot/main/certbot/src/certbot/ssl-dhparams.pem",
            dhparams_file,
        )

    # Request certificate via certbot standalone
    log.info("Requesting Let's Encrypt certificate for %s", domain)
    _run([
        "docker", "compose", "run", "-p", "80:80", "--rm", "--entrypoint",
        f"sh -c \"certbot certonly --standalone "
        f"--register-unsafely-without-email "
        f"-d {domain} "
        f"--agree-tos "
        f"--force-renewal && "
        f"ln -fs /etc/letsencrypt/live/{domain}/ /etc/letsencrypt/active\"",
    ], cwd=str(_SIGNAL_DIR), timeout=120)

    # Start the proxy stack
    log.info("Starting Signal TLS Proxy containers")
    _docker_compose("up", "--detach")

    public_ip = _get_public_ip()
    share_link = f"https://signal.tube/#{domain}"

    state = _load_state()
    state.update({
        "installed": True,
        "domain": domain,
        "public_ip": public_ip,
        "share_link": share_link,
        "ports": [_HTTP_PORT, _HEALTH_PORT],
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    _save_state(state)
    log.info("Signal TLS Proxy installed: %s → %s", domain, share_link)
    return state


def remove() -> dict:
    """Stop and remove Signal proxy containers."""
    if _SIGNAL_DIR.exists():
        _docker_compose("down", check=False, timeout=60)
        log.info("Signal proxy containers stopped")
    state = _load_state()
    state["installed"] = False
    state["removed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _save_state(state)
    return state


def status() -> dict:
    """Return current status of Signal proxy."""
    state = _load_state()
    running = _containers_running()
    health = "unknown"
    if running:
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
        "domain": state.get("domain"),
        "public_ip": state.get("public_ip"),
        "share_link": state.get("share_link"),
        "ports": state.get("ports", [_HTTP_PORT, _HEALTH_PORT]),
        "installed_at": state.get("installed_at"),
    }


def share_link(domain: Optional[str] = None) -> str:
    """Return the Signal proxy share link."""
    state = _load_state()
    domain = domain or state.get("domain")
    if not domain:
        raise RuntimeError("Signal proxy not installed or domain missing")
    return f"https://signal.tube/#{domain}"


def _download_file(url: str, dest: pathlib.Path):
    """Download a file from URL to dest path."""
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "install":
        if len(sys.argv) < 3:
            print("Usage: server_signal_proxy.py install <domain>")
            sys.exit(1)
        domain = sys.argv[2]
        print(json.dumps(install(domain), indent=2))
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "link":
        print(share_link())
    elif cmd == "remove":
        print(json.dumps(remove(), indent=2))
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
