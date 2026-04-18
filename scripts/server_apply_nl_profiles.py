import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import time
from typing import Any

import requests


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        return default
    return max(minimum, value)

BASE = "https://127.0.0.1:55445/panelsmartkama"
USER = "Tsikhilovk"
PASS = "Haker05dag$"
DB = "/opt/SmartKamaVPN/Database/smartkamavpn.db"

WS_INBOUND_ID = 2
GRPC_INBOUND_ID = 7
GRPC_PORT = 15443
TROJAN_INBOUND_ID = 8
TROJAN_PORT = 16443
REALITY_INBOUND_IDS = [3, 4, 5, 6]
ORDER_IDS = [2, 7, 8, 3, 4, 5, 6]

TARGET_REMARKS = {
    2: "Нидерланды - прямой direct",
    7: "Нидерланды - прямой gRPC",
    8: "Нидерланды - прямой Trojan",
    3: "Нидерланды - белый обход 1",
    4: "Нидерланды - белый обход 2",
    5: "Нидерланды - белый обход 3",
    6: "Нидерланды - LTE (моб. обход)",
}

# Кандидаты SNI для российских сетей; ниже выбираются только доступные по TLS:443.
REALITY_SNI_CANDIDATES = {
    3: ["yandex.ru", "ya.ru", "yastatic.net"],
    4: ["vk.com", "vk.ru", "mail.ru"],
    5: ["ok.ru", "dzen.ru", "rambler.ru"],
    6: ["avito.ru", "kinopoisk.ru", "gosuslugi.ru"],
}

FALLBACK_FAST_SNI = [
    "vk.com",
    "rambler.ru",
    "avito.ru",
    "ok.ru",
    "vk.ru",
    "mail.ru",
]

MAX_ACCEPTABLE_RTT_MS = 1200.0
STICKY_PRIMARY_DELTA_MS = _env_float("SMARTKAMA_PROBE_STICKY_PRIMARY_DELTA_MS", 75.0, minimum=0.0)
PROBE_MAX_WORKERS = _env_int("SMARTKAMA_PROBE_MAX_WORKERS", 8, minimum=1)
PROBE_CACHE_FILE = "/opt/SmartKamaVPN/Logs/nl-sni-probe-cache.json"
PROBE_CACHE_TTL_SEC = _env_int("SMARTKAMA_PROBE_CACHE_TTL_SEC", 1800, minimum=0)
PROBE_CACHE_STALE_TTL_SEC = _env_int("SMARTKAMA_PROBE_CACHE_STALE_TTL_SEC", 21600, minimum=0)

# --- Marzban-specific constants ---
MARZBAN_XRAY_CONFIG = "/var/lib/marzban/xray_config.json"
MARZBAN_REALITY_SNI_CANDIDATES: dict[str, list[str]] = {
    "nl-reality-1": ["vk.com", "rambler.ru", "yandex.ru", "ya.ru", "yastatic.net"],
    "nl-reality-2": ["ok.ru", "dzen.ru", "vk.ru", "mail.ru"],
    "nl-reality-3": ["gosuslugi.ru", "avito.ru", "kinopoisk.ru"],
    "nl-reality-4": ["mos.ru", "sberbank.ru", "wildberries.ru", "ozon.ru"],
}

VERBOSE = False


def vlog(*parts: Any) -> None:
    if VERBOSE:
        print(*parts)


def _print_sni_probe_summary(
    label: Any,
    ranked_unique: list[tuple[str, float]],
    selected: list[str],
    source: str = "live",
    age_sec: int | None = None,
) -> None:
    if VERBOSE:
        print(
            "sni_probe",
            label,
            "source",
            source,
            "ranked",
            [(host, round(ms, 1)) for host, ms in ranked_unique],
            "selected",
            selected,
        )
        return
    best_rtt = round(ranked_unique[0][1], 1) if ranked_unique else "na"
    primary = selected[0] if selected else ""
    backup = selected[1] if len(selected) > 1 else primary
    parts: list[Any] = ["sni_probe", label]
    if source != "live":
        parts.extend(["source", source])
    parts.extend(["primary", primary, "backup", backup, "best_ms", best_rtt])
    if age_sec is not None:
        parts.extend(["cache_age_sec", age_sec])
    print(*parts)


def _print_reality_target_summary(entries: list[tuple[Any, Any, str]]) -> None:
    if not entries:
        return
    print(
        "reality_targets",
        ", ".join(f"{tag}:{port}->{sni}" for tag, port, sni in entries),
    )


def _prefer_stable_selection(
    selected: list[str],
    ranked_unique: list[tuple[str, float]],
    current_server_names: list[str] | None = None,
) -> list[str]:
    current = [str(host or "").strip() for host in (current_server_names or []) if str(host or "").strip()]
    rtt_by_host = {host: ms for host, ms in ranked_unique}
    ordered = [host for host in selected if host]
    if not ordered:
        ordered = [host for host, _ in ranked_unique]
    if not ordered:
        return []

    primary = ordered[0]
    best_ms = rtt_by_host.get(primary)
    current_primary = current[0] if current else ""
    if current_primary and current_primary in rtt_by_host:
        current_ms = rtt_by_host[current_primary]
        if best_ms is None or current_ms <= best_ms + STICKY_PRIMARY_DELTA_MS:
            primary = current_primary

    stable: list[str] = [primary]
    for host in current[1:] + ordered + [host for host, _ in ranked_unique]:
        if host and host not in stable:
            stable.append(host)
        if len(stable) >= 2:
            break

    if len(stable) == 1:
        stable.append(stable[0])
    return stable[:2]


def _load_probe_cache() -> dict[str, dict[str, Any]]:
    try:
        with open(PROBE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _probe_cache_max_age_sec() -> int:
    return max(PROBE_CACHE_TTL_SEC, PROBE_CACHE_STALE_TTL_SEC)


def _prune_probe_cache(cache: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = time.time()
    max_age_sec = _probe_cache_max_age_sec()
    keep: dict[str, dict[str, Any]] = {}
    for key, entry in cache.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        try:
            ts = float(entry.get("ts") or 0)
        except Exception:
            continue
        if ts <= 0 or now - ts > max_age_sec:
            continue
        keep[key] = entry
    return keep


def _serialize_probe_cache(cache: dict[str, dict[str, Any]]) -> str:
    return json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _save_probe_cache(cache: dict[str, dict[str, Any]]) -> bool:
    pruned = _prune_probe_cache(cache)
    cache.clear()
    cache.update(pruned)

    target_dir = os.path.dirname(PROBE_CACHE_FILE)
    os.makedirs(target_dir, exist_ok=True)

    payload = _serialize_probe_cache(cache)
    try:
        with open(PROBE_CACHE_FILE, "r", encoding="utf-8") as f:
            if f.read() == payload:
                return False
    except FileNotFoundError:
        pass
    except Exception:
        pass

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target_dir, delete=False) as tmp:
            tmp.write(payload)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, PROBE_CACHE_FILE)
        return True
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _cache_key(prefix: str, label: Any) -> str:
    return f"{prefix}:{label}"


def _read_cached_selection(
    probe_cache: dict[str, dict[str, Any]],
    key: str,
    label: Any,
    candidates: list[str],
    current_server_names: list[str] | None = None,
) -> list[str] | None:
    entry = probe_cache.get(key)
    if not isinstance(entry, dict):
        return None

    try:
        ts = float(entry.get("ts") or 0)
    except Exception:
        return None
    if ts <= 0:
        return None

    age_sec = int(max(0, time.time() - ts))
    max_age_sec = _probe_cache_max_age_sec()
    if age_sec > max_age_sec:
        return None

    expected_candidates = [str(host or "").strip() for host in candidates if str(host or "").strip()]
    cached_candidates = [str(host or "").strip() for host in (entry.get("candidates") or []) if str(host or "").strip()]
    if cached_candidates != expected_candidates:
        return None

    ranked_unique: list[tuple[str, float]] = []
    for item in entry.get("ranked") or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        host = str(item[0] or "").strip()
        if not host:
            continue
        try:
            rtt = float(item[1])
        except Exception:
            continue
        ranked_unique.append((host, rtt))

    cached_selected = [str(host or "").strip() for host in (entry.get("selected") or []) if str(host or "").strip()]
    selected = _prefer_stable_selection(cached_selected, ranked_unique, current_server_names)
    if not selected:
        return None

    source = "cache" if age_sec <= PROBE_CACHE_TTL_SEC else "stale-cache"
    _print_sni_probe_summary(label, ranked_unique, selected, source=source, age_sec=age_sec)
    return selected


def _write_cached_selection(
    probe_cache: dict[str, dict[str, Any]],
    key: str,
    candidates: list[str],
    selected: list[str],
    ranked_unique: list[tuple[str, float]],
) -> None:
    probe_cache[key] = {
        "ts": int(time.time()),
        "candidates": [str(host or "").strip() for host in candidates if str(host or "").strip()],
        "selected": [str(host or "").strip() for host in selected if str(host or "").strip()],
        "ranked": [[host, round(ms, 3)] for host, ms in ranked_unique],
    }


def _j(obj: Any, default: Any) -> Any:
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except Exception:
            return default
    return obj if isinstance(obj, (dict, list)) else default


def _list_inbounds(sess: requests.Session) -> list[dict]:
    return sess.get(BASE + "/panel/api/inbounds/list", timeout=20).json().get("obj", [])


def _update_inbound(sess: requests.Session, inbound_id: int, inbound_obj: dict) -> bool:
    resp = sess.post(
        BASE + f"/panel/api/inbounds/update/{inbound_id}",
        json=inbound_obj,
        timeout=20,
    )
    data = resp.json()
    ok = bool(data.get("success"))
    if ok:
        vlog("update_inbound", inbound_id, ok, data.get("msg"))
    else:
        print("update_inbound_failed", inbound_id, data.get("msg"))
    return ok


def _update_client(sess: requests.Session, inbound_id: int, client: dict) -> bool:
    cid = client.get("id")
    if not cid:
        return False
    payload = {
        "id": inbound_id,
        "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
    }
    resp = sess.post(
        BASE + f"/panel/api/inbounds/updateClient/{cid}",
        json=payload,
        timeout=20,
    )
    data = resp.json()
    return bool(data.get("success"))


def _add_client(sess: requests.Session, inbound_id: int, client: dict) -> bool:
    payload = {
        "id": inbound_id,
        "settings": json.dumps({"clients": [client]}, ensure_ascii=False),
    }
    resp = sess.post(BASE + "/panel/api/inbounds/addClient", json=payload, timeout=20)
    data = resp.json()
    ok = bool(data.get("success"))
    if not ok:
        print("add_client_failed", inbound_id, client.get("id"), data.get("msg"))
    return ok


def _grpc_email(email: str) -> str:
    raw = str(email or "user")
    suffix = f"-{GRPC_INBOUND_ID}"
    return raw if raw.endswith(suffix) else f"{raw}{suffix}"


def _trojan_email(email: str) -> str:
    raw = str(email or "user")
    suffix = f"-{TROJAN_INBOUND_ID}"
    return raw if raw.endswith(suffix) else f"{raw}{suffix}"


def _trojan_client_from_ws(src: dict) -> dict | None:
    password = str(src.get("id") or "").strip()
    if not password:
        return None

    return {
        "password": password,
        "email": _trojan_email(src.get("email", "")),
        "limitIp": int(src.get("limitIp") or 0),
        "totalGB": int(src.get("totalGB") or 0),
        "expiryTime": int(src.get("expiryTime") or 0),
        "enable": bool(src.get("enable", True)),
        "tgId": str(src.get("tgId") or ""),
        "subId": str(src.get("subId") or ""),
        "comment": str(src.get("comment") or src.get("remark") or ""),
        "reset": int(src.get("reset") or 0),
    }


def _probe_tls_443(host: str) -> bool:
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
                return bool(cert)
    except Exception:
        return False


def _probe_tls_rtt_ms(host: str, attempts: int = 3) -> float | None:
    context = ssl.create_default_context()
    samples: list[float] = []
    for _ in range(max(1, attempts)):
        start = time.perf_counter()
        try:
            with socket.create_connection((host, 443), timeout=5) as sock:
                with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert()
                    if cert:
                        elapsed_ms = (time.perf_counter() - start) * 1000.0
                        samples.append(elapsed_ms)
        except Exception:
            continue
    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2]


def _rank_tls_hosts(candidates: list[str], attempts: int = 3) -> list[tuple[str, float]]:
    hosts: list[str] = []
    seen: set[str] = set()
    for host in candidates:
        name = str(host or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        hosts.append(name)

    if not hosts:
        return []

    ranked: list[tuple[str, float]] = []
    max_workers = min(PROBE_MAX_WORKERS, len(hosts))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_probe_tls_rtt_ms, host, attempts): host for host in hosts}
        for future in as_completed(futures):
            host = futures[future]
            try:
                rtt = future.result()
            except Exception:
                rtt = None
            if rtt is not None:
                ranked.append((host, rtt))

    ranked.sort(key=lambda item: item[1])
    return ranked


def _pick_reality_sni(probe_cache: dict[str, dict[str, Any]] | None = None) -> dict[int, list[str]]:
    picked: dict[int, list[str]] = {}
    if probe_cache is None:
        probe_cache = {}
    for inbound_id, candidates in REALITY_SNI_CANDIDATES.items():
        key = _cache_key("3xui", inbound_id)
        cached = _read_cached_selection(probe_cache, key, inbound_id, candidates)
        if cached is not None:
            picked[inbound_id] = cached
            continue

        ranked = _rank_tls_hosts(candidates)
        fastest_hosts = [host for host, ms in ranked if ms <= MAX_ACCEPTABLE_RTT_MS]

        if len(fastest_hosts) < 2:
            fallback_ranked = _rank_tls_hosts(FALLBACK_FAST_SNI, attempts=2)
            for host, rtt in fallback_ranked:
                if host in fastest_hosts:
                    continue
                if rtt > MAX_ACCEPTABLE_RTT_MS:
                    continue
                fastest_hosts.append(host)
                ranked.append((host, rtt))
                if len(fastest_hosts) >= 2:
                    break

        selected = (fastest_hosts or [h for h in candidates if _probe_tls_443(h)] or candidates)[:2]
        if len(selected) == 1:
            selected = [selected[0], selected[0]]
        ranked_unique = sorted({h: ms for h, ms in ranked}.items(), key=lambda x: x[1])
        picked[inbound_id] = selected
        _print_sni_probe_summary(inbound_id, ranked_unique, selected)
        _write_cached_selection(probe_cache, key, candidates, selected, ranked_unique)
    return picked


def _ensure_grpc_inbound(sess: requests.Session, by_id: dict[int, dict]) -> None:
    if GRPC_INBOUND_ID in by_id:
        return

    ws = by_id.get(WS_INBOUND_ID)
    if not ws:
        print("skip_create_grpc_no_ws")
        return

    obj = dict(ws)
    obj.pop("id", None)
    obj["port"] = GRPC_PORT
    obj["remark"] = TARGET_REMARKS[GRPC_INBOUND_ID]
    obj["tag"] = "inbound-15443-grpc"

    stream = _j(obj.get("streamSettings", "{}"), {})
    stream["network"] = "grpc"
    stream["security"] = "tls"
    stream.pop("wsSettings", None)
    stream["grpcSettings"] = {"serviceName": "grpc", "multiMode": False}

    tls_settings = dict(stream.get("tlsSettings") or {})
    if not tls_settings.get("serverName"):
        tls_settings["serverName"] = "sub.smartkama.ru"
    if not isinstance(tls_settings.get("alpn"), list) or not tls_settings.get("alpn"):
        tls_settings["alpn"] = ["h2", "http/1.1"]
    stream["tlsSettings"] = tls_settings
    obj["streamSettings"] = json.dumps(stream, ensure_ascii=False, separators=(",", ":"))

    settings = _j(obj.get("settings", "{}"), {})
    clients = settings.get("clients", [])
    patched_clients = []
    for client in clients:
        c = dict(client)
        c["email"] = _grpc_email(c.get("email", ""))
        c["flow"] = ""
        c["security"] = "none"
        patched_clients.append(c)
    settings["clients"] = patched_clients
    obj["settings"] = json.dumps(settings, ensure_ascii=False)

    add_resp = sess.post(BASE + "/panel/api/inbounds/add", json=obj, timeout=20).json()
    print("add_grpc_inbound", add_resp.get("success"), add_resp.get("msg"))


def _ensure_trojan_inbound(sess: requests.Session, by_id: dict[int, dict]) -> None:
    if TROJAN_INBOUND_ID in by_id:
        return

    ws = by_id.get(WS_INBOUND_ID)
    if not ws:
        print("skip_create_trojan_no_ws")
        return

    obj = dict(ws)
    obj.pop("id", None)
    obj["protocol"] = "trojan"
    obj["port"] = TROJAN_PORT
    obj["remark"] = TARGET_REMARKS[TROJAN_INBOUND_ID]
    obj["tag"] = "inbound-16443-trojan"

    stream = _j(obj.get("streamSettings", "{}"), {})
    stream["network"] = "tcp"
    stream["security"] = "tls"
    stream.pop("wsSettings", None)
    stream.pop("grpcSettings", None)
    stream.pop("realitySettings", None)
    tls_settings = dict(stream.get("tlsSettings") or {})
    if not tls_settings.get("serverName"):
        tls_settings["serverName"] = "sub.smartkama.ru"
    if not isinstance(tls_settings.get("alpn"), list) or not tls_settings.get("alpn"):
        tls_settings["alpn"] = ["h2", "http/1.1"]
    stream["tlsSettings"] = tls_settings
    obj["streamSettings"] = json.dumps(stream, ensure_ascii=False, separators=(",", ":"))

    ws_settings = _j(ws.get("settings", "{}"), {})
    trojan_clients = []
    for ws_client in ws_settings.get("clients", []):
        mapped = _trojan_client_from_ws(ws_client)
        if mapped:
            trojan_clients.append(mapped)
    obj["settings"] = json.dumps({"clients": trojan_clients}, ensure_ascii=False)

    add_resp = sess.post(BASE + "/panel/api/inbounds/add", json=obj, timeout=20).json()
    print("add_trojan_inbound", add_resp.get("success"), add_resp.get("msg"))


def _sync_grpc_clients(sess: requests.Session, by_id: dict[int, dict]) -> None:
    ws = by_id.get(WS_INBOUND_ID)
    grpc = by_id.get(GRPC_INBOUND_ID)
    if not ws or not grpc:
        print("sync_grpc_skip")
        return

    ws_settings = _j(ws.get("settings", "{}"), {})
    grpc_settings = _j(grpc.get("settings", "{}"), {})
    ws_clients = ws_settings.get("clients", [])
    grpc_clients = grpc_settings.get("clients", [])
    grpc_by_uuid = {c.get("id"): c for c in grpc_clients if c.get("id")}

    added = 0
    updated = 0
    for src in ws_clients:
        uid = src.get("id")
        if not uid:
            continue

        desired = dict(src)
        desired["email"] = _grpc_email(desired.get("email", ""))
        desired["flow"] = ""
        desired["security"] = "none"

        if uid not in grpc_by_uuid:
            if _add_client(sess, GRPC_INBOUND_ID, desired):
                added += 1
            continue

        current = dict(grpc_by_uuid[uid])
        changed = False
        for key, value in desired.items():
            if current.get(key) != value:
                current[key] = value
                changed = True
        if changed and _update_client(sess, GRPC_INBOUND_ID, current):
            updated += 1

    print("sync_grpc_clients", "ws", len(ws_clients), "added", added, "updated", updated)


def _sync_trojan_clients(sess: requests.Session, by_id: dict[int, dict]) -> None:
    ws = by_id.get(WS_INBOUND_ID)
    trojan = by_id.get(TROJAN_INBOUND_ID)
    if not ws or not trojan:
        print("sync_trojan_skip")
        return

    ws_settings = _j(ws.get("settings", "{}"), {})
    trojan_settings = _j(trojan.get("settings", "{}"), {})

    desired_clients = []
    for ws_client in ws_settings.get("clients", []):
        mapped = _trojan_client_from_ws(ws_client)
        if mapped:
            desired_clients.append(mapped)

    current_clients = trojan_settings.get("clients", []) or []
    current_by_password = {
        str(c.get("password") or ""): c for c in current_clients if str(c.get("password") or "")
    }

    changed = False
    if len(current_clients) != len(desired_clients):
        changed = True
    if not changed:
        desired_passwords = {c["password"] for c in desired_clients}
        if set(current_by_password.keys()) != desired_passwords:
            changed = True

    if not changed:
        for desired in desired_clients:
            current = current_by_password.get(desired["password"])
            if not current:
                changed = True
                break
            for key, value in desired.items():
                if current.get(key) != value:
                    changed = True
                    break
            if changed:
                break

    if not changed:
        print("sync_trojan_clients", "ws", len(desired_clients), "updated", 0)
        return

    trojan_settings["clients"] = desired_clients
    patch_obj = dict(trojan)
    patch_obj["settings"] = json.dumps(trojan_settings, ensure_ascii=False)
    ok = _update_inbound(sess, TROJAN_INBOUND_ID, patch_obj)
    print("sync_trojan_clients", "ws", len(desired_clients), "updated", 1 if ok else 0)


def _set_db_order() -> None:
    order_csv = ",".join(str(x) for x in ORDER_IDS)
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
        ("threexui_inbound_id", str(WS_INBOUND_ID)),
    )
    cur.execute(
        "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
        ("threexui_inbound_ids", order_csv),
    )
    cur.execute(
        "INSERT OR REPLACE INTO str_config(key,value) VALUES(?,?)",
        ("threexui_reality_fingerprint", "chrome"),
    )
    conn.commit()
    conn.close()
    print("db_order_set", order_csv, "fp", "chrome")


def _detect_provider() -> str:
    try:
        conn = sqlite3.connect(DB)
        row = conn.execute(
            "SELECT value FROM str_config WHERE key='panel_provider'"
        ).fetchone()
        conn.close()
        return (row[0] or "3xui").strip() if row else "3xui"
    except Exception:
        return "3xui"


def _pick_marzban_reality_sni(
    current_sni_map: dict[str, list[str]] | None = None,
    probe_cache: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    picked: dict[str, list[str]] = {}
    if probe_cache is None:
        probe_cache = {}
    for tag, candidates in MARZBAN_REALITY_SNI_CANDIDATES.items():
        key = _cache_key("marzban", tag)
        cached = _read_cached_selection(probe_cache, key, tag, candidates, (current_sni_map or {}).get(tag))
        if cached is not None:
            picked[tag] = cached
            continue

        ranked = _rank_tls_hosts(candidates)
        fastest_hosts = [host for host, ms in ranked if ms <= MAX_ACCEPTABLE_RTT_MS]
        if len(fastest_hosts) < 2:
            fallback_ranked = _rank_tls_hosts(FALLBACK_FAST_SNI, attempts=2)
            for host, rtt in fallback_ranked:
                if host in fastest_hosts:
                    continue
                if rtt > MAX_ACCEPTABLE_RTT_MS:
                    continue
                fastest_hosts.append(host)
                ranked.append((host, rtt))
                if len(fastest_hosts) >= 2:
                    break
        selected = (
            fastest_hosts
            or [h for h in candidates if _probe_tls_443(h)]
            or candidates
        )[:2]
        if len(selected) == 1:
            selected = [selected[0], selected[0]]
        ranked_unique = sorted(
            {h: ms for h, ms in ranked}.items(), key=lambda x: x[1]
        )
        selected = _prefer_stable_selection(selected, ranked_unique, (current_sni_map or {}).get(tag))
        picked[tag] = selected
        _print_sni_probe_summary(tag, ranked_unique, selected)
        _write_cached_selection(probe_cache, key, candidates, selected, ranked_unique)
    return picked


def main_marzban() -> None:
    """Marzban mode: probe SNI and update Reality settings in xray_config.json."""
    if not os.path.isfile(MARZBAN_XRAY_CONFIG):
        print("marzban_config_not_found", MARZBAN_XRAY_CONFIG)
        return

    with open(MARZBAN_XRAY_CONFIG, "r", encoding="utf-8") as f:
        config = json.load(f)

    current_sni_map: dict[str, list[str]] = {}
    for inbound in config.get("inbounds", []):
        tag = inbound.get("tag", "")
        if tag not in MARZBAN_REALITY_SNI_CANDIDATES:
            continue
        stream = inbound.get("streamSettings", {})
        reality = stream.get("realitySettings", {})
        current_sni_map[tag] = [str(host or "") for host in (reality.get("serverNames") or []) if str(host or "")]

    probe_cache = _load_probe_cache()
    sni_map = _pick_marzban_reality_sni(current_sni_map, probe_cache)
    _save_probe_cache(probe_cache)

    changed = False
    for inbound in config.get("inbounds", []):
        tag = inbound.get("tag", "")
        if tag not in MARZBAN_REALITY_SNI_CANDIDATES:
            continue
        stream = inbound.get("streamSettings", {})
        if stream.get("security") != "reality":
            continue
        reality = stream.get("realitySettings", {})
        selected = sni_map.get(tag, [])
        if not selected:
            continue
        if reality.get("serverNames") != selected:
            reality["serverNames"] = selected
            changed = True
        target_dest = f"{selected[0]}:443"
        if reality.get("dest") != target_dest:
            reality["dest"] = target_dest
            changed = True

    if changed:
        backup = MARZBAN_XRAY_CONFIG + f".bak.{int(time.time())}"
        with open(MARZBAN_XRAY_CONFIG, "r", encoding="utf-8") as src:
            with open(backup, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        with open(MARZBAN_XRAY_CONFIG, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print("marzban_config_updated backup", backup)
        restart = subprocess.run(
            ["marzban", "restart", "-n"],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        if VERBOSE:
            for line in (restart.stdout or "").splitlines():
                if line.strip():
                    print("marzban_restart", line.strip())
        print("marzban_restarted")
    else:
        print("marzban_config_unchanged")

    # Final summary.
    final_entries: list[tuple[Any, Any, str]] = []
    for inbound in config.get("inbounds", []):
        tag = inbound.get("tag", "")
        if tag not in MARZBAN_REALITY_SNI_CANDIDATES:
            continue
        stream = inbound.get("streamSettings", {})
        reality = stream.get("realitySettings", {})
        sni = (reality.get("serverNames") or [""])[0]
        final_entries.append((tag, inbound.get("port"), sni))
    _print_reality_target_summary(final_entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply SmartKama NL profiles")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    global VERBOSE
    VERBOSE = bool(args.verbose)

    provider = _detect_provider()
    if provider == "marzban":
        main_marzban()
        return

    probe_cache = _load_probe_cache()

    sess = requests.Session()
    sess.verify = False
    login = sess.post(BASE + "/login", data={"username": USER, "password": PASS}, timeout=20).json()
    if not login.get("success"):
        raise SystemExit("login failed")

    sni_map = _pick_reality_sni(probe_cache)
    _save_probe_cache(probe_cache)

    raw = _list_inbounds(sess)
    by_id = {int(x.get("id", 0)): x for x in raw if x.get("id") is not None}
    _ensure_grpc_inbound(sess, by_id)
    _ensure_trojan_inbound(sess, by_id)

    raw = _list_inbounds(sess)
    by_id = {int(x.get("id", 0)): x for x in raw if x.get("id") is not None}

    for iid, remark in TARGET_REMARKS.items():
        inbound = by_id.get(iid)
        if not inbound:
            print("skip_missing_inbound", iid)
            continue

        changed = False
        patch_obj = dict(inbound)

        if patch_obj.get("remark") != remark:
            patch_obj["remark"] = remark
            changed = True

        if iid in REALITY_INBOUND_IDS:
            stream = _j(patch_obj.get("streamSettings", "{}"), {})
            security = str(stream.get("security") or "").lower()
            network = str(stream.get("network") or "").lower()
            if security == "reality" and network == "tcp":
                reality = dict(stream.get("realitySettings") or {})
                target_names = sni_map.get(iid) or REALITY_SNI_CANDIDATES.get(iid, [])[:2]
                if reality.get("serverNames") != target_names:
                    reality["serverNames"] = target_names
                    changed = True
                target_dest = f"{target_names[0]}:443"
                if reality.get("dest") != target_dest:
                    reality["dest"] = target_dest
                    changed = True
                if reality.get("show") is not True:
                    reality["show"] = True
                    changed = True
                stream["realitySettings"] = reality
                patch_obj["streamSettings"] = json.dumps(stream, ensure_ascii=False, separators=(",", ":"))
            else:
                print("skip_non_reality_tcp", iid, "security", security, "network", network)

        if changed:
            _update_inbound(sess, iid, patch_obj)

    # WS and gRPC direct profiles must not use Vision flow.
    for direct_iid in [WS_INBOUND_ID, GRPC_INBOUND_ID]:
        inbound = by_id.get(direct_iid)
        if not inbound:
            continue
        settings = _j(inbound.get("settings", "{}"), {})
        clients = settings.get("clients", [])
        changed_count = 0
        for c in clients:
            if c.get("flow"):
                c["flow"] = ""
            if c.get("security") != "none":
                c["security"] = "none"
            if direct_iid == GRPC_INBOUND_ID:
                c["email"] = _grpc_email(c.get("email", ""))
            if _update_client(sess, direct_iid, c):
                changed_count += 1
        print("direct_clients_checked", direct_iid, len(clients), "updated", changed_count)

    _sync_grpc_clients(sess, by_id)
    _sync_trojan_clients(sess, by_id)

    # Reality clients should keep xtls-rprx-vision flow.
    for iid in REALITY_INBOUND_IDS:
        inbound = by_id.get(iid)
        if not inbound:
            continue
        settings = _j(inbound.get("settings", "{}"), {})
        clients = settings.get("clients", [])
        changed_count = 0
        for c in clients:
            if c.get("flow") != "xtls-rprx-vision":
                c["flow"] = "xtls-rprx-vision"
            if c.get("security") != "none":
                c["security"] = "none"
            if _update_client(sess, iid, c):
                changed_count += 1
        print("reality_clients_checked", iid, len(clients), "reality_clients_updated", changed_count)

    # Remove duplicates from inbound #1 when same UUID is already in direct inbound #2.
    in1 = by_id.get(1)
    in2 = by_id.get(2)
    if in1 and in2:
        st1 = _j(in1.get("settings", "{}"), {})
        st2 = _j(in2.get("settings", "{}"), {})
        ids2 = {c.get("id") for c in st2.get("clients", [])}
        removed = 0
        for c in st1.get("clients", []):
            uid = c.get("id")
            if uid in ids2:
                rr = sess.post(BASE + f"/panel/api/inbounds/1/delClient/{uid}", timeout=20).json()
                if rr.get("success"):
                    removed += 1
        print("removed_from_1", removed)

    _set_db_order()

    # Final summary.
    final_entries: list[tuple[Any, Any, str]] = []
    final_objs = _list_inbounds(sess)
    for o in final_objs:
        iid = int(o.get("id", 0))
        if iid not in TARGET_REMARKS:
            continue
        ss = _j(o.get("streamSettings", "{}"), {})
        sec = ss.get("security")
        net = ss.get("network")
        reality = ss.get("realitySettings") or {}
        sni = (reality.get("serverNames") or [""])[0]
        final_entries.append((iid, o.get("port"), sni or f"{sec}/{net}"))
        vlog("final", iid, o.get("port"), o.get("remark"), "sec", sec, "net", net, "sni", sni)
    _print_reality_target_summary(final_entries)


if __name__ == "__main__":
    main()