# 3x-ui Panel API Client
# Panel: https://sub.smartkama.ru:55445/
# Docs: https://github.com/mhsanaei/3x-ui

import json
import logging
import re
import hashlib
import uuid as _uuid_module
import datetime
import time
import requests
import config as app_config
from config import (
    THREEXUI_USERNAME, THREEXUI_PASSWORD,
    THREEXUI_PANEL_URL, THREEXUI_WEB_BASE_PATH,
    THREEXUI_INBOUND_ID, THREEXUI_INBOUND_IDS,
)
from Utils import marzban_api

# ---------------------------------------------------------------------------
# Session и авторизация
# ---------------------------------------------------------------------------
_session = requests.Session()
_session.headers.update({'Accept': 'application/json'})
_logged_in = False


def _active_provider():
    provider = str(getattr(app_config, "PANEL_PROVIDER", "3xui") or "3xui").strip().lower()
    if provider in ("3x-ui", "x-ui"):
        return "3xui"
    if provider == "marzban":
        return "marzban"
    return "3xui"


def get_provider_name():
    return _active_provider()


def get_provider_capabilities():
    if _active_provider() == "marzban":
        return marzban_api.provider_capabilities()
    return {
        "read_users": True,
        "read_status": True,
        "write_users": True,
        "device_actions": True,
    }


def _base_url():
    base = THREEXUI_PANEL_URL.rstrip('/')
    path = THREEXUI_WEB_BASE_PATH.strip('/')
    return f"{base}/{path}"


def _parse_inbound_ids(raw):
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [v.strip() for v in str(raw).split(",") if v and str(v).strip()]

    parsed = []
    for value in values:
        try:
            parsed.append(int(str(value).strip()))
        except Exception:
            continue
    # Keep order but deduplicate.
    return list(dict.fromkeys(parsed))


def _target_inbound_ids(inbound_id=None):
    if inbound_id is not None:
        ids = _parse_inbound_ids(inbound_id)
        return ids or [int(THREEXUI_INBOUND_ID)]

    ids = _parse_inbound_ids(THREEXUI_INBOUND_IDS)
    if ids:
        return ids
    return [int(THREEXUI_INBOUND_ID)]


def _login():
    global _logged_in
    try:
        resp = _session.post(
            f"{_base_url()}/login",
            data={"username": THREEXUI_USERNAME, "password": THREEXUI_PASSWORD},
            timeout=15,
        )
        data = resp.json()
        if data.get("success"):
            _logged_in = True
            return True
        logging.error("3x-ui login failed: %s", data.get("msg"))
        return False
    except Exception as e:
        logging.error("3x-ui login error: %s", e)
        return False


def _api(method, path, **kwargs):
    """Authenticated request — re-logins on session expiry."""
    global _logged_in
    if not _logged_in:
        _login()
    url = f"{_base_url()}{path}"
    resp = _session.request(method, url, timeout=30, **kwargs)
    if resp.status_code == 401:
        _logged_in = False
        _login()
        resp = _session.request(method, url, timeout=30, **kwargs)
    return resp


def _response_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}


def _response_success(resp, data=None):
    payload = data if isinstance(data, dict) else _response_json(resp)
    if isinstance(payload, dict) and 'success' in payload:
        return bool(payload.get('success'))
    return 200 <= int(resp.status_code) < 300


def _extract_ip_from_text(text):
    raw = str(text or '').strip()
    if not raw:
        return None

    ipv4 = re.search(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", raw)
    if ipv4:
        return ipv4.group(0)

    # Minimal IPv6 fallback for keys like "2001:db8::1".
    ipv6 = re.search(r"\b(?:[0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F:]{1,4}\b", raw)
    if ipv6:
        return ipv6.group(0)
    return None


def _make_device_fingerprint(ip, user_agent=None, platform=None, client=None):
    ip_text = str(ip or '').strip().lower()
    ua_text = str(user_agent or '').strip().lower()
    platform_text = str(platform or '').strip().lower()
    client_text = str(client or '').strip().lower()

    stable_parts = [x for x in [ua_text, platform_text, client_text] if x]
    if stable_parts:
        seed = "|".join(stable_parts)
    elif ip_text:
        # Fallback when panel does not expose device metadata beyond IP.
        seed = f"ip:{ip_text}"
    else:
        return None

    return hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]


def _normalize_ip_entries(raw_entries):
    entries = []

    def _add(ip_value, label=None, user_agent=None, platform=None, client=None):
        ip = _extract_ip_from_text(ip_value)
        if not ip:
            return
        text = str(label or ip).strip()
        fingerprint = _make_device_fingerprint(ip, user_agent=user_agent, platform=platform, client=client)
        device_key = f"dev:{fingerprint}|ip:{ip}" if fingerprint else f"ip:{ip}"
        entries.append({
            'ip': ip,
            'label': text or ip,
            'key': device_key,
            'fingerprint': fingerprint,
            'user_agent': str(user_agent or '').strip(),
            'platform': str(platform or '').strip(),
            'client': str(client or '').strip(),
        })

    if isinstance(raw_entries, dict):
        for key, value in raw_entries.items():
            if isinstance(value, dict):
                ip = value.get('ip') or value.get('address') or key
                ua = value.get('userAgent') or value.get('user_agent') or value.get('device')
                platform = value.get('platform') or value.get('os')
                client = value.get('client') or value.get('app')
                last_seen = value.get('lastSeen') or value.get('last_seen') or value.get('time')
                count = value.get('count') or value.get('hits') or value.get('requests')
                meta = []
                if count is not None:
                    meta.append(f"hits={count}")
                if last_seen:
                    meta.append(f"last={last_seen}")
                suffix = f" | {'; '.join(meta)}" if meta else ''
                _add(ip, f"{_extract_ip_from_text(ip) or ip}{suffix}", user_agent=ua, platform=platform, client=client)
            else:
                _add(key, key)
    elif isinstance(raw_entries, list):
        for item in raw_entries:
            if isinstance(item, dict):
                ip = item.get('ip') or item.get('address') or item.get('clientIp') or item.get('remoteAddr')
                ua = item.get('userAgent') or item.get('user_agent') or item.get('device') or item.get('platform')
                platform = item.get('platform') or item.get('os')
                client = item.get('client') or item.get('app')
                last_seen = item.get('lastSeen') or item.get('last_seen') or item.get('time')
                meta = [x for x in [ua, last_seen] if x]
                suffix = f" | {' | '.join(str(x) for x in meta)}" if meta else ''
                _add(ip, f"{_extract_ip_from_text(ip) or ip}{suffix}" if ip else None, user_agent=ua, platform=platform, client=client)
            else:
                _add(item, item)
    else:
        _add(raw_entries, raw_entries)

    unique = []
    seen = set()
    for item in entries:
        fingerprint = item.get('fingerprint') or f"{item['ip']}|{item['label']}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(item)
    return unique


def _parse_client_ips_obj(obj):
    """Parse x-ui clientIps obj payload into normalized device entries."""
    if obj is None:
        return []

    # x-ui can return plain strings like "No IP Record" or a JSON string.
    if isinstance(obj, str):
        text = obj.strip()
        if not text:
            return []
        if text.lower() in ("no ip record", "null", "none"):
            return []

        parsed = text
        if text.startswith("[") or text.startswith("{"):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = text

        if isinstance(parsed, str):
            chunks = [x.strip() for x in re.split(r"[\n,|]+", parsed) if x and x.strip()]
            return _normalize_ip_entries(chunks)
        return _normalize_ip_entries(parsed)

    return _normalize_ip_entries(obj)


def _get_client_ip_entries(email):
    if not email:
        return []
    # x-ui variants differ by method/route. Prefer modern 2.8+ POST endpoints,
    # then fallback to legacy GET routes.
    endpoint_methods = [
        ("POST", f"/panel/api/inbounds/clientIps/{email}"),
        ("POST", f"/panel/api/inbounds/getClientIps/{email}"),
        ("GET", f"/panel/api/inbounds/clientIps/{email}"),
        ("GET", f"/panel/api/inbounds/getClientIps/{email}"),
    ]
    for method, endpoint in endpoint_methods:
        try:
            resp = _api(method, endpoint)
            data = _response_json(resp)
            if not _response_success(resp, data):
                continue
            obj = data.get('obj') if isinstance(data, dict) else None
            if obj is None:
                continue
            entries = _parse_client_ips_obj(obj)
            if entries:
                return entries
        except Exception as e:
            logging.debug("_get_client_ip_entries failed for %s %s: %s", method, endpoint, e)
    return []


def _normalize_last_online_ts(value):
    try:
        ts = int(float(value))
    except Exception:
        return 0
    return ts if ts > 0 else 0


def _get_last_online_map():
    endpoint_methods = [
        ("POST", "/panel/api/inbounds/lastOnline"),
        ("GET", "/panel/api/inbounds/lastOnline"),
    ]
    for method, endpoint in endpoint_methods:
        try:
            resp = _api(method, endpoint)
            data = _response_json(resp)
            if not _response_success(resp, data):
                continue
            obj = data.get('obj') if isinstance(data, dict) else None
            if isinstance(obj, dict):
                return obj
        except Exception as e:
            logging.debug("_get_last_online_map failed for %s %s: %s", method, endpoint, e)
    return {}


def _email_base_key(email):
    text = str(email or '').strip().lower()
    if not text:
        return ''
    for inbound_id in _target_inbound_ids():
        suffix = f"-{inbound_id}"
        if text.endswith(suffix):
            return text[:-len(suffix)]
    return text


def _virtual_device_entry(email, ts):
    base_email = _email_base_key(email)
    label = "Активное устройство (online по панели)"
    if ts:
        try:
            dt = datetime.datetime.fromtimestamp(ts / 1000.0)
            label = f"Активное устройство (online: {dt.strftime('%d.%m %H:%M')})"
        except Exception:
            pass
    fp = f"virtual:{base_email or str(email).strip().lower()}"
    return {
        'ip': '',
        'name': label,
        'label': label,
        'key': fp,
        'fingerprint': fp,
        'user_agent': 'panel-last-online',
        'platform': '',
        'client': '',
    }


def _inbounds_list():
    try:
        resp = _api("GET", "/panel/api/inbounds/list")
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        return data.get("obj", [])
    except Exception as e:
        logging.error("_inbounds_list error: %s", e)
        return []


def _target_vless_inbound_ids(inbound_id=None):
    """Return only VLESS inbounds for client create/select operations."""
    ids = _target_inbound_ids(inbound_id)
    if not ids:
        return []

    protocol_by_id = {}
    for inbound in _inbounds_list():
        try:
            iid = int(inbound.get("id"))
        except Exception:
            continue
        protocol_by_id[iid] = str(inbound.get("protocol") or "").lower()

    filtered = [iid for iid in ids if protocol_by_id.get(iid, "vless") == "vless"]
    if filtered:
        skipped = [iid for iid in ids if iid not in filtered]
        if skipped:
            logging.info("Skipping non-VLESS inbounds for client ops: %s", skipped)
        return filtered

    # If protocol metadata is unavailable, keep legacy behavior.
    return ids


def _get_inbound_transport(inbound_id):
    """Return (security, network) for inbound id."""
    try:
        for inbound in _inbounds_list():
            if str(inbound.get("id")) != str(inbound_id):
                continue
            raw = inbound.get("streamSettings", "{}")
            stream = json.loads(raw) if isinstance(raw, str) else (raw or {})
            return (stream.get("security") or "", stream.get("network") or "")
    except Exception as e:
        logging.error("_get_inbound_transport error: %s", e)
    return "", ""


def _apply_client_transport_defaults(client_obj, inbound_id):
    """Normalize client fields depending on inbound transport type."""
    security, network = _get_inbound_transport(inbound_id)
    sec = (security or "").lower()
    net = (network or "").lower()

    if sec == "reality":
        client_obj["flow"] = "xtls-rprx-vision"
    else:
        # WS/TLS and other non-Reality transports must not use Vision flow.
        client_obj["flow"] = ""

    # Explicit VLESS client security field helps some client importers.
    client_obj["security"] = "none"
    if net == "ws" and not client_obj.get("security"):
        client_obj["security"] = "none"
    return client_obj


# ---------------------------------------------------------------------------
# Конвертация форматов
# ---------------------------------------------------------------------------

def _gb_to_bytes(gb):
    return int(float(gb) * 1024 ** 3) if gb else 0


def _bytes_to_gb(b):
    return round(int(b) / 1024 ** 3, 3) if b else 0.0


def _days_to_expiry_ms(package_days, start_date=None):
    """Вычисляет Unix-timestamp в миллисекундах для даты истечения."""
    if not package_days:
        return 0
    if start_date:
        try:
            base = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        except Exception:
            base = datetime.datetime.utcnow()
    else:
        base = datetime.datetime.utcnow()
    expiry = base + datetime.timedelta(days=int(package_days))
    return int(expiry.timestamp() * 1000)


def _expiry_ms_to_days(expiry_ms):
    """Сколько дней осталось с текущего момента."""
    if not expiry_ms:
        return None
    remaining = (expiry_ms / 1000) - time.time()
    return max(0, int(remaining / 86400))


def _expiry_ms_to_start_date(expiry_ms, package_days):
    """Обратно вычисляет start_date из expiryTime и package_days."""
    if not expiry_ms or not package_days:
        return datetime.datetime.utcnow().strftime("%Y-%m-%d")
    start_ts = (expiry_ms / 1000) - int(package_days) * 86400
    return datetime.datetime.utcfromtimestamp(start_ts).strftime("%Y-%m-%d")


def _client_to_user(client, stats=None, device_entries=None):
    """Преобразует 3x-ui client dict в формат, совместимый с Hiddify-логикой бота."""
    expiry_ms = client.get("expiryTime", 0)
    total_bytes = client.get("totalGB", 0)  # в API это уже байты (несмотря на имя)
    usage_limit_gb = _bytes_to_gb(total_bytes) if total_bytes else 0.0

    up = stats.get("up", 0) if stats else 0
    down = stats.get("down", 0) if stats else 0
    current_usage_gb = _bytes_to_gb(up + down)

    # package_days: сохраняем в comment как "days:<N>" если нет expiryTime
    comment = client.get("remark") or client.get("email") or ""
    package_days = None
    if expiry_ms:
        remaining = _expiry_ms_to_days(expiry_ms)
        package_days = remaining
    else:
        package_days = 36500  # бессрочный

    start_date = _expiry_ms_to_start_date(expiry_ms, package_days) if expiry_ms else \
        datetime.datetime.utcnow().strftime("%Y-%m-%d")

    user_payload = {
        "uuid": client.get("id"),
        "name": client.get("email", ""),
        "last_online": "1-01-01 00:00:00",
        "expiry_time": expiry_ms,
        "usage_limit_GB": usage_limit_gb,
        "package_days": package_days,
        "mode": "no_reset",
        "monthly": None,
        "start_date": start_date,
        "current_usage_GB": current_usage_gb,
        "last_reset_time": start_date,
        "comment": comment,
        "telegram_id": client.get("tgId") or None,
        "added_by": None,
        "max_ips": client.get("limitIp") or None,
        "enable": client.get("enable", True),
        "sub_id": client.get("subId", ""),
    }

    if device_entries:
        user_payload["connected_ips"] = device_entries
        user_payload["online_ips"] = device_entries
        user_payload["devices"] = device_entries

    return user_payload


def _get_inbound_clients(inbound_id=None):
    """Возвращает список клиентов из указанного inbound."""
    iid = int(inbound_id or THREEXUI_INBOUND_ID)
    try:
        for inbound in _inbounds_list():
            if str(inbound.get("id")) == str(iid):
                raw = inbound.get("settings", "{}")
                settings = json.loads(raw) if isinstance(raw, str) else raw
                return settings.get("clients", [])
        return []
    except Exception as e:
        logging.error("_get_inbound_clients error: %s", e)
        return []


def _get_client_stats(email):
    """Трафик клиента по email из 3x-ui."""
    try:
        resp = _api("GET", f"/panel/api/inbounds/getClientTraffics/{email}")
        if resp.status_code == 200:
            d = resp.json()
            if d.get("success"):
                return d.get("obj") or {}
    except Exception as e:
        logging.error("_get_client_stats error: %s", e)
    return {}


# ---------------------------------------------------------------------------
# Публичный API — та же сигнатура, что была в Hiddify-клиенте
# ---------------------------------------------------------------------------

def select(url=None, endpoint=None):
    """Получить всех клиентов из inbound."""
    if _active_provider() == "marzban":
        return marzban_api.select(url=url, endpoint=endpoint)

    import Utils.utils as utils
    try:
        target_ids = _target_vless_inbound_ids(endpoint)
        if not target_ids:
            return None
        primary_inbound_id = target_ids[0]
        clients = _get_inbound_clients(primary_inbound_id)
        users = []
        for c in clients:
            stats = _get_client_stats(c.get("email", ""))
            users.append(_client_to_user(c, stats))
        return utils.dict_process(url or THREEXUI_PANEL_URL, users)
    except Exception as e:
        logging.error("API select error: %s", e)
        return None


def find(url=None, uuid=None, endpoint=None):
    """Найти клиента по UUID."""
    if _active_provider() == "marzban":
        return marzban_api.find(url=url, uuid=uuid, endpoint=endpoint)

    try:
        contexts = []
        for inbound_id in _target_inbound_ids(endpoint):
            clients = _get_inbound_clients(inbound_id)
            for c in clients:
                if c.get("id") == str(uuid):
                    contexts.append((inbound_id, c))

        if not contexts:
            return None

        # Берем первый найденный контекст как основной источник полей пользователя,
        # а устройства собираем со всех inbound, где присутствует UUID.
        primary_inbound_id, primary_client = contexts[0]
        del primary_inbound_id

        blocked_ips = set()
        blocked_devices = set()
        for _inbound_id, client_obj in contexts:
            blocked_ips |= _parse_blocked_ips_from_comment(client_obj.get("remark") or "")
            blocked_devices |= _parse_blocked_devices_from_comment(client_obj.get("remark") or "")

        last_online_map = _get_last_online_map()

        all_device_entries = []
        for inbound_id, client_obj in contexts:
            email = client_obj.get("email", "")
            entries = _get_client_ip_entries(email)
            if not entries:
                ts = _normalize_last_online_ts(last_online_map.get(email))
                if ts > 0:
                    entries = [_virtual_device_entry(email, ts)]
            for entry in entries:
                e = dict(entry)
                e["_inbound_id"] = inbound_id
                e["_email"] = email
                all_device_entries.append(e)

        filtered_devices = []
        kicked_ips = set()
        for entry in all_device_entries:
            entry_ip = entry.get('ip')
            entry_fp = str(entry.get('fingerprint') or '').lower()
            inbound_id = entry.get("_inbound_id")
            email = entry.get("_email", "")

            is_blocked = (entry_ip in blocked_ips) or (entry_fp and entry_fp in blocked_devices)
            if is_blocked and entry_ip and inbound_id is not None:
                kick_key = (int(inbound_id), str(email), str(entry_ip))
                if kick_key not in kicked_ips:
                    _delete_client_ip(str(email), int(inbound_id), str(entry_ip), allow_clear_fallback=True)
                    kicked_ips.add(kick_key)
                continue
            filtered_devices.append(entry)

        unique_device_entries = []
        seen = set()
        for entry in filtered_devices:
            key = str(entry.get('key') or '')
            fp = str(entry.get('fingerprint') or '').lower()
            ip = str(entry.get('ip') or '')
            label = str(entry.get('label') or '')
            dedupe_key = fp or key or f"{ip}|{label}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            clean_entry = dict(entry)
            clean_entry.pop("_inbound_id", None)
            clean_entry.pop("_email", None)
            unique_device_entries.append(clean_entry)

        stats = _get_client_stats(primary_client.get("email", ""))
        return _client_to_user(primary_client, stats, device_entries=unique_device_entries)
        return None
    except Exception as e:
        logging.error("API find error: %s", e)
        return None


def insert(url=None, name=None, usage_limit_GB=0, package_days=30,
           last_reset_time=None, added_by_uuid=None, mode="no_reset",
           last_online="1-01-01 00:00:00", telegram_id=None,
           comment=None, current_usage_GB=0, start_date=None,
           max_ips=None, endpoint=None):
    """Создать нового клиента в 3x-ui inbound, вернуть UUID или None."""
    if _active_provider() == "marzban":
        return marzban_api.insert(
            url=url,
            name=name,
            usage_limit_GB=usage_limit_GB,
            package_days=package_days,
            last_reset_time=last_reset_time,
            added_by_uuid=added_by_uuid,
            mode=mode,
            last_online=last_online,
            telegram_id=telegram_id,
            comment=comment,
            current_usage_GB=current_usage_GB,
            start_date=start_date,
            max_ips=max_ips,
            endpoint=endpoint,
        )

    new_uuid = str(_uuid_module.uuid4())
    # 3x-ui требует уникальный email в рамках inbound, поэтому имя пользователя
    # нельзя использовать как есть: возможны повторные покупки с одинаковым именем.
    raw_name = str(name or "user").strip().lower()
    safe_name = re.sub(r"[^a-z0-9._-]+", "_", raw_name).strip("_") or "user"
    email = f"{safe_name}-{new_uuid[:8]}"
    expiry_ms = _days_to_expiry_ms(package_days, start_date)

    client = {
        "id": new_uuid,
        "alterId": 0,
        "email": email,
        "remark": comment or "",
        "limitIp": int(max_ips) if max_ips else 0,
        "totalGB": _gb_to_bytes(usage_limit_GB),
        "expiryTime": expiry_ms,
        "enable": True,
        "flow": "xtls-rprx-vision",
        "security": "none",
        "tgId": str(telegram_id) if telegram_id else "",
        "subId": new_uuid[:8],
    }
    target_inbound_ids = _target_vless_inbound_ids(endpoint)
    if not target_inbound_ids:
        target_inbound_ids = [int(THREEXUI_INBOUND_ID)]

    added_inbounds = []
    try:
        for idx, inbound_id in enumerate(target_inbound_ids):
            client_copy = dict(client)
            # 3x-ui не допускает одинаковые email между inbound-ами.
            client_copy["email"] = email if idx == 0 else f"{email}-{inbound_id}"
            client_copy = _apply_client_transport_defaults(client_copy, inbound_id)
            payload = {"id": inbound_id, "settings": json.dumps({"clients": [client_copy]})}
            resp = _api("POST", "/panel/api/inbounds/addClient", json=payload)
            data = resp.json()
            if not data.get("success"):
                logging.error("API insert error (inbound %s): %s", inbound_id, data.get("msg"))
                for rollback_inbound in added_inbounds:
                    try:
                        _api("POST", f"/panel/api/inbounds/{rollback_inbound}/delClient/{new_uuid}")
                    except Exception:
                        pass
                return None
            added_inbounds.append(inbound_id)
        return new_uuid
    except Exception as e:
        logging.error("API insert error: %s", e)
        return None


def update(url=None, uuid=None, endpoint=None, **kwargs):
    """Обновить поля клиента."""
    if _active_provider() == "marzban":
        return marzban_api.update(url=url, uuid=uuid, endpoint=endpoint, **kwargs)

    try:
        target_inbound_ids = _target_inbound_ids(endpoint)
        updated_any = False

        for idx, inbound_id in enumerate(target_inbound_ids):
            clients = _get_inbound_clients(inbound_id)
            target = next((c for c in clients if c.get("id") == str(uuid)), None)
            if not target:
                continue

            # Маппинг Hiddify-полей → 3x-ui поля
            if "usage_limit_GB" in kwargs:
                target["totalGB"] = _gb_to_bytes(kwargs["usage_limit_GB"])
            if "package_days" in kwargs:
                sd = kwargs.get("start_date") or datetime.datetime.utcnow().strftime("%Y-%m-%d")
                target["expiryTime"] = _days_to_expiry_ms(kwargs["package_days"], sd)
            if "start_date" in kwargs and "package_days" not in kwargs:
                pd = _expiry_ms_to_days(target.get("expiryTime", 0)) or 30
                target["expiryTime"] = _days_to_expiry_ms(pd, kwargs["start_date"])
            if "max_ips" in kwargs and kwargs["max_ips"] is not None:
                target["limitIp"] = int(kwargs["max_ips"])
            if "telegram_id" in kwargs:
                target["tgId"] = str(kwargs["telegram_id"]) if kwargs["telegram_id"] else ""
            if "enable" in kwargs:
                target["enable"] = bool(kwargs["enable"])
            if "comment" in kwargs:
                target["remark"] = kwargs["comment"] or ""
            if "name" in kwargs and kwargs["name"]:
                base_email = str(kwargs["name"])
                target["email"] = base_email if idx == 0 else f"{base_email}-{inbound_id}"

            target = _apply_client_transport_defaults(target, inbound_id)

            payload = {"id": inbound_id, "settings": json.dumps({"clients": [target]})}
            resp = _api("POST", f"/panel/api/inbounds/updateClient/{uuid}", json=payload)
            data = resp.json()
            if data.get("success"):
                updated_any = True
            else:
                logging.error("API update error (inbound %s): %s", inbound_id, data.get("msg"))

        return uuid if updated_any else None
    except Exception as e:
        logging.error("API update error: %s", e)
        return None


def delete(url=None, uuid=None, endpoint=None):
    """Удалить клиента из inbound."""
    if _active_provider() == "marzban":
        return marzban_api.delete(url=url, uuid=uuid, endpoint=endpoint)

    try:
        deleted_any = False
        for inbound_id in _target_inbound_ids(endpoint):
            clients = _get_inbound_clients(inbound_id)
            exists = any(c.get("id") == str(uuid) for c in clients)
            if not exists:
                continue
            resp = _api("POST", f"/panel/api/inbounds/{inbound_id}/delClient/{uuid}")
            data = resp.json()
            if data.get("success"):
                deleted_any = True
            else:
                logging.error("API delete error (inbound %s): %s", inbound_id, data.get("msg"))
        return deleted_any
    except Exception as e:
        logging.error("API delete error: %s", e)
        return False


def get_panel_status(url=None):
    """Статус сервера 3x-ui."""
    if _active_provider() == "marzban":
        return marzban_api.get_panel_status(url=url)

    try:
        resp = _api("GET", "/panel/api/server/status")
        if resp.status_code == 200:
            d = resp.json()
            if d.get("success"):
                return d.get("obj")
        return None
    except Exception as e:
        logging.error("API status error: %s", e)
        return None


def reset_user_usage(url=None, uuid=None, endpoint=None):
    """Сбросить трафик клиента."""
    if _active_provider() == "marzban":
        return marzban_api.reset_user_usage(url=url, uuid=uuid, endpoint=endpoint)

    try:
        reset_any = False
        for inbound_id in _target_inbound_ids(endpoint):
            clients = _get_inbound_clients(inbound_id)
            target = next((c for c in clients if c.get("id") == str(uuid)), None)
            if not target:
                continue
            email = target.get("email", "")
            resp = _api("POST", f"/panel/api/inbounds/resetClientTraffic/{email}")
            data = resp.json()
            if data.get("success"):
                reset_any = True
            else:
                logging.error("API reset traffic error (inbound %s): %s", inbound_id, data.get("msg"))
        return uuid if reset_any else None
    except Exception as e:
        logging.error("API reset_user_usage error: %s", e)
        return None


def reset_user_days(url=None, uuid=None, package_days=30, endpoint=None):
    """Продлить подписку клиента на package_days дней от сейчас."""
    if _active_provider() == "marzban":
        return marzban_api.reset_user_days(url=url, uuid=uuid, package_days=package_days, endpoint=endpoint)

    return update(url, uuid,
                  package_days=package_days,
                  start_date=datetime.datetime.utcnow().strftime("%Y-%m-%d"))


# ---------------------------------------------------------------------------
# Device management helpers (IP-based for 3x-ui)
# ---------------------------------------------------------------------------


def _find_client_contexts(uuid, endpoint=None):
    contexts = []
    for inbound_id in _target_inbound_ids(endpoint):
        clients = _get_inbound_clients(inbound_id)
        for client in clients:
            if client.get("id") == str(uuid):
                contexts.append((inbound_id, client))
    return contexts


def _parse_device_key(device_key):
    raw = str(device_key or '').strip()
    if not raw:
        return None, None

    ip = _extract_ip_from_text(raw)
    fp_match = re.search(r"dev:([0-9a-f]{8,64})", raw, flags=re.IGNORECASE)
    fingerprint = fp_match.group(1).lower() if fp_match else None
    return fingerprint, ip


def _parse_comment_list(comment, field_name):
    text = str(comment or "")
    if not text:
        return set()

    pattern = rf"(?:^|[;,\s]){re.escape(field_name)}\s*=\s*([^;\n]+)"
    m = re.search(pattern, text, flags=re.IGNORECASE)
    if not m:
        return set()

    values = set()
    for part in [p.strip() for p in str(m.group(1)).split(",") if p and p.strip()]:
        values.add(part)
    return values


def _set_comment_list(comment, field_name, values):
    original = str(comment or "").strip()
    pattern = rf"(?:^|[;,\s]){re.escape(field_name)}\s*=\s*[^;\n]+"
    cleaned = re.sub(pattern, "", original, flags=re.IGNORECASE).strip(" ;,")

    normalized = sorted({str(v).strip() for v in (values or set()) if str(v).strip()})
    if not normalized:
        return cleaned

    suffix = f"{field_name}=" + ",".join(normalized)
    if not cleaned:
        return suffix
    return f"{cleaned};{suffix}"


def _parse_blocked_ips_from_comment(comment):
    parts = _parse_comment_list(comment, "blocked_ips")
    blocked = set()
    for item in parts:
        ip = _extract_ip_from_text(item)
        if ip:
            blocked.add(ip)
    return blocked


def _parse_blocked_devices_from_comment(comment):
    parts = _parse_comment_list(comment, "blocked_devices")
    blocked = set()
    for item in parts:
        token = str(item or '').strip().lower()
        if token:
            blocked.add(token)
    return blocked


def _set_blocked_ips_in_comment(comment, blocked_ips):
    return _set_comment_list(comment, "blocked_ips", blocked_ips)


def _set_blocked_devices_in_comment(comment, blocked_devices):
    return _set_comment_list(comment, "blocked_devices", blocked_devices)


def _delete_client_ip(email, inbound_id, ip, allow_clear_fallback=False):
    if not email or not ip:
        return False

    candidate_requests = [
        ("POST", f"/panel/api/inbounds/{inbound_id}/delClientIp/{email}/{ip}"),
        ("POST", f"/panel/api/inbounds/delClientIp/{email}/{ip}"),
        ("POST", f"/panel/api/inbounds/{inbound_id}/delClientIp/{ip}"),
        ("POST", f"/panel/api/inbounds/delClientIp/{ip}"),
        ("GET", f"/panel/api/inbounds/{inbound_id}/delClientIp/{email}/{ip}"),
        ("GET", f"/panel/api/inbounds/delClientIp/{email}/{ip}"),
    ]
    for method, path in candidate_requests:
        try:
            resp = _api(method, path)
            data = _response_json(resp)
            if _response_success(resp, data):
                return True
        except Exception as e:
            logging.debug("_delete_client_ip failed for %s %s: %s", method, path, e)

    if allow_clear_fallback:
        try:
            resp = _api("POST", f"/panel/api/inbounds/clearClientIps/{email}")
            data = _response_json(resp)
            if _response_success(resp, data):
                return True
        except Exception as e:
            logging.debug("_delete_client_ip clear fallback failed for %s: %s", email, e)

    return False


def device_action(url=None, uuid=None, device_key=None, action="delete"):
    if _active_provider() == "marzban":
        return marzban_api.device_action(url=url, uuid=uuid, device_key=device_key, action=action)

    contexts = _find_client_contexts(uuid)
    if not contexts:
        return False

    _fingerprint, ip = _parse_device_key(device_key)
    if not ip:
        return False

    done = False
    if action == "delete":
        for inbound_id, client in contexts:
            email = client.get("email", "")
            if _delete_client_ip(email, inbound_id, ip):
                done = True
        return done

    return False


def block_device(url=None, uuid=None, device_key=None):
    if _active_provider() == "marzban":
        return marzban_api.block_device(url=url, uuid=uuid, device_key=device_key)

    fingerprint, ip = _parse_device_key(device_key)
    if not fingerprint and not ip:
        return False

    contexts = _find_client_contexts(uuid)
    if not contexts:
        return False

    comment = contexts[0][1].get("remark") or ""
    blocked_devices = _parse_blocked_devices_from_comment(comment)
    blocked_ips = _parse_blocked_ips_from_comment(comment)
    if fingerprint:
        blocked_devices.add(fingerprint)
    elif ip:
        blocked_ips.add(ip)

    new_comment = _set_blocked_devices_in_comment(comment, blocked_devices)
    new_comment = _set_blocked_ips_in_comment(new_comment, blocked_ips)

    saved = update(url, uuid, comment=new_comment) is not None
    kicked = device_action(url, uuid, device_key, action="delete")
    return bool(saved or kicked)


def delete_device(url=None, uuid=None, device_key=None):
    if _active_provider() == "marzban":
        return marzban_api.delete_device(url=url, uuid=uuid, device_key=device_key)

    fingerprint, ip = _parse_device_key(device_key)
    if not fingerprint and not ip:
        return False

    contexts = _find_client_contexts(uuid)
    if not contexts:
        return False

    comment = contexts[0][1].get("remark") or ""
    blocked_devices = _parse_blocked_devices_from_comment(comment)
    blocked_ips = _parse_blocked_ips_from_comment(comment)
    updated_comment = comment
    changed = False

    if fingerprint and fingerprint in blocked_devices:
        blocked_devices.remove(fingerprint)
        updated_comment = _set_blocked_devices_in_comment(updated_comment, blocked_devices)
        changed = True

    if not fingerprint and ip in blocked_ips:
        blocked_ips.remove(ip)
        updated_comment = _set_blocked_ips_in_comment(updated_comment, blocked_ips)
        changed = True

    if changed:
        update(url, uuid, comment=updated_comment)

    return device_action(url, uuid, device_key, action="delete")
