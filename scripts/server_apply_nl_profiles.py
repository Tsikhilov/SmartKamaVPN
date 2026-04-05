import json
import socket
import sqlite3
import ssl
import time
from typing import Any

import requests

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
    print("update_inbound", inbound_id, ok, data.get("msg"))
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


def _pick_reality_sni() -> dict[int, list[str]]:
    picked: dict[int, list[str]] = {}
    for inbound_id, candidates in REALITY_SNI_CANDIDATES.items():
        ranked: list[tuple[str, float]] = []
        for host in candidates:
            rtt = _probe_tls_rtt_ms(host)
            if rtt is not None:
                ranked.append((host, rtt))

        ranked.sort(key=lambda x: x[1])
        fastest_hosts = [host for host, ms in ranked if ms <= MAX_ACCEPTABLE_RTT_MS]

        if len(fastest_hosts) < 2:
            for host in FALLBACK_FAST_SNI:
                if host in fastest_hosts:
                    continue
                rtt = _probe_tls_rtt_ms(host, attempts=2)
                if rtt is None or rtt > MAX_ACCEPTABLE_RTT_MS:
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
        print(
            "sni_probe",
            inbound_id,
            "ranked",
            [(h, round(ms, 1)) for h, ms in ranked_unique],
            "selected",
            selected,
        )
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


def main() -> None:
    sess = requests.Session()
    sess.verify = False
    login = sess.post(BASE + "/login", data={"username": USER, "password": PASS}, timeout=20).json()
    if not login.get("success"):
        raise SystemExit("login failed")

    sni_map = _pick_reality_sni()

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
        print("final", iid, o.get("port"), o.get("remark"), "sec", sec, "net", net, "sni", sni)


if __name__ == "__main__":
    main()