#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


XUI_DB_DEFAULT = "/etc/x-ui/x-ui.db"
BOT_DB_DEFAULT = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
BASE_DIR_DEFAULT = "/opt/SmartKamaVPN"
PYTHON_BIN_DEFAULT = "/opt/SmartKamaVPN/.venv/bin/python"

OUTBOUND_TEST_URL = "https://www.gstatic.com/generate_204"
WARP_DOMAINS = [
    "domain:openai.com",
    "domain:api.openai.com",
    "domain:chatgpt.com",
    "domain:oaistatic.com",
    "domain:claude.ai",
    "domain:anthropic.com",
    "domain:poe.com",
    "domain:perplexity.ai",
]


def run(cmd: List[str], check: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc


class AutoTuner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.changed = False
        self.provider = self._load_panel_provider()

    def log(self, *parts: object) -> None:
        print("[autotune]", *parts)

    def _xui_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.args.xui_db)

    def _get_latest_setting(self, conn: sqlite3.Connection, key: str) -> tuple[Optional[int], Optional[str]]:
        row = conn.execute(
            "SELECT rowid, value FROM settings WHERE key=? ORDER BY rowid DESC LIMIT 1", (key,)
        ).fetchone()
        if not row:
            return None, None
        return int(row[0]), str(row[1]) if row[1] is not None else ""

    def _set_latest_setting(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        rowid, _ = self._get_latest_setting(conn, key)
        if rowid is not None:
            conn.execute("UPDATE settings SET value=? WHERE rowid=?", (value, rowid))
        else:
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (key, value))

    def _dedupe_keep_latest(self, conn: sqlite3.Connection, key: str) -> None:
        rows = conn.execute("SELECT rowid FROM settings WHERE key=? ORDER BY rowid DESC", (key,)).fetchall()
        for stale in rows[1:]:
            conn.execute("DELETE FROM settings WHERE rowid=?", (int(stale[0]),))

    def _backup_key(self, conn: sqlite3.Connection, key: str) -> None:
        _, value = self._get_latest_setting(conn, key)
        if value is None:
            return
        ts = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?)", (f"{key}_backup_{ts}", value))

    def _load_bot_admin_token(self) -> str:
        if not Path(self.args.bot_db).exists():
            return ""
        conn = sqlite3.connect(self.args.bot_db)
        try:
            row = conn.execute(
                "SELECT value FROM str_config WHERE key='bot_token_admin' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return str(row[0]).strip() if row and row[0] else ""
        finally:
            conn.close()

    def _load_panel_provider(self) -> str:
        if not Path(self.args.bot_db).exists():
            return "3xui"
        conn = sqlite3.connect(self.args.bot_db)
        try:
            row = conn.execute(
                "SELECT value FROM str_config WHERE key='panel_provider' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            provider = str(row[0]).strip().lower() if row and row[0] else "3xui"
            if provider in {"3x-ui", "x-ui"}:
                return "3xui"
            if provider == "marzban":
                return "marzban"
            return "3xui"
        finally:
            conn.close()

    def apply_network_tuning(self) -> None:
        script = Path(self.args.base_dir) / "scripts" / "server_tune_network.py"
        if not script.exists():
            self.log("skip network tuning: script not found", script)
            return
        self.log("run", script)
        proc = run([self.args.python_bin, str(script)])
        print(proc.stdout, end="")
        if proc.returncode != 0:
            raise RuntimeError(f"server_tune_network.py failed: {proc.stderr}")
        self.changed = True

    def apply_inbound_profiles(self) -> None:
        if self.provider == "marzban":
            self.log("skip inbound profiles: provider=marzban")
            return
        script = Path(self.args.base_dir) / "scripts" / "server_apply_nl_profiles.py"
        if not script.exists():
            self.log("skip profile apply: script not found", script)
            return
        self.log("run", script)
        proc = run([self.args.python_bin, str(script)])
        print(proc.stdout, end="")
        if proc.returncode != 0:
            raise RuntimeError(f"server_apply_nl_profiles.py failed: {proc.stderr}")
        self.changed = True

    def apply_warp_routing_template(self) -> None:
        if self.provider == "marzban":
            self.log("skip warp template: provider=marzban")
            return

        if not Path(self.args.xui_db).exists():
            self.log("skip warp template: x-ui db missing", self.args.xui_db)
            return

        conn = self._xui_conn()
        old_template: Optional[str] = None
        try:
            self._backup_key(conn, "xrayTemplateConfig")
            self._backup_key(conn, "xrayOutboundTestUrl")

            _, raw_tpl = self._get_latest_setting(conn, "xrayTemplateConfig")
            if not raw_tpl:
                self.log("skip warp template: xrayTemplateConfig empty")
                return

            cfg = json.loads(raw_tpl)
            old_template = raw_tpl

            outbounds = list(cfg.get("outbounds") or [])
            has_warp = any(
                (o.get("tag") == "warp" and str(o.get("protocol") or "").lower() == "wireguard")
                for o in outbounds
            )
            if not has_warp:
                self.log("skip warp routing: outbound 'warp' not found in template")
                return

            routing = dict(cfg.get("routing") or {})
            rules = [r for r in list(routing.get("rules") or []) if not r.get("_smartkama_hybrid")]

            warp_rule = {
                "type": "field",
                "outboundTag": "warp",
                "domain": WARP_DOMAINS,
                "_smartkama_hybrid": True,
            }

            insert_at = 1 if rules else 0
            rules.insert(insert_at, warp_rule)
            routing["rules"] = rules
            cfg["routing"] = routing

            # This x-ui build does not materialize balancers/observatory from template.
            cfg.pop("balancers", None)
            cfg.pop("observatory", None)

            self._set_latest_setting(conn, "xrayTemplateConfig", json.dumps(cfg, ensure_ascii=False))
            self._set_latest_setting(conn, "xrayOutboundTestUrl", OUTBOUND_TEST_URL)

            for key in [
                "xrayTemplateConfig",
                "xrayOutboundTestUrl",
                "tgBotEnable",
                "tgBotToken",
                "tgBotChatId",
                "tgBotLoginNotify",
                "tgBotBackup",
            ]:
                self._dedupe_keep_latest(conn, key)

            conn.commit()
            self.changed = True
            self.log("xray template updated: selective WARP domains", len(WARP_DOMAINS))
        finally:
            conn.close()

        self.log("restart xray through x-ui")
        proc = run(["x-ui", "restart-xray"])
        print(proc.stdout, end="")
        if proc.returncode != 0:
            self.log("restart-xray failed; rolling back template")
            if old_template is not None:
                conn = self._xui_conn()
                try:
                    self._set_latest_setting(conn, "xrayTemplateConfig", old_template)
                    conn.commit()
                finally:
                    conn.close()
                run(["x-ui", "restart-xray"], check=False)
            raise RuntimeError(f"x-ui restart-xray failed: {proc.stderr}")

    def disable_conflicting_panel_tg(self) -> None:
        if self.provider == "marzban":
            self.log("skip panel tg conflict check: provider=marzban")
            return

        if not Path(self.args.xui_db).exists():
            return
        admin_token = self._load_bot_admin_token()
        if not admin_token:
            return

        conn = self._xui_conn()
        try:
            _, enabled = self._get_latest_setting(conn, "tgBotEnable")
            _, xui_token = self._get_latest_setting(conn, "tgBotToken")

            is_enabled = str(enabled or "").strip().lower() == "true"
            same_token = str(xui_token or "").strip() == admin_token
            if is_enabled and same_token:
                self._set_latest_setting(conn, "tgBotEnable", "false")
                self._dedupe_keep_latest(conn, "tgBotEnable")
                conn.commit()
                self.changed = True
                self.log("disabled panel tg bot: shared token conflict with SmartKama admin bot")
            else:
                self.log("panel tg conflict check: no action needed")
        finally:
            conn.close()

    def restart_xui_if_needed(self) -> None:
        if self.provider == "marzban":
            self.log("skip x-ui restart: provider=marzban")
            return

        if not self.changed:
            self.log("no service restart required")
            return
        self.log("restart x-ui service")
        run(["systemctl", "restart", "x-ui"], check=True)

    def run_guard(self) -> None:
        script = Path(self.args.base_dir) / "scripts" / "server_ops_guard.py"
        if not script.exists():
            self.log("skip guard: script not found", script)
            return
        self.log("run guard", self.args.guard_mode)
        proc = run([self.args.python_bin, str(script), "--mode", self.args.guard_mode])
        print(proc.stdout, end="")
        if proc.returncode != 0:
            raise RuntimeError(f"server_ops_guard.py failed: {proc.stderr}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SmartKama one-click production autotune")
    p.add_argument("--xui-db", default=XUI_DB_DEFAULT)
    p.add_argument("--bot-db", default=BOT_DB_DEFAULT)
    p.add_argument("--base-dir", default=BASE_DIR_DEFAULT)
    p.add_argument("--python-bin", default=PYTHON_BIN_DEFAULT)

    p.add_argument("--apply-network", action="store_true")
    p.add_argument("--apply-inbounds", action="store_true")
    p.add_argument("--apply-warp-routing", action="store_true")
    p.add_argument("--disable-panel-tg-conflict", action="store_true")
    p.add_argument("--run-guard", action="store_true")
    p.add_argument("--guard-mode", choices=["diagnose", "autofix", "smoke", "all"], default="all")

    p.add_argument("--full", action="store_true", help="Enable all safe autotune steps")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.full:
        args.apply_network = True
        args.apply_inbounds = True
        args.apply_warp_routing = True
        args.disable_panel_tg_conflict = True
        args.run_guard = True

    tuner = AutoTuner(args)
    tuner.log("panel provider", tuner.provider)
    try:
        if args.apply_network:
            tuner.apply_network_tuning()
        if args.apply_inbounds:
            tuner.apply_inbound_profiles()
        if args.apply_warp_routing:
            tuner.apply_warp_routing_template()
        if args.disable_panel_tg_conflict:
            tuner.disable_conflicting_panel_tg()

        tuner.restart_xui_if_needed()

        if args.run_guard:
            tuner.run_guard()
    except Exception as exc:
        print("[autotune] ERROR", exc)
        return 1

    print("[autotune] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
