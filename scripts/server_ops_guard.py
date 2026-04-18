#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, cast
from urllib.parse import parse_qs, urlparse

import requests
import urllib3


XUI_DB_DEFAULT = "/etc/x-ui/x-ui.db"
BOT_DB_DEFAULT = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
MARZBAN_URL_DEFAULT = "http://127.0.0.1:8000"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "error"
    fixable: bool = False


class Guard:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.issues: List[Issue] = []
        self.logs: List[str] = []
        self.session = requests.Session()
        self.session.verify = False

    def log(self, *parts: object) -> None:
        line = " ".join(str(p) for p in parts)
        self.logs.append(line)
        print(line)

    def add_issue(self, code: str, message: str, severity: str = "error", fixable: bool = False) -> None:
        self.issues.append(Issue(code=code, message=message, severity=severity, fixable=fixable))
        self.log(f"[{severity.upper()}] {code}: {message}")

    @staticmethod
    def _normalize_provider(raw_value: object) -> str:
        value = str(raw_value or "3xui").strip().lower()
        if value in {"3x-ui", "x-ui"}:
            return "3xui"
        if value == "marzban":
            return "marzban"
        return "3xui"

    @staticmethod
    def _run(cmd: List[str]) -> Tuple[int, str, str]:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    @staticmethod
    def _service_is_active(name: str) -> bool:
        code, out, _ = Guard._run(["systemctl", "is-active", name])
        return code == 0 and out == "active"

    @staticmethod
    def _restart_service(name: str) -> bool:
        if name in {"x-ui", "x-ui.service"}:
            code, _, _ = Guard._run(["x-ui", "restart"])
        else:
            code, _, _ = Guard._run(["systemctl", "restart", name])
        if code != 0:
            return False
        return Guard._service_is_active(name)

    @staticmethod
    def _wait_for_port(host: str, port: int, timeout: float = 15.0, interval: float = 0.5) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if Guard._port_open(host, port, timeout=1.0):
                return True
            time.sleep(interval)
        return False

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    @staticmethod
    def _decode_sub_payload(raw: str) -> str:
        payload = (raw or "").strip()
        if not payload:
            return ""
        if "vless://" in payload or "trojan://" in payload or "vmess://" in payload:
            return payload
        try:
            padded = payload + "=" * ((4 - len(payload) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            return decoded
        except Exception:
            return payload

    @staticmethod
    def _extract_sub_id_from_target(target_url: str) -> Optional[str]:
        parsed = urlparse(target_url or "")
        path = (parsed.path or "").strip("/")
        if not path:
            return None
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "sub":
            return parts[1]
        if len(parts) >= 1 and parts[0] == "sub" and parsed.query:
            return parts[-1]
        return None

    def _load_xui_settings(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        if not os.path.exists(self.args.xui_db):
            self.add_issue("xui_db_missing", f"x-ui DB not found: {self.args.xui_db}", "error", False)
            return result
        conn = sqlite3.connect(self.args.xui_db)
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            for key, value in rows:
                result[str(key)] = str(value)
        finally:
            conn.close()
        return result

    def _load_reality_inbounds(self) -> List[Dict[str, Any]]:
        inbounds: List[Dict[str, Any]] = []
        if not os.path.exists(self.args.xui_db):
            return inbounds
        conn = sqlite3.connect(self.args.xui_db)
        try:
            rows = conn.execute("SELECT id, port, remark, stream_settings FROM inbounds ORDER BY id").fetchall()
            for inbound_id, port, remark, stream_settings in rows:
                try:
                    stream = json.loads(stream_settings or "{}")
                except Exception:
                    continue
                if str(stream.get("security") or "").lower() != "reality":
                    continue
                reality = dict(stream.get("realitySettings") or {})
                inbounds.append(
                    {
                        "id": int(inbound_id),
                        "port": int(port),
                        "remark": str(remark or ""),
                        "stream": stream,
                        "reality": reality,
                    }
                )
        finally:
            conn.close()
        return inbounds

    def _load_bot_str_config(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        if not os.path.exists(self.args.bot_db):
            self.add_issue("bot_db_missing", f"Bot DB not found: {self.args.bot_db}", "error", False)
            return result
        conn = sqlite3.connect(self.args.bot_db)
        try:
            try:
                rows = conn.execute("SELECT key, value FROM str_config").fetchall()
            except sqlite3.OperationalError:
                return result
            for key, value in rows:
                result[str(key)] = str(value)
        finally:
            conn.close()
        return result

    def _run_marzban_selfcheck(self) -> bool:
        script_path = os.path.join(os.path.dirname(__file__), "selfcheck_marzban_api.py")
        if not os.path.exists(script_path):
            self.add_issue("marzban_selfcheck_missing", f"Selfcheck script not found: {script_path}", "error", False)
            return False

        code, out, err = self._run([sys.executable, script_path])
        if code != 0:
            details = err or out or "unknown error"
            self.add_issue("marzban_selfcheck_failed", f"Marzban selfcheck failed: {details}", "error", False)
            return False

        brief = out.splitlines()[0] if out else "OK"
        self.log("marzban selfcheck", brief)
        return True

    def _ensure_shortlink_tables(self) -> bool:
        if not os.path.exists(self.args.bot_db):
            return False
        conn = sqlite3.connect(self.args.bot_db)
        changed = False
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS short_links (token TEXT PRIMARY KEY,target_url TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS short_links_meta (token TEXT PRIMARY KEY,remaining_days INTEGER,remaining_hours INTEGER,remaining_minutes INTEGER,usage_current REAL,usage_limit REAL,updated_at TEXT NOT NULL,FOREIGN KEY(token) REFERENCES short_links(token) ON DELETE CASCADE)"
            )
            conn.commit()
            changed = True
        finally:
            conn.close()
        return changed

    def _get_marzban_sub_token(self) -> Optional[str]:
        """Get subscription token of the first Marzban user via API."""
        base = (self.args.marzban_url or "").rstrip("/")
        if not base:
            return None
        bot_config = self._load_bot_str_config()
        username = (bot_config.get("marzban_username") or os.getenv("MARZBAN_USERNAME") or "").strip()
        password = (bot_config.get("marzban_password") or os.getenv("MARZBAN_PASSWORD") or "").strip()
        access_token = os.getenv("MARZBAN_ACCESS_TOKEN", "").strip()
        if access_token:
            token = access_token
        elif username and password:
            try:
                resp = self.session.post(
                    f"{base}/api/admin/token",
                    data={"username": username, "password": password},
                    timeout=10,
                )
                if resp.status_code != 200:
                    self.log("marzban auth failed", resp.status_code)
                    return None
                token = resp.json().get("access_token") or resp.json().get("token") or ""
            except Exception as exc:
                self.log("marzban auth error", exc)
                return None
        else:
            return None
        if not token:
            return None
        try:
            resp = self.session.get(
                f"{base}/api/users",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if resp.status_code != 200:
                self.log("marzban list users failed", resp.status_code)
                return None
            data = resp.json()
            users = data.get("users", data) if isinstance(data, dict) else data
            if not users:
                return None
            sub_url = str(users[0].get("subscription_url") or "").strip()
            if sub_url:
                pieces = [p for p in sub_url.split("/") if p]
                if pieces:
                    return pieces[-1].split("?")[0]
        except Exception as exc:
            self.log("marzban users error", exc)
        return None

    def _discover_sub_id(self) -> Optional[str]:
        if self.args.sub_id:
            return self.args.sub_id.strip()

        if not os.path.exists(self.args.bot_db):
            return self._get_marzban_sub_token()

        conn = sqlite3.connect(self.args.bot_db)
        try:
            row = conn.execute(
                "SELECT target_url FROM short_links ORDER BY rowid DESC LIMIT 20"
            ).fetchall()
            for item in row:
                sub_id = self._extract_sub_id_from_target(str(item[0]))
                if sub_id:
                    # Verify the sub_id actually returns 200 from Marzban
                    try:
                        check_url = f"http://127.0.0.1:8000/sub/{sub_id}"
                        resp = self.session.get(check_url, timeout=5)
                        if resp.status_code == 200:
                            return sub_id
                    except Exception:
                        pass
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

        # Fallback: try Marzban API
        return self._get_marzban_sub_token()

    def _find_or_create_short_token(self, sub_id: str, create_if_missing: bool) -> Optional[str]:
        if not os.path.exists(self.args.bot_db):
            return None

        target_url = f"https://{self.args.sub_domain}:{self.args.public_port}/sub/{sub_id}"
        conn = sqlite3.connect(self.args.bot_db)
        try:
            try:
                row = conn.execute(
                    "SELECT token FROM short_links WHERE target_url=? ORDER BY rowid DESC LIMIT 1",
                    (target_url,),
                ).fetchone()
            except sqlite3.OperationalError:
                return None

            if row and row[0]:
                return str(row[0])

            if not create_if_missing:
                return None

            token = f"guard-{secrets.token_hex(4)}"
            conn.execute(
                "INSERT OR REPLACE INTO short_links(token,target_url,created_at) VALUES(?,?,?)",
                (token, target_url, dt.datetime.now(dt.timezone.utc).isoformat()),
            )
            conn.commit()
            self.log("created short token", token, "for", target_url)
            return token
        finally:
            conn.close()

    def _public_url(self, path: str, port: int) -> str:
        port_suffix = f":{port}" if port not in (443, 0) else ""
        if not path.startswith("/"):
            path = f"/{path}"
        return f"https://{self.args.sub_domain}{port_suffix}{path}"

    def _check_subscription_url(self, url: str, expected_host: str, expected_ip: str) -> Tuple[bool, Dict[str, object]]:
        try:
            resp = self.session.get(url, timeout=20)
        except Exception as exc:
            self.add_issue("sub_request_failed", f"{url} -> {exc}", "error", False)
            return False, {}

        if resp.status_code != 200:
            self.add_issue("sub_bad_status", f"{url} -> HTTP {resp.status_code}", "error", True)
            return False, {"status": resp.status_code}

        text = self._decode_sub_payload(resp.text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            self.add_issue("sub_empty", f"{url} returned no config lines", "error", True)
            return False, {"status": resp.status_code, "lines": 0}

        reality_count = 0
        host_mismatch = 0
        missing_reality_flags = 0
        kinds = {"vless": 0, "trojan": 0, "vmess": 0}

        for line in lines:
            lower = line.lower()
            if lower.startswith("vless://"):
                kinds["vless"] += 1
                parsed = urlparse(line.split("#", 1)[0])
                host = (parsed.hostname or "").strip().lower()
                if host and expected_host and host not in {expected_host.lower(), expected_ip.lower()}:
                    host_mismatch += 1
                query = parse_qs(parsed.query)
                security = (query.get("security", [""])[0] or "").lower()
                if security == "reality":
                    reality_count += 1
                    has_pbk = bool(query.get("pbk", [""])[0])
                    has_fp = bool(query.get("fp", [""])[0])
                    has_sid = bool(query.get("sid", [""])[0])
                    flow = (query.get("flow", [""])[0] or "")
                    has_flow = flow == "xtls-rprx-vision"
                    if not (has_pbk and has_fp and has_sid and has_flow):
                        missing_reality_flags += 1
            elif lower.startswith("trojan://"):
                kinds["trojan"] += 1
                parsed = urlparse(line.split("#", 1)[0])
                host = (parsed.hostname or "").strip().lower()
                if host and expected_host and host not in {expected_host.lower(), expected_ip.lower()}:
                    host_mismatch += 1
            elif lower.startswith("vmess://"):
                kinds["vmess"] += 1

        if host_mismatch:
            self.add_issue(
                "sub_host_mismatch",
                f"{url} has {host_mismatch} lines with host != {expected_host}/{expected_ip}",
                "error",
                True,
            )

        if reality_count and missing_reality_flags:
            self.add_issue(
                "sub_reality_missing_flags",
                f"{url} has {missing_reality_flags}/{reality_count} Reality lines without pbk/fp/sid/flow",
                "error",
                True,
            )

        return True, {
            "status": resp.status_code,
            "lines": len(lines),
            "kinds": kinds,
            "reality": reality_count,
            "missing_reality": missing_reality_flags,
        }

    def diagnose(self) -> bool:
        self.log("== diagnose start ==")

        bot_config = self._load_bot_str_config()
        provider = self._normalize_provider(bot_config.get("panel_provider") if bot_config else "3xui")
        self.log("panel provider", provider)

        services = ["smartkamavpn", "smartkama-shortlink.service", "nginx"]
        if provider != "marzban":
            services.insert(0, "x-ui")

        for svc in services:
            active = self._service_is_active(svc)
            if active:
                self.log("service", svc, "active")
            else:
                self.add_issue("service_inactive", f"{svc} is not active", "warning", True)

        ports_to_check = [
            ("127.0.0.1", self.args.shortlink_port, "shortlink_local"),
            ("127.0.0.1", self.args.public_port, "public_sub_local"),
        ]
        if provider != "marzban":
            ports_to_check.insert(1, ("127.0.0.1", self.args.internal_sub_port, "xui_sub_local"))

        for host, port, name in ports_to_check:
            if self._port_open(host, port):
                self.log("port", name, f"{host}:{port}", "open")
            else:
                self.add_issue("port_closed", f"{name} {host}:{port} is not open", "warning", True)

        if provider == "marzban":
            if bot_config and not (bot_config.get("marzban_panel_url") or "").strip():
                self.add_issue("marzban_panel_url_empty", "str_config marzban_panel_url is empty", "warning", True)
            self._run_marzban_selfcheck()
        else:
            xui_settings = self._load_xui_settings()
            if xui_settings:
                sub_domain = xui_settings.get("subDomain", "")
                sub_listen = xui_settings.get("subListen", "")
                sub_port = xui_settings.get("subPort", "")
                self.log("xui settings", f"subDomain={sub_domain}", f"subListen={sub_listen}", f"subPort={sub_port}")

                if self.args.sub_domain and sub_domain != self.args.sub_domain:
                    self.add_issue(
                        "xui_subdomain_mismatch",
                        f"x-ui subDomain={sub_domain} expected={self.args.sub_domain}",
                        "warning",
                        True,
                    )
                if sub_listen != "127.0.0.1":
                    self.add_issue("xui_sublisten_bad", f"x-ui subListen={sub_listen} expected=127.0.0.1", "warning", True)
                if str(sub_port) != str(self.args.internal_sub_port):
                    self.add_issue(
                        "xui_subport_bad",
                        f"x-ui subPort={sub_port} expected={self.args.internal_sub_port}",
                        "warning",
                        True,
                    )

            reality_inbounds = self._load_reality_inbounds()
            if not reality_inbounds:
                self.add_issue("reality_missing", "No Reality inbounds found in x-ui DB", "error", False)
            else:
                for item in reality_inbounds:
                    iid = int(cast(int, item["id"]))
                    port = int(cast(int, item["port"]))
                    reality = cast(Dict[str, Any], item["reality"])
                    pk = str(reality.get("privateKey") or "")
                    pub = str(reality.get("publicKey") or "")
                    fp = str(reality.get("fingerprint") or "")
                    short_ids = list(reality.get("shortIds") or [])

                    if not pk:
                        self.add_issue("reality_no_private", f"inbound {iid}:{port} has empty privateKey", "error", False)
                    if not pub:
                        self.add_issue("reality_no_public", f"inbound {iid}:{port} has empty publicKey", "error", False)
                    if not short_ids:
                        self.add_issue("reality_no_shortid", f"inbound {iid}:{port} has empty shortIds", "warning", True)
                    if fp.lower() != "chrome":
                        self.add_issue(
                            "reality_fp_not_chrome",
                            f"inbound {iid}:{port} fingerprint={fp or 'EMPTY'} expected=chrome",
                            "warning",
                            True,
                        )

        if bot_config and provider != "marzban":
            fp_cfg = (bot_config.get("threexui_reality_fingerprint") or "").lower().strip()
            if fp_cfg != "chrome":
                self.add_issue(
                    "bot_fp_not_chrome",
                    f"str_config threexui_reality_fingerprint={fp_cfg or 'EMPTY'} expected=chrome",
                    "warning",
                    True,
                )

            if not (bot_config.get("threexui_inbound_ids") or "").strip():
                self.add_issue("bot_inbound_ids_empty", "str_config threexui_inbound_ids is empty", "warning", True)

        sub_id = self._discover_sub_id()
        if not sub_id:
            self.add_issue("sub_id_missing", "Cannot auto-discover sub_id (use --sub-id)", "error", False)
            self.log("== diagnose done ==")
            return False

        self.log("using sub_id", sub_id)
        sub_url = self._public_url(f"/sub/{sub_id}", self.args.public_port)
        _, sub_meta = self._check_subscription_url(sub_url, self.args.sub_domain, self.args.server_ip)
        if sub_meta:
            self.log("native sub", json.dumps(sub_meta, ensure_ascii=False))

        token = self._find_or_create_short_token(sub_id, create_if_missing=False)
        if token:
            short_url = self._public_url(f"/s/{token}?raw=1", self.args.public_port)
            _, short_meta = self._check_subscription_url(short_url, self.args.sub_domain, self.args.server_ip)
            if short_meta:
                self.log("shortlink sub", json.dumps(short_meta, ensure_ascii=False))
        else:
            self.add_issue("short_token_missing", f"No short token found for sub_id={sub_id}", "warning", True)

        if self.args.user_facing_port not in (self.args.public_port, 0):
            user_sub_url = self._public_url(f"/sub/{sub_id}", self.args.user_facing_port)
            _, user_sub_meta = self._check_subscription_url(user_sub_url, self.args.sub_domain, self.args.server_ip)
            if user_sub_meta:
                self.log("user-facing native sub", json.dumps(user_sub_meta, ensure_ascii=False))

            if token:
                user_short_url = self._public_url(f"/s/{token}?raw=1", self.args.user_facing_port)
                _, user_short_meta = self._check_subscription_url(user_short_url, self.args.sub_domain, self.args.server_ip)
                if user_short_meta:
                    self.log("user-facing shortlink sub", json.dumps(user_short_meta, ensure_ascii=False))

        self.log("== diagnose done ==")
        return not any(i.severity == "error" for i in self.issues)

    def autofix(self) -> bool:
        self.log("== autofix start ==")
        changed = False
        bot_config = self._load_bot_str_config()
        provider = self._normalize_provider(bot_config.get("panel_provider") if bot_config else "3xui")

        if self._ensure_shortlink_tables():
            changed = True

        if provider != "marzban" and os.path.exists(self.args.bot_db):
            conn = sqlite3.connect(self.args.bot_db)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
                    ("threexui_reality_fingerprint", "chrome"),
                )

                current_ids = conn.execute(
                    "SELECT value FROM str_config WHERE key='threexui_inbound_ids' LIMIT 1"
                ).fetchone()
                if not current_ids or not str(current_ids[0] or "").strip():
                    # Build default list from x-ui inbounds order.
                    inbound_ids = []
                    if os.path.exists(self.args.xui_db):
                        xconn = sqlite3.connect(self.args.xui_db)
                        try:
                            rows = xconn.execute("SELECT id FROM inbounds WHERE enable=1 ORDER BY id").fetchall()
                            inbound_ids = [str(int(r[0])) for r in rows]
                        finally:
                            xconn.close()
                    if inbound_ids:
                        conn.execute(
                            "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
                            ("threexui_inbound_ids", ",".join(inbound_ids)),
                        )

                current_pbk = conn.execute(
                    "SELECT value FROM str_config WHERE key='threexui_reality_public_key' LIMIT 1"
                ).fetchone()
                if not current_pbk or not str(current_pbk[0] or "").strip():
                    reality = self._load_reality_inbounds()
                    if reality:
                        first_reality = cast(Dict[str, Any], reality[0].get("reality") or {})
                        first_pub = str(first_reality.get("publicKey") or "").strip()
                        if first_pub:
                            conn.execute(
                                "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
                                ("threexui_reality_public_key", first_pub),
                            )

                conn.commit()
                changed = True
            finally:
                conn.close()

        if provider != "marzban" and os.path.exists(self.args.xui_db):
            xconn = sqlite3.connect(self.args.xui_db)
            try:
                xconn.execute("UPDATE settings SET value=? WHERE key='subDomain'", (self.args.sub_domain,))
                xconn.execute("UPDATE settings SET value='127.0.0.1' WHERE key='subListen'")
                xconn.execute("UPDATE settings SET value=? WHERE key='subPort'", (str(self.args.internal_sub_port),))
                xconn.execute("UPDATE settings SET value='true' WHERE key='subEnable'")
                xconn.execute("UPDATE settings SET value='true' WHERE key='subJsonEnable'")
                xconn.execute("UPDATE settings SET value='true' WHERE key='subEnableRouting'")

                rows = xconn.execute("SELECT id, stream_settings FROM inbounds").fetchall()
                for inbound_id, stream_settings in rows:
                    try:
                        stream = json.loads(stream_settings or "{}")
                    except Exception:
                        continue
                    if str(stream.get("security") or "").lower() != "reality":
                        continue
                    reality = dict(stream.get("realitySettings") or {})
                    updated = False
                    if str(reality.get("fingerprint") or "").lower() != "chrome":
                        reality["fingerprint"] = "chrome"
                        updated = True
                    short_ids = list(reality.get("shortIds") or [])
                    if not short_ids:
                        reality["shortIds"] = [secrets.token_hex(4)]
                        updated = True
                    if updated:
                        stream["realitySettings"] = reality
                        xconn.execute(
                            "UPDATE inbounds SET stream_settings=? WHERE id=?",
                            (json.dumps(stream, ensure_ascii=False, separators=(",", ":")), int(inbound_id)),
                        )
                        changed = True

                xconn.commit()
            finally:
                xconn.close()

        sub_id = self._discover_sub_id()
        if sub_id:
            token = self._find_or_create_short_token(sub_id, create_if_missing=True)
            if token:
                changed = True
        else:
            self.add_issue("autofix_subid_missing", "Autofix skipped shortlink creation: sub_id not found", "warning", False)

        if changed:
            services = ["smartkama-shortlink.service", "smartkamavpn", "nginx"]
            if provider != "marzban":
                services.insert(0, "x-ui")

            for svc in services:
                if self._restart_service(svc):
                    self.log("restarted", svc)
                else:
                    self.add_issue("restart_failed", f"Failed to restart {svc}", "warning", False)

            if provider != "marzban":
                if not self._wait_for_port("127.0.0.1", self.args.internal_sub_port, timeout=20.0):
                    self.add_issue(
                        "xui_subport_not_ready",
                        f"x-ui sub listener did not become ready on 127.0.0.1:{self.args.internal_sub_port}",
                        "warning",
                        True,
                    )
            if not self._wait_for_port("127.0.0.1", self.args.shortlink_port, timeout=10.0):
                self.add_issue(
                    "shortlink_not_ready",
                    f"shortlink listener did not become ready on 127.0.0.1:{self.args.shortlink_port}",
                    "warning",
                    True,
                )
            if not self._wait_for_port("127.0.0.1", self.args.public_port, timeout=10.0):
                self.add_issue(
                    "public_port_not_ready",
                    f"public subscription listener did not become ready on 127.0.0.1:{self.args.public_port}",
                    "warning",
                    True,
                )

        self.log("== autofix done ==")
        return True

    def smoke(self) -> bool:
        self.log("== smoke start ==")
        sub_id = self._discover_sub_id()
        if not sub_id:
            self.add_issue("smoke_subid_missing", "Smoke failed: cannot find sub_id (pass --sub-id)", "error", False)
            return False

        native = self._public_url(f"/sub/{sub_id}", self.args.public_port)
        native_ok, _ = self._check_subscription_url(native, self.args.sub_domain, self.args.server_ip)

        token = self._find_or_create_short_token(sub_id, create_if_missing=True)
        short_ok = False
        if token:
            short = self._public_url(f"/s/{token}?raw=1", self.args.public_port)
            short_ok, _ = self._check_subscription_url(short, self.args.sub_domain, self.args.server_ip)
        else:
            self.add_issue("smoke_short_missing", "Smoke failed: short token missing", "error", False)

        user_facing_ok = True
        if self.args.user_facing_port not in (self.args.public_port, 0):
            user_native = self._public_url(f"/sub/{sub_id}", self.args.user_facing_port)
            user_native_ok, _ = self._check_subscription_url(user_native, self.args.sub_domain, self.args.server_ip)
            user_short_ok = short_ok
            if token:
                user_short = self._public_url(f"/s/{token}?raw=1", self.args.user_facing_port)
                user_short_ok, _ = self._check_subscription_url(user_short, self.args.sub_domain, self.args.server_ip)
            user_facing_ok = user_native_ok and user_short_ok

        self.log("== smoke done ==")
        return native_ok and short_ok and user_facing_ok


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartKama production guard for subscriptions and Reality")
    parser.add_argument("--mode", choices=["diagnose", "autofix", "smoke", "all"], default="all")
    parser.add_argument("--xui-db", default=XUI_DB_DEFAULT)
    parser.add_argument("--bot-db", default=BOT_DB_DEFAULT)
    parser.add_argument("--sub-domain", default=os.getenv("SMARTKAMA_SUB_DOMAIN", "sub.smartkama.ru"))
    parser.add_argument("--server-ip", default=os.getenv("SMARTKAMA_SERVER_IP", "72.56.100.45"))
    parser.add_argument("--public-port", type=int, default=int(os.getenv("SMARTKAMA_SUB_PUBLIC_PORT", "2096")))
    parser.add_argument("--user-facing-port", type=int, default=int(os.getenv("SMARTKAMA_SUB_USER_FACING_PORT", "443")))
    parser.add_argument("--internal-sub-port", type=int, default=int(os.getenv("SMARTKAMA_SUB_INTERNAL_PORT", "2097")))
    parser.add_argument("--shortlink-port", type=int, default=int(os.getenv("SMARTKAMA_SHORTLINK_PORT", "9101")))
    parser.add_argument("--sub-id", default=os.getenv("SMARTKAMA_SUB_ID", ""))
    parser.add_argument("--marzban-url", default=os.getenv("MARZBAN_PANEL_URL", MARZBAN_URL_DEFAULT))
    return parser.parse_args()


def summarize(issues: List[Issue]) -> int:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    print("\n== summary ==")
    print("errors:", len(errors), "warnings:", len(warnings))
    if issues:
        for issue in issues:
            print(f"- {issue.severity.upper()} {issue.code}: {issue.message} (fixable={issue.fixable})")
    return len(errors)


def main() -> int:
    args = parse_args()
    guard = Guard(args)

    if args.mode == "diagnose":
        guard.diagnose()
    elif args.mode == "autofix":
        guard.autofix()
        guard.diagnose()
    elif args.mode == "smoke":
        guard.smoke()
    else:
        guard.autofix()
        guard.diagnose()
        guard.smoke()

    err_count = summarize(guard.issues)
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
