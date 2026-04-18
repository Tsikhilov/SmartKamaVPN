#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

import requests


XUI_DB_DEFAULT = "/etc/x-ui/x-ui.db"
BOT_DB_DEFAULT = "/opt/SmartKamaVPN/Database/smartkamavpn.db"
BASE_DIR_DEFAULT = "/opt/SmartKamaVPN"
PYTHON_BIN_DEFAULT = "/opt/SmartKamaVPN/.venv/bin/python"
POLICY_PATH_DEFAULT = "/opt/SmartKamaVPN/autotune-policy.json"
STATE_PATH_DEFAULT = "/opt/SmartKamaVPN/Logs/autotune-state.json"
TG_API = "https://api.telegram.org"
MARZBAN_LOCAL_SUB_BASE = "http://127.0.0.1:8000/sub"

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


def run(
    cmd: List[str],
    check: bool = False,
    env: Optional[dict[str, str]] = None,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess:
    run_env = None
    if env:
        run_env = os.environ.copy()
        run_env.update(env)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=run_env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").strip()
        stderr = (exc.stderr or "").strip()
        parts = [f"Command timed out after {timeout}s: {' '.join(cmd)}"]
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(stderr)
        raise RuntimeError("\n".join(parts)) from exc
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc


class AutoTuner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.changed = False
        self.started_at = time.perf_counter()
        self.stage_timings: list[tuple[str, float]] = []
        self.current_run_metadata: dict[str, object] = {}
        self.provider = self._load_panel_provider()
        self.policy = self._load_policy()
        self._apply_policy()
        self.guard_sub_id = self._discover_guard_sub_id()

    def log(self, *parts: object) -> None:
        print("[autotune]", *parts)

    def _load_policy(self) -> dict:
        path = Path(self.args.policy_path)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            self.log("WARN: failed to load policy", path, exc)
            return {}

    def _apply_policy(self) -> None:
        if not self.policy:
            return

        bool_keys = [
            "apply_network",
            "apply_inbounds",
            "apply_warp_routing",
            "apply_direct_inbound",
            "apply_mobile_opt",
            "run_mobile_diag",
            "disable_panel_tg_conflict",
            "run_guard",
        ]
        for key in bool_keys:
            if key in self.policy:
                setattr(self.args, key, bool(self.policy.get(key)))

        if self.policy.get("guard_mode") in {"diagnose", "autofix", "smoke", "all"}:
            self.args.guard_mode = str(self.policy["guard_mode"])
        if "guard_retries" in self.policy:
            self.args.guard_retries = int(self.policy["guard_retries"])
        if "guard_retry_delay" in self.policy:
            self.args.guard_retry_delay = int(self.policy["guard_retry_delay"])
        if "guard_ready_timeout_sec" in self.policy:
            self.args.guard_ready_timeout_sec = int(self.policy["guard_ready_timeout_sec"])
        if "guard_ready_interval_sec" in self.policy:
            self.args.guard_ready_interval_sec = int(self.policy["guard_ready_interval_sec"])

    def _extract_sub_id_from_target(self, target_url: str) -> str:
        text = (target_url or "").strip()
        if not text:
            return ""
        path = text.split("?", 1)[0].rstrip("/")
        if not path:
            return ""
        tail = path.rsplit("/", 1)[-1].strip()
        return tail if len(tail) >= 8 else ""

    def _discover_guard_sub_id(self) -> str:
        explicit = (self.args.guard_sub_id or "").strip()
        if explicit:
            self.log("guard sub_id from args", explicit)
            return explicit

        if not Path(self.args.bot_db).exists():
            return ""

        conn = sqlite3.connect(self.args.bot_db)
        try:
            rows = conn.execute(
                "SELECT target_url FROM short_links ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return ""
        finally:
            conn.close()

        for row in rows:
            sub_id = self._extract_sub_id_from_target(str(row[0] or ""))
            if sub_id:
                self.log("guard sub_id discovered", sub_id)
                return sub_id
        return ""

    def _read_latest_bot_value(self, key: str) -> str:
        if not Path(self.args.bot_db).exists():
            return ""
        conn = sqlite3.connect(self.args.bot_db)
        try:
            row = conn.execute(
                "SELECT value FROM str_config WHERE key=? ORDER BY rowid DESC LIMIT 1",
                (key,),
            ).fetchone()
            return str(row[0]).strip() if row and row[0] else ""
        finally:
            conn.close()

    def _parse_chat_id(self, raw: str) -> Optional[int]:
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                data = json.loads(text)
                if isinstance(data, list) and data:
                    return int(data[0])
                return None
            except Exception:
                return None
        first = text.split(",", 1)[0].strip()
        if not first:
            return None
        try:
            return int(first)
        except Exception:
            return None

    def _load_notify_target(self) -> tuple[str, Optional[int]]:
        token = self._read_latest_bot_value("bot_token_admin")
        chat_id = self._parse_chat_id(self._read_latest_bot_value("bot_admin_id"))
        return token, chat_id

    def _state_path(self) -> Path:
        path = Path(self.args.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_state(self) -> dict:
        path = self._state_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.log("WARN: failed to save state", self.args.state_path, exc)

    def _record_runtime_state(self, state: dict) -> None:
        total_elapsed = round(time.perf_counter() - self.started_at, 3)
        state["last_run_elapsed_sec"] = total_elapsed
        state["last_stage_elapsed_sec"] = {
            name: round(elapsed, 3) for name, elapsed in self.stage_timings
        }
        if self.current_run_metadata:
            state["last_run_metadata"] = dict(self.current_run_metadata)
        else:
            state.pop("last_run_metadata", None)
        if self.stage_timings:
            stage_parts = [f"{name}={elapsed:.3f}" for name, elapsed in self.stage_timings]
            self.log("timings", f"total_sec={total_elapsed:.3f}", *stage_parts)
        else:
            self.log("timings", f"total_sec={total_elapsed:.3f}")

    def _history_limit_from_policy(self) -> int:
        value = self.policy.get("performance_history_limit", 20)
        try:
            limit = int(value)
        except Exception:
            limit = 20
        return max(1, limit)

    def _append_run_history(self, state: dict, now: int, success: bool, details: str) -> None:
        history = state.get("run_history")
        if not isinstance(history, list):
            history = []

        entry = {
            "ts": now,
            "status": "ok" if success else "error",
            "total_sec": round(float(state.get("last_run_elapsed_sec") or 0.0), 3),
            "stages": dict(state.get("last_stage_elapsed_sec") or {}),
        }
        if self.current_run_metadata:
            entry["metadata"] = dict(self.current_run_metadata)
            source = self.current_run_metadata.get("nl_profile_probe_source")
            if source:
                entry["nl_profile_probe_source"] = str(source)
        if not success and details:
            entry["error"] = details[:300]

        history.append(entry)
        state["run_history"] = history[-self._history_limit_from_policy():]

    def _normalize_nl_profile_probe_source(self, source: str) -> str:
        value = str(source or "").strip().lower()
        if value in {"cache", "cached"}:
            return "cache"
        if value == "stale-cache":
            return value
        if value == "live":
            return value
        return value

    def _nl_profile_probe_source_from_history_item(self, item: dict) -> str:
        source = self._normalize_nl_profile_probe_source(item.get("nl_profile_probe_source") or "")
        if source:
            return source

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            source = self._normalize_nl_profile_probe_source(metadata.get("nl_profile_probe_source") or "")
            if source:
                return source

        try:
            infer_threshold_sec = float(self.policy.get("performance_infer_live_probe_stage_sec", 10.0))
        except Exception:
            infer_threshold_sec = 10.0
        try:
            infer_cached_threshold_sec = float(self.policy.get("performance_infer_cached_probe_stage_sec", 2.0))
        except Exception:
            infer_cached_threshold_sec = 2.0
        stages = item.get("stages")
        if isinstance(stages, dict):
            try:
                nl_profiles_sec = float(stages.get("nl_profiles") or 0.0)
            except Exception:
                nl_profiles_sec = 0.0
            if nl_profiles_sec >= max(0.0, infer_threshold_sec):
                return "live"
            if 0 < nl_profiles_sec <= max(0.0, infer_cached_threshold_sec):
                return "cache"
        return ""

    def _performance_success_history(self, history: list[dict]) -> list[dict]:
        ignore_live_probe_runs = bool(self.policy.get("performance_ignore_live_probe_runs", True))
        success_history: list[dict] = []
        for item in history:
            if not isinstance(item, dict) or item.get("status") != "ok":
                continue
            if ignore_live_probe_runs and self._nl_profile_probe_source_from_history_item(item) == "live":
                continue
            success_history.append(item)
        return success_history

    def _success_history_for_performance(self, state: dict) -> list[dict]:
        history = state.get("run_history")
        if not isinstance(history, list):
            return []
        return self._performance_success_history(history)

    def _summary_rollup_days_limit(self) -> int:
        value = self.policy.get("summary_rollup_days_limit", 14)
        try:
            limit = int(value)
        except Exception:
            limit = 14
        return max(1, limit)

    def _summary_rollup_version(self) -> int:
        return 3

    def _summary_day_key(self, ts: int) -> str:
        return dt.datetime.utcfromtimestamp(max(0, int(ts))).strftime("%Y-%m-%d")

    def _empty_summary_bucket(self, ts: int) -> dict:
        return {
            "first_ts": ts,
            "last_ts": ts,
            "updated_at": ts,
            "runs": 0,
            "ok_runs": 0,
            "error_runs": 0,
            "performance_ok_runs": 0,
            "live_refresh_runs": 0,
            "probe_source_counts": {},
            "ok_total_count": 0,
            "ok_total_sum": 0.0,
            "ok_total_min": None,
            "ok_total_max": None,
            "performance_total_count": 0,
            "performance_total_sum": 0.0,
            "performance_total_min": None,
            "performance_total_max": None,
            "stage_sum_sec": {},
            "stage_count": {},
        }

    def _accumulate_summary_rollup_item(self, days: dict[str, dict], item: dict) -> None:
        try:
            ts = int(item.get("ts") or 0)
        except Exception:
            ts = 0
        if ts <= 0:
            return

        day_key = self._summary_day_key(ts)
        bucket = days.get(day_key)
        if not isinstance(bucket, dict):
            bucket = self._empty_summary_bucket(ts)
            days[day_key] = bucket

        bucket["first_ts"] = min(int(bucket.get("first_ts") or ts), ts)
        bucket["last_ts"] = max(int(bucket.get("last_ts") or ts), ts)
        bucket["updated_at"] = ts
        bucket["runs"] = int(bucket.get("runs") or 0) + 1

        status = str(item.get("status") or "").strip().lower()
        try:
            total_sec = float(item.get("total_sec") or 0.0)
        except Exception:
            total_sec = 0.0

        if status != "ok":
            bucket["error_runs"] = int(bucket.get("error_runs") or 0) + 1
            return

        bucket["ok_runs"] = int(bucket.get("ok_runs") or 0) + 1
        if total_sec > 0:
            bucket["ok_total_count"] = int(bucket.get("ok_total_count") or 0) + 1
            bucket["ok_total_sum"] = round(float(bucket.get("ok_total_sum") or 0.0) + total_sec, 6)
            current_min = bucket.get("ok_total_min")
            current_max = bucket.get("ok_total_max")
            bucket["ok_total_min"] = total_sec if current_min is None else min(float(current_min), total_sec)
            bucket["ok_total_max"] = total_sec if current_max is None else max(float(current_max), total_sec)

        source = self._nl_profile_probe_source_from_history_item(item) or "unknown"
        probe_source_counts = bucket.get("probe_source_counts")
        if not isinstance(probe_source_counts, dict):
            probe_source_counts = {}
            bucket["probe_source_counts"] = probe_source_counts
        probe_source_counts[source] = int(probe_source_counts.get(source) or 0) + 1
        if source == "live":
            bucket["live_refresh_runs"] = int(bucket.get("live_refresh_runs") or 0) + 1

        ignore_live_probe_runs = bool(self.policy.get("performance_ignore_live_probe_runs", True))
        if ignore_live_probe_runs and source == "live":
            return

        bucket["performance_ok_runs"] = int(bucket.get("performance_ok_runs") or 0) + 1
        if total_sec > 0:
            bucket["performance_total_count"] = int(bucket.get("performance_total_count") or 0) + 1
            bucket["performance_total_sum"] = round(
                float(bucket.get("performance_total_sum") or 0.0) + total_sec,
                6,
            )
            perf_min = bucket.get("performance_total_min")
            perf_max = bucket.get("performance_total_max")
            bucket["performance_total_min"] = total_sec if perf_min is None else min(float(perf_min), total_sec)
            bucket["performance_total_max"] = total_sec if perf_max is None else max(float(perf_max), total_sec)

        stage_sum_sec = bucket.get("stage_sum_sec")
        if not isinstance(stage_sum_sec, dict):
            stage_sum_sec = {}
            bucket["stage_sum_sec"] = stage_sum_sec
        stage_count = bucket.get("stage_count")
        if not isinstance(stage_count, dict):
            stage_count = {}
            bucket["stage_count"] = stage_count

        stages = item.get("stages")
        if not isinstance(stages, dict):
            return
        for stage_name, raw_value in stages.items():
            try:
                stage_sec = float(raw_value)
            except Exception:
                continue
            if stage_sec <= 0:
                continue
            stage_key = str(stage_name)
            stage_sum_sec[stage_key] = round(float(stage_sum_sec.get(stage_key) or 0.0) + stage_sec, 6)
            stage_count[stage_key] = int(stage_count.get(stage_key) or 0) + 1

    def _ensure_summary_rollup(self, state: dict, seed_history: Optional[list[dict]] = None) -> dict[str, dict]:
        rollup = state.get("summary_rollup")
        if not isinstance(rollup, dict):
            rollup = {}
            state["summary_rollup"] = rollup

        rebuild_required = False
        try:
            current_version = int(rollup.get("version") or 0)
        except Exception:
            current_version = 0
        if current_version != self._summary_rollup_version():
            rebuild_required = True

        days = rollup.get("days")
        if rebuild_required or not isinstance(days, dict):
            days = {}
            rollup["days"] = days
            if isinstance(seed_history, list):
                for item in seed_history:
                    if isinstance(item, dict):
                        self._accumulate_summary_rollup_item(days, item)

        for day_key in list(days.keys()):
            if not isinstance(days.get(day_key), dict):
                days.pop(day_key, None)

        for day_key in sorted(days.keys())[:-self._summary_rollup_days_limit()]:
            days.pop(day_key, None)

        rollup["version"] = self._summary_rollup_version()
        rollup["updated_at"] = int(time.time())
        return days

    def _update_summary_rollup(self, state: dict) -> None:
        history = state.get("run_history")
        if not isinstance(history, list) or not history:
            return

        rollup = state.get("summary_rollup")
        if not isinstance(rollup, dict):
            rollup = {}
        try:
            existing_version = int(rollup.get("version") or 0)
        except Exception:
            existing_version = 0
        rebuild_required = existing_version != self._summary_rollup_version()

        days = self._ensure_summary_rollup(state, history[:-1])
        last_item = history[-1]
        if not isinstance(last_item, dict):
            return

        last_rollup_ts = 0 if rebuild_required else int(state.get("summary_rollup_last_ts") or 0)
        try:
            item_ts = int(last_item.get("ts") or 0)
        except Exception:
            item_ts = 0
        if item_ts <= 0 or item_ts == last_rollup_ts:
            return

        self._accumulate_summary_rollup_item(days, last_item)
        state["summary_rollup_last_ts"] = item_ts

    def _summary_rollup_window(self, state: dict, now: int, window_sec: int) -> list[dict]:
        days = ((state.get("summary_rollup") or {}).get("days") or {})
        if not isinstance(days, dict):
            return []

        cutoff = now - max(0, int(window_sec)) if window_sec > 0 else 0
        buckets: list[dict] = []
        for bucket in days.values():
            if not isinstance(bucket, dict):
                continue
            try:
                first_ts = int(bucket.get("first_ts") or 0)
                last_ts = int(bucket.get("last_ts") or 0)
            except Exception:
                continue
            if last_ts <= 0:
                continue
            if cutoff and last_ts < cutoff:
                continue
            if first_ts > now:
                continue
            buckets.append(bucket)
        return sorted(buckets, key=lambda item: int(item.get("first_ts") or 0))

    def _summary_status(self, state: dict, now: int) -> dict:
        status = {
            "enabled": bool(self.policy.get("summary_enabled", True)),
            "eligible": False,
            "reason": "disabled",
        }
        if not status["enabled"]:
            return status

        interval_sec = int(self.policy.get("summary_interval_sec", 86400))
        window_sec = int(self.policy.get("summary_window_sec", interval_sec))
        min_runs = int(self.policy.get("summary_min_runs", 6))
        min_coverage_sec = int(self.policy.get("summary_min_coverage_sec", 43200))
        last_summary_at = int(state.get("last_summary_notified_at") or 0)
        rollup_window = self._summary_rollup_window(state, now, window_sec)
        total_runs = sum(int(bucket.get("runs") or 0) for bucket in rollup_window)

        first_ts = min((int(bucket.get("first_ts") or 0) for bucket in rollup_window), default=0)
        last_ts = max((int(bucket.get("last_ts") or 0) for bucket in rollup_window), default=0)
        coverage_sec = max(0, last_ts - first_ts) if first_ts and last_ts else 0
        next_eligible_at = last_summary_at + interval_sec if last_summary_at else 0

        status.update(
            {
                "interval_sec": interval_sec,
                "window_sec": window_sec,
                "min_runs": min_runs,
                "min_coverage_sec": min_coverage_sec,
                "last_summary_at": last_summary_at,
                "next_eligible_at": next_eligible_at,
                "window_runs": total_runs,
                "coverage_sec": coverage_sec,
                "window_first_ts": first_ts,
                "window_last_ts": last_ts,
                "runs_remaining": max(0, min_runs - total_runs),
                "coverage_remaining_sec": max(0, min_coverage_sec - coverage_sec),
            }
        )

        if last_summary_at and now - last_summary_at < interval_sec:
            status["reason"] = "interval"
            return status
        if not rollup_window:
            status["reason"] = "no_rollup_window"
            return status
        if total_runs < min_runs:
            status["reason"] = "min_runs"
            return status
        if coverage_sec < max(0, min_coverage_sec):
            status["reason"] = "min_coverage"
            return status

        performance_total_count = sum(int(bucket.get("performance_total_count") or 0) for bucket in rollup_window)
        if performance_total_count <= 0:
            status["reason"] = "no_performance_data"
            return status

        status["eligible"] = True
        status["reason"] = "ready"
        return status

    def _record_summary_status(self, state: dict, now: int) -> None:
        summary_status = self._summary_status(state, now)
        state["summary_status"] = summary_status
        if summary_status.get("enabled"):
            self.log(
                "summary status",
                f"reason={summary_status.get('reason')}",
                f"eligible={str(bool(summary_status.get('eligible'))).lower()}",
                f"window_runs={int(summary_status.get('window_runs') or 0)}",
                f"coverage_sec={int(summary_status.get('coverage_sec') or 0)}",
            )

    def _build_summary_report(self, state: dict, now: int, summary_status: Optional[dict] = None) -> Optional[dict]:
        summary_status = summary_status or self._summary_status(state, now)
        if not summary_status.get("enabled"):
            return None

        interval_sec = int(summary_status.get("interval_sec") or self.policy.get("summary_interval_sec", 86400))
        window_sec = int(summary_status.get("window_sec") or interval_sec)
        rollup_window = self._summary_rollup_window(state, now, window_sec)
        if not rollup_window:
            return None

        total_runs = int(summary_status.get("window_runs") or 0)
        coverage_sec = int(summary_status.get("coverage_sec") or 0)
        source_counts: dict[str, int] = {}
        stage_sum_sec: dict[str, float] = {}
        stage_count: dict[str, int] = {}
        live_refresh_runs = 0
        ok_runs = 0
        error_runs = 0
        performance_ok_runs = 0
        ok_total_max: float | None = None
        performance_total_sum = 0.0
        performance_total_count = 0
        performance_total_min: float | None = None

        for bucket in rollup_window:
            ok_runs += int(bucket.get("ok_runs") or 0)
            error_runs += int(bucket.get("error_runs") or 0)
            performance_ok_runs += int(bucket.get("performance_ok_runs") or 0)
            live_refresh_runs += int(bucket.get("live_refresh_runs") or 0)

            for name, count in dict(bucket.get("probe_source_counts") or {}).items():
                source_counts[str(name)] = source_counts.get(str(name), 0) + int(count or 0)

            bucket_ok_max = bucket.get("ok_total_max")
            if bucket_ok_max is not None:
                ok_max = float(bucket_ok_max)
                ok_total_max = ok_max if ok_total_max is None else max(ok_total_max, ok_max)

            performance_total_sum += float(bucket.get("performance_total_sum") or 0.0)
            performance_total_count += int(bucket.get("performance_total_count") or 0)
            bucket_perf_min = bucket.get("performance_total_min")
            if bucket_perf_min is not None:
                perf_min = float(bucket_perf_min)
                performance_total_min = perf_min if performance_total_min is None else min(performance_total_min, perf_min)

            for stage_name, raw_sum in dict(bucket.get("stage_sum_sec") or {}).items():
                stage_key = str(stage_name)
                stage_sum_sec[stage_key] = stage_sum_sec.get(stage_key, 0.0) + float(raw_sum or 0.0)
            for stage_name, raw_count in dict(bucket.get("stage_count") or {}).items():
                stage_key = str(stage_name)
                stage_count[stage_key] = stage_count.get(stage_key, 0) + int(raw_count or 0)

        if performance_total_count <= 0 or performance_total_min is None:
            return None

        stage_averages = {
            stage_name: (stage_sum_sec[stage_name] / stage_count[stage_name])
            for stage_name in stage_sum_sec.keys()
            if stage_count.get(stage_name)
        }
        slowest_stage = ""
        slowest_stage_sec = 0.0
        if stage_averages:
            slowest_stage, slowest_stage_sec = max(stage_averages.items(), key=lambda item: item[1])

        baseline = state.get("performance_baseline")
        baseline_total_sec = 0.0
        if isinstance(baseline, dict):
            try:
                baseline_total_sec = float(baseline.get("total_median_sec") or 0.0)
            except Exception:
                baseline_total_sec = 0.0

        last_total_sec = round(float(state.get("last_run_elapsed_sec") or 0.0), 3)
        avg_total_sec = performance_total_sum / performance_total_count
        source_summary = ", ".join(
            f"{name}:{count}" for name, count in sorted(source_counts.items()) if count > 0
        ) or "none"

        message = (
            "SmartKama autotune summary\n"
            f"provider={self.provider}\n"
            f"guard_mode={self.args.guard_mode}\n"
            f"window_runs={total_runs}\n"
            f"coverage_sec={coverage_sec}\n"
            f"ok_runs={ok_runs}\n"
            f"error_runs={error_runs}\n"
            f"performance_ok_runs={performance_ok_runs}\n"
            f"live_refresh_runs={live_refresh_runs}\n"
            f"probe_sources={source_summary}\n"
            f"last_total_sec={last_total_sec:.3f}\n"
            f"avg_total_sec={avg_total_sec:.3f}\n"
            f"best_total_sec={performance_total_min:.3f}\n"
            f"worst_total_sec={float(ok_total_max or performance_total_min):.3f}"
        )
        snapshot = {
            "ts": now,
            "source": "rollup",
            "window_runs": total_runs,
            "coverage_sec": coverage_sec,
            "ok_runs": ok_runs,
            "error_runs": error_runs,
            "performance_ok_runs": performance_ok_runs,
            "live_refresh_runs": live_refresh_runs,
            "probe_source_counts": dict(sorted(source_counts.items())),
            "last_total_sec": last_total_sec,
            "avg_total_sec": round(avg_total_sec, 3),
            "best_total_sec": round(performance_total_min, 3),
            "worst_total_sec": round(float(ok_total_max or performance_total_min), 3),
        }
        if baseline_total_sec > 0:
            message += f"\nbaseline_total_sec={baseline_total_sec:.3f}"
            snapshot["baseline_total_sec"] = round(baseline_total_sec, 3)
        if slowest_stage:
            message += f"\nslowest_stage_avg={slowest_stage}:{slowest_stage_sec:.3f}"
            snapshot["slowest_stage_avg"] = {
                "stage": slowest_stage,
                "sec": round(slowest_stage_sec, 3),
            }
        return {
            "message": message,
            "snapshot": snapshot,
        }

    def _record_summary_preview(self, state: dict, now: int) -> None:
        summary_status = state.get("summary_status")
        if not isinstance(summary_status, dict):
            summary_status = self._summary_status(state, now)
            state["summary_status"] = summary_status
        report = self._build_summary_report(state, now, summary_status)
        if not report:
            state.pop("summary_preview", None)
            return
        preview = dict(report["snapshot"])
        preview["reason"] = str(summary_status.get("reason") or "")
        preview["eligible"] = bool(summary_status.get("eligible"))
        preview["message"] = report["message"]
        state["summary_preview"] = preview

    def _history_window(self, state: dict, now: int, window_sec: int) -> list[dict]:
        history = state.get("run_history")
        if not isinstance(history, list):
            return []

        items = [item for item in history if isinstance(item, dict)]
        if not items:
            return []
        if window_sec <= 0:
            return items

        cutoff = now - window_sec
        filtered: list[dict] = []
        for item in items:
            try:
                ts = int(item.get("ts") or 0)
            except Exception:
                ts = 0
            if ts >= cutoff:
                filtered.append(item)
        return filtered

    def _maybe_notify_periodic_summary(self, state: dict, now: int) -> bool:
        summary_status = self._summary_status(state, now)
        state["summary_status"] = summary_status
        if not summary_status.get("enabled"):
            return False

        if not summary_status.get("eligible"):
            return False

        report = self._build_summary_report(state, now, summary_status)
        if not report:
            return False

        interval_sec = int(summary_status.get("interval_sec") or self.policy.get("summary_interval_sec", 86400))
        total_runs = int(summary_status.get("window_runs") or 0)
        if self._send_admin_notification(report["message"]):
            state["last_summary_notified_at"] = now
            state["last_summary_snapshot"] = dict(report["snapshot"])
            summary_status["eligible"] = False
            summary_status["reason"] = "interval"
            summary_status["last_summary_at"] = now
            summary_status["next_eligible_at"] = now + interval_sec
            summary_status["runs_remaining"] = max(0, int(summary_status.get("min_runs") or 0) - total_runs)
            summary_status["coverage_remaining_sec"] = 0
            state["summary_status"] = summary_status
            self._record_summary_preview(state, now)
            return True
        return False

    def _refresh_performance_baseline(self, state: dict, now: int) -> None:
        success_history = self._success_history_for_performance(state)
        if not success_history:
            state.pop("performance_baseline", None)
            return
        total_values: list[float] = []
        stage_values: dict[str, list[float]] = {}

        for item in success_history:
            try:
                total_sec = float(item.get("total_sec") or 0.0)
            except Exception:
                total_sec = 0.0
            if total_sec > 0:
                total_values.append(total_sec)

            stages = item.get("stages")
            if not isinstance(stages, dict):
                continue
            for stage_name, raw_value in stages.items():
                try:
                    stage_sec = float(raw_value)
                except Exception:
                    continue
                if stage_sec <= 0:
                    continue
                stage_values.setdefault(str(stage_name), []).append(stage_sec)

        if not total_values:
            state.pop("performance_baseline", None)
            return

        stage_median_sec = {
            stage_name: round(statistics.median(values), 3)
            for stage_name, values in sorted(stage_values.items())
            if values
        }
        state["performance_baseline"] = {
            "updated_at": now,
            "sample_count": len(total_values),
            "total_median_sec": round(statistics.median(total_values), 3),
            "stage_median_sec": stage_median_sec,
        }

    def _recent_metric_window(
        self,
        success_history: list[dict],
        metric_getter,
        min_runs: int,
        consecutive_runs: int,
    ) -> tuple[float, list[float]] | None:
        if len(success_history) < max(min_runs, consecutive_runs + 1):
            return None

        recent = success_history[-consecutive_runs:]
        baseline_source = success_history[:-consecutive_runs]
        required_baseline = max(1, min_runs - consecutive_runs)
        if len(baseline_source) < required_baseline:
            return None

        baseline_values: list[float] = []
        for item in baseline_source:
            try:
                value = float(metric_getter(item) or 0.0)
            except Exception:
                value = 0.0
            if value > 0:
                baseline_values.append(value)
        if len(baseline_values) < required_baseline:
            return None

        recent_values: list[float] = []
        for item in recent:
            try:
                value = float(metric_getter(item) or 0.0)
            except Exception:
                value = 0.0
            if value <= 0:
                return None
            recent_values.append(value)

        baseline_value = statistics.median(baseline_values)
        if baseline_value <= 0:
            return None
        return baseline_value, recent_values

    def _maybe_notify_slow_performance(self, state: dict, now: int) -> bool:
        if not self.policy.get("performance_alert_enabled", True):
            return False

        success_history = self._success_history_for_performance(state)
        if not success_history:
            return False

        min_runs = int(self.policy.get("performance_alert_min_runs", 6))
        consecutive_runs = int(self.policy.get("performance_alert_consecutive_runs", 2))
        min_total_sec = float(self.policy.get("performance_alert_min_total_sec", 2.0))
        factor = float(self.policy.get("performance_alert_factor", 2.5))
        min_interval_sec = int(self.policy.get("performance_alert_min_interval_sec", 21600))

        metric_window = self._recent_metric_window(
            success_history,
            lambda item: item.get("total_sec"),
            min_runs,
            consecutive_runs,
        )
        if metric_window is None:
            return False
        baseline_sec, recent_values = metric_window

        threshold_sec = max(min_total_sec, baseline_sec * factor)
        if not recent_values or any(total < threshold_sec for total in recent_values):
            return False

        last_alert_at = int(state.get("last_performance_alert_at") or 0)
        if now - last_alert_at < min_interval_sec:
            return False

        current_stages = state.get("last_stage_elapsed_sec") or {}
        slowest_stage = ""
        slowest_elapsed = 0.0
        if isinstance(current_stages, dict):
            for name, value in current_stages.items():
                try:
                    elapsed = float(value)
                except Exception:
                    continue
                if elapsed >= slowest_elapsed:
                    slowest_stage = str(name)
                    slowest_elapsed = elapsed

        message = (
            "SmartKama autotune slowdown\n"
            f"provider={self.provider}\n"
            f"guard_mode={self.args.guard_mode}\n"
            f"total_sec={recent_values[-1]:.3f}\n"
            f"baseline_sec={baseline_sec:.3f}\n"
            f"threshold_sec={threshold_sec:.3f}\n"
            f"window={consecutive_runs}/{consecutive_runs}"
        )
        if slowest_stage:
            message += f"\nslowest_stage={slowest_stage}:{slowest_elapsed:.3f}"

        if self._send_admin_notification(message):
            state["last_performance_alert_at"] = now
            state["last_performance_alert_total_sec"] = round(recent_values[-1], 3)
            return True
        return False

    def _maybe_notify_slow_stage_performance(self, state: dict, now: int) -> bool:
        if not self.policy.get("stage_performance_alert_enabled", True):
            return False

        success_history = self._success_history_for_performance(state)
        if not success_history:
            return False

        min_runs = int(self.policy.get("stage_performance_alert_min_runs", 6))
        consecutive_runs = int(self.policy.get("stage_performance_alert_consecutive_runs", 2))
        min_stage_sec = float(self.policy.get("stage_performance_alert_min_stage_sec", 0.3))
        factor = float(self.policy.get("stage_performance_alert_factor", 2.0))
        min_interval_sec = int(self.policy.get("stage_performance_alert_min_interval_sec", 21600))

        current_stages = state.get("last_stage_elapsed_sec")
        if not isinstance(current_stages, dict) or not current_stages:
            return False

        last_alerts = state.get("last_stage_performance_alert_at")
        if not isinstance(last_alerts, dict):
            last_alerts = {}
            state["last_stage_performance_alert_at"] = last_alerts

        best_candidate: tuple[str, float, float, float] | None = None
        for stage_name in current_stages:
            stage_key = str(stage_name)
            metric_window = self._recent_metric_window(
                success_history,
                lambda item, key=stage_key: (item.get("stages") or {}).get(key),
                min_runs,
                consecutive_runs,
            )
            if metric_window is None:
                continue
            baseline_sec, recent_values = metric_window
            threshold_sec = max(min_stage_sec, baseline_sec * factor)
            if any(value < threshold_sec for value in recent_values):
                continue

            try:
                last_stage_alert_at = int(last_alerts.get(stage_key) or 0)
            except Exception:
                last_stage_alert_at = 0
            if now - last_stage_alert_at < min_interval_sec:
                continue

            candidate = (stage_key, recent_values[-1], baseline_sec, threshold_sec)
            if best_candidate is None or (candidate[1] / max(candidate[2], 0.001)) > (
                best_candidate[1] / max(best_candidate[2], 0.001)
            ):
                best_candidate = candidate

        if best_candidate is None:
            return False

        stage_name, current_sec, baseline_sec, threshold_sec = best_candidate
        total_sec = round(float(state.get("last_run_elapsed_sec") or 0.0), 3)
        message = (
            "SmartKama autotune stage slowdown\n"
            f"provider={self.provider}\n"
            f"guard_mode={self.args.guard_mode}\n"
            f"stage={stage_name}\n"
            f"stage_sec={current_sec:.3f}\n"
            f"stage_baseline_sec={baseline_sec:.3f}\n"
            f"stage_threshold_sec={threshold_sec:.3f}\n"
            f"run_total_sec={total_sec:.3f}\n"
            f"window={consecutive_runs}/{consecutive_runs}"
        )
        if self._send_admin_notification(message):
            last_alerts[stage_name] = now
            state["last_stage_performance_alert_sec"] = {
                **dict(state.get("last_stage_performance_alert_sec") or {}),
                stage_name: round(current_sec, 3),
            }
            return True
        return False

    def _run_stage(self, name: str, fn) -> None:
        self.log("stage start", name)
        started = time.perf_counter()
        status = "ok"
        try:
            fn()
        except Exception as exc:
            status = "error"
            raise RuntimeError(f"{name} stage failed: {exc}") from exc
        finally:
            elapsed = time.perf_counter() - started
            self.stage_timings.append((name, elapsed))
            self.log("stage done", name, f"status={status}", f"elapsed_sec={elapsed:.3f}")

    def _timeout_from_policy(self, key: str, default: int) -> int:
        value = self.policy.get(key, default)
        try:
            timeout = int(value)
        except Exception:
            timeout = default
        return max(1, timeout)

    def _nl_profile_env(self) -> dict[str, str]:
        env_map = {
            "nl_profile_probe_cache_ttl_sec": "SMARTKAMA_PROBE_CACHE_TTL_SEC",
            "nl_profile_probe_cache_stale_ttl_sec": "SMARTKAMA_PROBE_CACHE_STALE_TTL_SEC",
            "nl_profile_probe_max_workers": "SMARTKAMA_PROBE_MAX_WORKERS",
            "nl_profile_sticky_primary_delta_ms": "SMARTKAMA_PROBE_STICKY_PRIMARY_DELTA_MS",
        }
        env: dict[str, str] = {}
        for policy_key, env_key in env_map.items():
            value = self.policy.get(policy_key)
            if value is None:
                continue
            env[env_key] = str(value)
        return env

    def _capture_nl_profile_metadata(self, stdout: str) -> None:
        source_counts: dict[str, int] = {}
        for raw_line in (stdout or "").splitlines():
            line = raw_line.strip()
            if not line.startswith("sni_probe "):
                continue
            source = "live"
            marker = " source "
            if marker in line:
                tail = line.split(marker, 1)[1].strip()
                token = tail.split(" ", 1)[0].strip()
                if token:
                    source = token
            source_counts[source] = source_counts.get(source, 0) + 1

        if not source_counts:
            self.current_run_metadata.pop("nl_profile_probe_source", None)
            self.current_run_metadata.pop("nl_profile_probe_source_counts", None)
            return

        if len(source_counts) == 1:
            source = next(iter(source_counts))
        elif "live" in source_counts:
            source = "live"
        elif "stale-cache" in source_counts:
            source = "stale-cache"
        elif "cache" in source_counts:
            source = "cache"
        else:
            source = "mixed"

        self.current_run_metadata["nl_profile_probe_source"] = source
        self.current_run_metadata["nl_profile_probe_source_counts"] = dict(sorted(source_counts.items()))
        self.log(
            "nl profile source",
            source,
            *(f"{name}={count}" for name, count in sorted(source_counts.items())),
        )

    def _send_admin_notification(self, text: str) -> bool:
        token, chat_id = self._load_notify_target()
        if not token or chat_id is None:
            self.log("skip admin notify: token/chat_id missing")
            return False
        try:
            resp = requests.post(
                f"{TG_API}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_notification": True},
                timeout=15,
            )
            if resp.status_code == 200 and (resp.json() if resp.content else {}).get("ok"):
                self.log("admin notify sent")
                return True
            self.log("WARN: admin notify failed", resp.status_code, resp.text[:300])
            return False
        except Exception as exc:
            self.log("WARN: admin notify error", exc)
            return False

    def _handle_outcome(self, success: bool, details: str) -> None:
        state = self._load_state()
        now = int(time.time())
        last_status = str(state.get("status") or "unknown")
        last_notified_at = int(state.get("last_notified_at") or 0)
        previous_error = str(state.get("last_error") or "")
        notify_interval = int(self.policy.get("min_notify_interval_sec", 3600))
        self._record_runtime_state(state)

        if success:
            state["status"] = "ok"
            state["last_success_at"] = now
            state.pop("last_error", None)
            state.pop("last_error_at", None)
            self._append_run_history(state, now, True, "")
            self._update_summary_rollup(state)
            self._refresh_performance_baseline(state, now)
            self._record_summary_status(state, now)
            self._record_summary_preview(state, now)
            total_alert_sent = self._maybe_notify_slow_performance(state, now)
            stage_alert_sent = False
            if not total_alert_sent:
                stage_alert_sent = self._maybe_notify_slow_stage_performance(state, now)
            recovery_sent = False
            if last_status == "error" and self.policy.get("notify_admin_on_recovery", True):
                recovery_sent = self._send_admin_notification(
                    "SmartKama autotune recovered\n"
                    f"provider={self.provider}\n"
                    f"guard_mode={self.args.guard_mode}"
                )
                if recovery_sent:
                    state["last_notified_at"] = now
            if not (total_alert_sent or stage_alert_sent or recovery_sent):
                self._maybe_notify_periodic_summary(state, now)
            self._save_state(state)
            return

        state["status"] = "error"
        state["last_error_at"] = now
        state["last_error"] = details[:4000]
        self._append_run_history(state, now, False, details)
        self._update_summary_rollup(state)
        self._record_summary_status(state, now)
        self._record_summary_preview(state, now)
        should_notify = False
        if self.policy.get("notify_admin_on_failure", True):
            if last_status != "error":
                should_notify = True
            elif details != previous_error:
                should_notify = True
            elif now - last_notified_at >= notify_interval:
                should_notify = True

        if should_notify:
            sent = self._send_admin_notification(
                "SmartKama autotune failure\n"
                f"provider={self.provider}\n"
                f"guard_mode={self.args.guard_mode}\n"
                f"details={details[:3000]}"
            )
            if sent:
                state["last_notified_at"] = now
        self._save_state(state)

    def _wait_for_guard_ready(self, sub_id: str) -> bool:
        if not sub_id or self.provider != "marzban":
            return True

        deadline = time.time() + max(1, int(self.args.guard_ready_timeout_sec))
        wait_interval = max(1, int(self.args.guard_ready_interval_sec))
        url = f"{MARZBAN_LOCAL_SUB_BASE}/{sub_id}"
        last_pending_log_at = 0.0
        pending_count = 0

        while time.time() < deadline:
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    if pending_count:
                        self.log("guard target recovered", sub_id, f"checks={pending_count}")
                    self.log("guard target ready", sub_id)
                    return True
                pending_count += 1
                now = time.time()
                if pending_count == 1 or now - last_pending_log_at >= 15:
                    self.log("guard target pending", sub_id, f"status={resp.status_code}", f"checks={pending_count}")
                    last_pending_log_at = now
            except Exception as exc:
                pending_count += 1
                now = time.time()
                if pending_count == 1 or now - last_pending_log_at >= 15:
                    self.log("guard target pending", sub_id, exc, f"checks={pending_count}")
                    last_pending_log_at = now
            time.sleep(wait_interval)

        self.log("WARN: guard target readiness timeout", sub_id)
        return False

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
        return self._read_latest_bot_value("bot_token_admin")

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
        proc = run(
            [self.args.python_bin, str(script)],
            timeout=self._timeout_from_policy("network_timeout_sec", 30),
        )
        print(proc.stdout, end="")
        if proc.returncode != 0:
            raise RuntimeError(f"server_tune_network.py failed: {proc.stderr}")
        self.changed = True

    def apply_inbound_profiles(self) -> None:
        script = Path(self.args.base_dir) / "scripts" / "server_apply_nl_profiles.py"
        if not script.exists():
            self.log("skip profile apply: script not found", script)
            return
        self.log("run", script)
        env = self._nl_profile_env()
        if env:
            self.log(
                "nl profile policy",
                *(f"{key.split('SMARTKAMA_PROBE_')[-1].lower()}={value}" for key, value in env.items()),
            )
        proc = run(
            [self.args.python_bin, str(script)],
            env=env,
            timeout=self._timeout_from_policy("nl_profile_timeout_sec", 120),
        )
        print(proc.stdout, end="")
        self._capture_nl_profile_metadata(proc.stdout)
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
        proc = run(
            ["x-ui", "restart-xray"],
            timeout=self._timeout_from_policy("restart_xui_timeout_sec", 60),
        )
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
                run(
                    ["x-ui", "restart-xray"],
                    check=False,
                    timeout=self._timeout_from_policy("restart_xui_timeout_sec", 60),
                )
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

    def apply_direct_inbound(self) -> None:
        """Add VLESS TCP+TLS direct inbound for max speed (no masking)."""
        script = Path(self.args.base_dir) / "scripts" / "server_add_direct_inbound.py"
        if not script.exists():
            self.log("skip direct inbound: script not found", script)
            return
        self.log("run", script)
        proc = run(
            [self.args.python_bin, str(script), "--xui-db", self.args.xui_db],
            timeout=self._timeout_from_policy("direct_inbound_timeout_sec", 60),
        )
        print(proc.stdout, end="")
        if proc.returncode != 0:
            self.log("WARN: direct inbound script exit", proc.returncode)
        else:
            self.changed = True

    def apply_mobile_optimisation(self) -> None:
        """Apply mobile transport optimisations (sysctl, xray template, inbounds)."""
        script = Path(self.args.base_dir) / "scripts" / "server_optimize_mobile_transport.py"
        if not script.exists():
            self.log("skip mobile optimisation: script not found", script)
            return
        self.log("run", script)
        proc = run(
            [self.args.python_bin, str(script), "--xui-db", self.args.xui_db],
            timeout=self._timeout_from_policy("mobile_opt_timeout_sec", 60),
        )
        print(proc.stdout, end="")
        if proc.returncode != 0:
            self.log("WARN: mobile optimisation exit", proc.returncode)
        else:
            self.changed = True

    def run_mobile_diagnostics(self) -> None:
        """Run mobile connectivity diagnostics (non-blocking, informational)."""
        script = Path(self.args.base_dir) / "scripts" / "server_diagnose_mobile.py"
        if not script.exists():
            self.log("skip mobile diagnostics: script not found", script)
            return
        self.log("run mobile diag")
        proc = run(
            [self.args.python_bin, str(script)],
            timeout=self._timeout_from_policy("mobile_diag_timeout_sec", 60),
        )
        print(proc.stdout, end="")

    def run_guard(self) -> None:
        script = Path(self.args.base_dir) / "scripts" / "server_ops_guard.py"
        if not script.exists():
            self.log("skip guard: script not found", script)
            return

        guard_sub_id = (self.guard_sub_id or "").strip()
        for attempt in range(1, self.args.guard_retries + 1):
            if guard_sub_id and not self._wait_for_guard_ready(guard_sub_id):
                if attempt < self.args.guard_retries:
                    self.log("guard readiness timeout, retry after sec", self.args.guard_retry_delay)
                    time.sleep(self.args.guard_retry_delay)
                    continue
                raise RuntimeError(f"guard readiness timeout for sub_id={guard_sub_id}")

            self.log("run guard", self.args.guard_mode, f"attempt={attempt}/{self.args.guard_retries}")
            cmd = [self.args.python_bin, str(script), "--mode", self.args.guard_mode]
            if guard_sub_id:
                cmd.extend(["--sub-id", guard_sub_id])
            proc = run(
                cmd,
                timeout=self._timeout_from_policy("guard_timeout_sec", 120),
            )
            print(proc.stdout, end="")
            if proc.returncode == 0:
                return

            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            transient = any(marker in (stdout + "\n" + stderr) for marker in [
                "Connection refused",
                "Failed to establish a new connection",
                "sub_id_missing",
                "smoke_subid_missing",
            ])
            if attempt < self.args.guard_retries and transient:
                self.log("guard transient failure, retry after sec", self.args.guard_retry_delay)
                time.sleep(self.args.guard_retry_delay)
                continue

            raise RuntimeError(f"server_ops_guard.py failed: {stderr or stdout}")

    # ── MTProto proxy health check ──────────────────────────────
    def check_mtproto_health(self) -> None:
        """TCP probe to MTProto proxy port (if enabled in policy)."""
        if not self.policy.get("mtproto_health_check"):
            self.log("skip mtproto health: disabled in policy")
            return
        import socket
        host = "127.0.0.1"
        port = 3128
        timeout = self.policy.get("mtproto_health_timeout_sec", 10)
        self.log("mtproto health check", f"{host}:{port}", f"timeout={timeout}s")
        try:
            with socket.create_connection((host, port), timeout=timeout):
                self.log("mtproto health: OK")
        except OSError as exc:
            self.log("mtproto health: FAIL", str(exc))
            # Non-fatal — just log, don't raise

    # ── WhatsApp proxy health check ─────────────────────────────
    def check_whatsapp_proxy_health(self) -> None:
        """TCP probe to WhatsApp proxy port (if enabled in policy)."""
        if not self.policy.get("whatsapp_proxy_health_check"):
            self.log("skip whatsapp proxy health: disabled in policy")
            return
        import socket
        host = "127.0.0.1"
        port = 5222
        timeout = self.policy.get("whatsapp_proxy_health_timeout_sec", 10)
        self.log("whatsapp proxy health check", f"{host}:{port}", f"timeout={timeout}s")
        try:
            with socket.create_connection((host, port), timeout=timeout):
                self.log("whatsapp proxy health: OK")
        except OSError as exc:
            self.log("whatsapp proxy health: FAIL", str(exc))
            # Non-fatal — just log, don't raise

    # ── Signal proxy health check ──────────────────────────────
    def check_signal_proxy_health(self) -> None:
        """TCP probe to Signal TLS proxy port 443 (if enabled in policy)."""
        if not self.policy.get("signal_proxy_health_check"):
            self.log("skip signal proxy health: disabled in policy")
            return
        import socket
        host = "127.0.0.1"
        port = 443
        timeout = self.policy.get("signal_proxy_health_timeout_sec", 10)
        self.log("signal proxy health check", f"{host}:{port}", f"timeout={timeout}s")
        try:
            with socket.create_connection((host, port), timeout=timeout):
                self.log("signal proxy health: OK")
        except OSError as exc:
            self.log("signal proxy health: FAIL", str(exc))
            # Non-fatal — just log, don't raise


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SmartKama one-click production autotune")
    p.add_argument("--xui-db", default=XUI_DB_DEFAULT)
    p.add_argument("--bot-db", default=BOT_DB_DEFAULT)
    p.add_argument("--base-dir", default=BASE_DIR_DEFAULT)
    p.add_argument("--python-bin", default=PYTHON_BIN_DEFAULT)
    p.add_argument("--policy-path", default=POLICY_PATH_DEFAULT)
    p.add_argument("--state-path", default=STATE_PATH_DEFAULT)

    p.add_argument("--apply-network", action="store_true")
    p.add_argument("--apply-inbounds", action="store_true")
    p.add_argument("--apply-warp-routing", action="store_true")
    p.add_argument("--apply-direct-inbound", action="store_true")
    p.add_argument("--apply-mobile-opt", action="store_true")
    p.add_argument("--run-mobile-diag", action="store_true")
    p.add_argument("--disable-panel-tg-conflict", action="store_true")
    p.add_argument("--run-guard", action="store_true")
    p.add_argument("--guard-mode", choices=["diagnose", "autofix", "smoke", "all"], default="all")
    p.add_argument("--guard-retries", type=int, default=3)
    p.add_argument("--guard-retry-delay", type=int, default=10)
    p.add_argument("--guard-sub-id", default="")
    p.add_argument("--guard-ready-timeout-sec", type=int, default=90)
    p.add_argument("--guard-ready-interval-sec", type=int, default=3)

    p.add_argument("--full", action="store_true", help="Enable all safe autotune steps")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.full:
        args.apply_network = True
        args.apply_inbounds = True
        args.apply_warp_routing = True
        args.apply_direct_inbound = True
        args.apply_mobile_opt = True
        args.run_mobile_diag = True
        args.disable_panel_tg_conflict = True
        args.run_guard = True

    tuner = AutoTuner(args)
    tuner.log("panel provider", tuner.provider)
    try:
        if args.apply_network:
            tuner._run_stage("network", tuner.apply_network_tuning)
        if args.apply_inbounds:
            tuner._run_stage("nl_profiles", tuner.apply_inbound_profiles)
        if args.apply_warp_routing:
            tuner._run_stage("warp_routing", tuner.apply_warp_routing_template)
        if args.apply_direct_inbound:
            tuner._run_stage("direct_inbound", tuner.apply_direct_inbound)
        if args.apply_mobile_opt:
            tuner._run_stage("mobile_opt", tuner.apply_mobile_optimisation)
        if args.disable_panel_tg_conflict:
            tuner._run_stage("disable_panel_tg_conflict", tuner.disable_conflicting_panel_tg)

        tuner._run_stage("restart_xui_if_needed", tuner.restart_xui_if_needed)

        if args.run_mobile_diag:
            tuner._run_stage("mobile_diag", tuner.run_mobile_diagnostics)
        if args.run_guard:
            tuner._run_stage("guard", tuner.run_guard)
        tuner._run_stage("mtproto_health", tuner.check_mtproto_health)
        tuner._run_stage("whatsapp_proxy_health", tuner.check_whatsapp_proxy_health)
        tuner._run_stage("signal_proxy_health", tuner.check_signal_proxy_health)
    except Exception as exc:
        print("[autotune] ERROR", exc)
        try:
            tuner._handle_outcome(False, str(exc))
        except Exception as outcome_exc:
            print("[autotune] CRITICAL: outcome handler failed", outcome_exc)
        return 1

    tuner._handle_outcome(True, "ok")
    print("[autotune] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
