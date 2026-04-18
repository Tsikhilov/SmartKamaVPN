#!/usr/bin/env python3
"""
SmartKama MTProto Proxy Manager
================================
Manages mtg (MTProto proxy in Go) via Docker on the production server.

Capabilities:
  - Install / remove mtg container
  - Generate FakeTLS secrets (disguise as popular websites)
  - Health check (connectivity, uptime, stats)
  - Generate tg://proxy sharing links
  - Status reporting for autotune integration

Usage:
  python server_mtproto_proxy.py install          # Deploy mtg container
  python server_mtproto_proxy.py status           # Health/status JSON
  python server_mtproto_proxy.py generate-secret  # New FakeTLS secret
  python server_mtproto_proxy.py link             # tg://proxy link
  python server_mtproto_proxy.py remove           # Stop & remove container
  python server_mtproto_proxy.py promote <tag>    # Set promoted channel tag
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
_STATE_FILE = _LOGS_DIR / "mtproto-proxy-state.json"

_CONTAINER_NAME = "smartkama-mtg"
_MTG_IMAGE = "nineseconds/mtg:2"
_DEFAULT_PORT = 3128
_DEFAULT_FAKETLS_HOST = "storage.googleapis.com"
_PROMOTE_TAG = ""  # e.g. "@SmartKamaVPN"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [mtproto-proxy] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mtproto-proxy")

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
def _docker(*args, capture=True, check=True, timeout=60) -> subprocess.CompletedProcess:
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
# Secret generation  (mtg generate-secret)
# ---------------------------------------------------------------------------
def generate_secret(faketls_host: str = _DEFAULT_FAKETLS_HOST) -> str:
    """Generate a FakeTLS secret using mtg CLI inside Docker."""
    r = _docker(
        "run", "--rm", _MTG_IMAGE,
        "generate-secret", faketls_host,
    )
    secret = r.stdout.strip()
    if not secret:
        raise RuntimeError("mtg generate-secret returned empty output")
    log.info("Generated FakeTLS secret for host=%s: %s...%s", faketls_host, secret[:8], secret[-4:])
    return secret


# ---------------------------------------------------------------------------
# Install / deploy mtg container
# ---------------------------------------------------------------------------
def install(
    port: int = _DEFAULT_PORT,
    faketls_host: str = _DEFAULT_FAKETLS_HOST,
    promote_tag: str = _PROMOTE_TAG,
    secret: Optional[str] = None,
) -> dict:
    """Deploy mtg Docker container.  Returns state dict."""

    state = _load_state()

    # Reuse existing secret if not provided
    if not secret:
        secret = state.get("secret")
    if not secret:
        secret = generate_secret(faketls_host)

    # Remove old container if present
    if _container_exists():
        log.info("Removing existing container %s", _CONTAINER_NAME)
        _docker("rm", "-f", _CONTAINER_NAME, check=False)

    cmd = [
        "run", "-d",
        "--name", _CONTAINER_NAME,
        "--restart", "unless-stopped",
        "-p", f"{port}:{3128}",
    ]

    if promote_tag:
        cmd += ["--env", f"MTG_PROMOTE_TAG={promote_tag}"]

    cmd += [_MTG_IMAGE, "simple-run", f"0.0.0.0:{3128}", secret]

    _docker(*cmd)

    # Detect public IP
    public_ip = _get_public_ip()

    state.update({
        "installed": True,
        "port": port,
        "secret": secret,
        "faketls_host": faketls_host,
        "promote_tag": promote_tag,
        "public_ip": public_ip,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    _save_state(state)

    log.info("MTProto proxy installed: %s:%d", public_ip, port)
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
        # TCP probe
        port = state.get("port", _DEFAULT_PORT)
        ip = state.get("public_ip") or "127.0.0.1"
        if _tcp_probe(ip, port, timeout=5):
            health = "healthy"
        else:
            health = "unhealthy"
    else:
        health = "stopped"

    result = {
        "installed": state.get("installed", False),
        "running": running,
        "health": health,
        "port": state.get("port", _DEFAULT_PORT),
        "public_ip": state.get("public_ip"),
        "faketls_host": state.get("faketls_host"),
        "promote_tag": state.get("promote_tag", ""),
        "secret_preview": (state.get("secret", "")[:8] + "...") if state.get("secret") else None,
        "uptime_since": uptime,
        "installed_at": state.get("installed_at"),
    }
    return result


def _tcp_probe(host: str, port: int, timeout: float = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# tg://proxy link
# ---------------------------------------------------------------------------
def proxy_link(server: Optional[str] = None, port: Optional[int] = None, secret: Optional[str] = None) -> str:
    state = _load_state()
    server = server or state.get("public_ip")
    port = port or state.get("port", _DEFAULT_PORT)
    secret = secret or state.get("secret")

    if not server or not secret:
        raise RuntimeError("MTProto proxy not installed or state missing")

    return f"tg://proxy?server={server}&port={port}&secret={secret}"


def proxy_link_https(server: Optional[str] = None, port: Optional[int] = None, secret: Optional[str] = None) -> str:
    """Return t.me/proxy link (opens in Telegram apps that don't handle tg://)."""
    state = _load_state()
    server = server or state.get("public_ip")
    port = port or state.get("port", _DEFAULT_PORT)
    secret = secret or state.get("secret")

    if not server or not secret:
        raise RuntimeError("MTProto proxy not installed or state missing")

    return f"https://t.me/proxy?server={server}&port={port}&secret={secret}"


# ---------------------------------------------------------------------------
# Promote tag management
# ---------------------------------------------------------------------------
def set_promote_tag(tag: str):
    state = _load_state()
    state["promote_tag"] = tag
    _save_state(state)

    # Restart container to apply
    if _container_running():
        _docker("restart", _CONTAINER_NAME)
        log.info("Container restarted with promote_tag=%s", tag)


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
        port = int(os.environ.get("MTPROTO_PORT", _DEFAULT_PORT))
        host = os.environ.get("MTPROTO_FAKETLS_HOST", _DEFAULT_FAKETLS_HOST)
        tag = os.environ.get("MTPROTO_PROMOTE_TAG", _PROMOTE_TAG)
        result = install(port=port, faketls_host=host, promote_tag=tag)
        print(json.dumps(result, indent=2))

    elif cmd == "status":
        result = status()
        print(json.dumps(result, indent=2))

    elif cmd == "generate-secret":
        host = sys.argv[2] if len(sys.argv) > 2 else _DEFAULT_FAKETLS_HOST
        s = generate_secret(host)
        print(s)

    elif cmd == "link":
        print("tg://   ", proxy_link())
        print("https://", proxy_link_https())

    elif cmd == "remove":
        result = remove()
        print(json.dumps(result, indent=2))

    elif cmd == "promote":
        tag = sys.argv[2] if len(sys.argv) > 2 else ""
        set_promote_tag(tag)
        print(f"Promote tag set to: {tag!r}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
