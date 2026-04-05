import datetime
import logging
import re
import time
import uuid
from urllib.parse import quote
from typing import Any, Dict, List, Optional, cast

import requests
import config as app_config


_session = requests.Session()
_access_token_cache: Optional[str] = None
_access_token_ts: float = 0.0

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def provider_name() -> str:
    return "marzban"


def provider_capabilities() -> Dict[str, bool]:
    return {
        "read_users": True,
        "read_status": True,
        "write_users": True,
        "device_actions": False,
    }


def is_enabled() -> bool:
    return bool(_base_url())


def _cfg_str(name: str) -> str:
    return str(getattr(app_config, name, "") or "")


def _cfg_bool(name: str, default: bool = False) -> bool:
    raw = getattr(app_config, name, default)
    if isinstance(raw, bool):
        return raw
    text = str(raw or "").strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return bool(default)


def _cfg_inbound_tags() -> List[str]:
    raw = getattr(app_config, "MARZBAN_INBOUND_TAGS", [])
    if isinstance(raw, list):
        raw_list = cast(List[Any], raw)
        tags = [str(x).strip() for x in raw_list if str(x).strip()]
        return list(dict.fromkeys(tags))
    text = str(raw or "")
    tags = [x.strip() for x in text.split(",") if x and x.strip()]
    return list(dict.fromkeys(tags))


def _base_url() -> str:
    raw = _cfg_str("MARZBAN_PANEL_URL").strip().rstrip("/")
    if raw.endswith("/dashboard"):
        raw = raw[: -len("/dashboard")]
    return raw


def _normalize_expire_ts(value: Any) -> int:
    try:
        ts = int(float(value))
    except Exception:
        return 0
    if ts <= 0:
        return 0
    # Marzban may expose seconds, some integrations use milliseconds.
    return ts // 1000 if ts > 10**12 else ts


def _bytes_to_gb(value: Any) -> float:
    try:
        return round(int(value) / (1024 ** 3), 3)
    except Exception:
        return 0.0


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    base = _base_url()
    if not base:
        raise RuntimeError("MARZBAN_PANEL_URL is not configured")

    headers = dict(kwargs.pop("headers", {}) or {})
    token = _get_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = int(kwargs.pop("timeout", 25))
    verify = _cfg_bool("MARZBAN_TLS_VERIFY", False)

    response = _session.request(
        method,
        f"{base}{path}",
        headers=headers,
        timeout=timeout,
        verify=verify,
        **kwargs,
    )

    # Retry once with fresh token if we use dynamic credential auth.
    if response.status_code == 401 and not _cfg_str("MARZBAN_ACCESS_TOKEN").strip():
        global _access_token_cache, _access_token_ts
        _access_token_cache = None
        _access_token_ts = 0.0

        refreshed_token = _get_access_token()
        if refreshed_token:
            headers["Authorization"] = f"Bearer {refreshed_token}"
            response = _session.request(
                method,
                f"{base}{path}",
                headers=headers,
                timeout=timeout,
                verify=verify,
                **kwargs,
            )

    return response


def _get_access_token() -> Optional[str]:
    global _access_token_cache, _access_token_ts

    static_token = _cfg_str("MARZBAN_ACCESS_TOKEN").strip()
    if static_token:
        return static_token

    if _access_token_cache and (time.time() - _access_token_ts) < 600:
        return _access_token_cache

    username = _cfg_str("MARZBAN_USERNAME").strip()
    password = _cfg_str("MARZBAN_PASSWORD").strip()
    if not username or not password or not _base_url():
        return None

    verify = _cfg_bool("MARZBAN_TLS_VERIFY", False)

    login_paths = ["/api/admin/token", "/api/token"]
    for path in login_paths:
        try:
            resp = _session.post(
                f"{_base_url()}{path}",
                data={"username": username, "password": password},
                timeout=20,
                verify=verify,
            )
            if resp.status_code >= 400:
                continue
            raw_payload: Any = resp.json() if resp.content else {}
            payload: Dict[str, Any] = cast(Dict[str, Any], raw_payload if isinstance(raw_payload, dict) else {})
            token = str(payload.get("access_token") or payload.get("token") or "").strip()
            if token:
                _access_token_cache = token
                _access_token_ts = time.time()
                return token
        except Exception as exc:
            logging.debug("Marzban auth failed on %s: %s", path, exc)

    return None


_META_RE = re.compile(r"\[SKV_META\s+([^\]]+)\]\s*$", flags=re.IGNORECASE)


def _split_note_and_meta(note_value: Any) -> tuple[str, Dict[str, str]]:
    note = str(note_value or "")
    match = _META_RE.search(note)
    if not match:
        return note.strip(), {}

    body = match.group(1)
    meta: Dict[str, str] = {}
    for token in re.split(r"\s+", body.strip()):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        k = key.strip().lower()
        v = value.strip()
        if k and v:
            meta[k] = v

    plain = note[: match.start()].strip()
    return plain, meta


def _compose_note(plain_text: str, meta: Dict[str, str]) -> str:
    text = str(plain_text or "").strip()
    clean_meta = {str(k).strip().lower(): str(v).strip() for k, v in meta.items() if str(k).strip() and str(v).strip()}
    if not clean_meta:
        return text

    ordered_keys = sorted(clean_meta.keys())
    suffix = "[SKV_META " + " ".join(f"{k}={clean_meta[k]}" for k in ordered_keys) + "]"
    return f"{text}\n\n{suffix}" if text else suffix


def _extract_max_ips(user: Dict[str, Any]) -> Optional[int]:
    direct_value = user.get("max_ips")
    if direct_value is not None:
        try:
            parsed_direct = int(direct_value)
            return parsed_direct if parsed_direct >= 0 else None
        except Exception:
            pass

    _, meta = _split_note_and_meta(user.get("note") or "")
    if "max_ips" not in meta:
        return None
    try:
        parsed = int(meta["max_ips"])
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _extract_telegram_id(user: Dict[str, Any]) -> Optional[str]:
    _, meta = _split_note_and_meta(user.get("note") or "")
    value = str(meta.get("telegram_id") or "").strip()
    return value or None


def _extract_uuid_from_user(user: Dict[str, Any]) -> str:
    def _walk(value: Any) -> Optional[str]:
        if isinstance(value, dict):
            value_dict = cast(Dict[str, Any], value)
            candidate = str(value_dict.get("id") or "").strip()
            if _UUID_RE.fullmatch(candidate):
                return candidate
            for nested in value_dict.values():
                found = _walk(nested)
                if found:
                    return found
        elif isinstance(value, list):
            value_list = cast(List[Any], value)
            for item in value_list:
                found = _walk(item)
                if found:
                    return found
        return None

    proxies = user.get("proxies")
    found_uuid = _walk(proxies)
    if found_uuid:
        return found_uuid

    username = str(user.get("username") or "").strip()
    if _UUID_RE.fullmatch(username):
        return username

    return username or ""


def _extract_sub_id(user: Dict[str, Any], fallback_uuid: str) -> str:
    for key in ("sub_id", "subscription_token", "token"):
        value = str(user.get(key) or "").strip()
        if value:
            return value

    sub_url = str(user.get("subscription_url") or "").strip()
    if sub_url:
        pieces = [p for p in sub_url.split("/") if p]
        if pieces:
            return pieces[-1].split("?")[0]

    if fallback_uuid:
        return fallback_uuid[:8]
    return ""


def _user_to_compat(user: Dict[str, Any]) -> Dict[str, Any]:
    now_ts = int(time.time())
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    expire_ts = _normalize_expire_ts(user.get("expire") or user.get("expire_time") or 0)

    if expire_ts <= 0:
        package_days = 36500
        start_date = now_utc.strftime("%Y-%m-%d")
    else:
        package_days = max(0, int((expire_ts - now_ts) / 86400))
        start_ts = max(0, expire_ts - package_days * 86400)
        start_date = (
            datetime.datetime.fromtimestamp(start_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
            if start_ts
            else now_utc.strftime("%Y-%m-%d")
        )

    status = str(user.get("status") or "active").strip().lower()
    is_enabled = status in ("active", "on_hold", "limited")

    user_uuid = _extract_uuid_from_user(user)

    note_text, _ = _split_note_and_meta(user.get("note") or "")
    return {
        "uuid": user_uuid,
        "name": str(user.get("username") or ""),
        "last_online": "1-01-01 00:00:00",
        "expiry_time": expire_ts * 1000 if expire_ts else 0,
        "usage_limit_GB": _bytes_to_gb(user.get("data_limit") or 0),
        "package_days": package_days,
        "mode": str(user.get("data_limit_reset_strategy") or "no_reset"),
        "monthly": None,
        "start_date": start_date,
        "current_usage_GB": _bytes_to_gb(user.get("used_traffic") or 0),
        "last_reset_time": start_date,
        "comment": note_text,
        "telegram_id": _extract_telegram_id(user),
        "added_by": None,
        "max_ips": _extract_max_ips(user),
        "enable": is_enabled,
        "sub_id": _extract_sub_id(user, user_uuid),
        "connected_ips": [],
        "online_ips": [],
        "devices": [],
    }


def _parse_users_payload(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        payload_list = cast(List[Any], payload)
        users: List[Dict[str, Any]] = []
        for item in payload_list:
            if isinstance(item, dict):
                users.append(cast(Dict[str, Any], item))
        return users

    if isinstance(payload, dict):
        payload_dict = cast(Dict[str, Any], payload)
        for key in ("users", "items", "data"):
            value = payload_dict.get(key)
            if isinstance(value, list):
                value_list = cast(List[Any], value)
                users: List[Dict[str, Any]] = []
                for item in value_list:
                    if isinstance(item, dict):
                        users.append(cast(Dict[str, Any], item))
                return users

    return []


def _list_users_raw() -> List[Dict[str, Any]]:
    try:
        resp = _request("GET", "/api/users")
        if resp.status_code != 200:
            logging.error("Marzban users list failed: HTTP %s", resp.status_code)
            return []
        data: Any = resp.json() if resp.content else {}
        return _parse_users_payload(data)
    except Exception as exc:
        logging.error("Marzban list users error: %s", exc)
        return []


def _resolve_user_raw(identifier: Optional[str]) -> Optional[Dict[str, Any]]:
    target = str(identifier or "").strip()
    if not target:
        return None

    # Fast path: if identifier is username, direct endpoint avoids full scan.
    try:
        resp = _request("GET", f"/api/user/{quote(target, safe='')}")
        if resp.status_code == 200:
            payload: Any = resp.json() if resp.content else {}
            if isinstance(payload, dict):
                return cast(Dict[str, Any], payload)
    except Exception as exc:
        logging.debug("Marzban direct user resolve failed for %s: %s", target, exc)

    for user in _list_users_raw():
        username = str(user.get("username") or "")
        compat = _user_to_compat(user)
        if target == username:
            return user
        if target == str(compat.get("uuid") or ""):
            return user
        if target == str(compat.get("sub_id") or ""):
            return user

    return None


def _resolve_username(identifier: Optional[str]) -> Optional[str]:
    user = _resolve_user_raw(identifier)
    if not user:
        return None
    username = str(user.get("username") or "").strip()
    return username or None


def _parse_date_ymd(date_text: Optional[str]) -> datetime.date:
    raw = str(date_text or "").strip()
    if not raw:
        return datetime.datetime.now(datetime.timezone.utc).date()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except Exception:
            continue

    return datetime.datetime.now(datetime.timezone.utc).date()


def _days_to_expire_ts(package_days: Any, start_date: Optional[str]) -> int:
    try:
        days = int(float(package_days))
    except Exception:
        days = 0

    if days <= 0:
        return 0

    start_day = _parse_date_ymd(start_date)
    dt = datetime.datetime.combine(start_day, datetime.time.min, tzinfo=datetime.timezone.utc)
    return int(dt.timestamp()) + max(0, days) * 86400


def _gb_to_bytes(value: Any) -> int:
    try:
        gb = float(value)
    except Exception:
        gb = 0.0
    if gb <= 0:
        return 0
    return int(gb * (1024 ** 3))


def _normalize_mode(value: Any) -> str:
    mode = str(value or "no_reset").strip().lower()
    allowed = {"no_reset", "day", "week", "month", "year"}
    return mode if mode in allowed else "no_reset"


def _safe_username_seed(name: Optional[str]) -> str:
    raw = str(name or "user").strip().lower()
    clean = re.sub(r"[^a-z0-9_.@-]+", "_", raw).strip("_.@-")
    if len(clean) < 3:
        clean = (clean + "user")[:3]
    return clean[:20]


def _build_username(name: Optional[str], add_suffix: bool = True) -> str:
    seed = _safe_username_seed(name)
    if not add_suffix:
        return seed[:32]
    suffix = uuid.uuid4().hex[:8]
    base = seed[:23]
    return f"{base}-{suffix}"[:32]


def _parse_inbounds_payload(payload: Any) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    if not isinstance(payload, dict):
        return result

    payload_dict = cast(Dict[str, Any], payload)
    for protocol_raw, items in payload_dict.items():
        protocol = str(protocol_raw or "").strip().lower()
        if not protocol:
            continue
        tags: List[str] = []
        if isinstance(items, list):
            items_list = cast(List[Any], items)
            for item in items_list:
                if isinstance(item, dict):
                    item_dict = cast(Dict[str, Any], item)
                    tag = str(item_dict.get("tag") or "").strip()
                    if tag:
                        tags.append(tag)
                elif isinstance(item, str):
                    tag = str(item).strip()
                    if tag:
                        tags.append(tag)
        tags = list(dict.fromkeys(tags))
        if tags:
            result[protocol] = tags

    return result


def _available_inbounds_map() -> Dict[str, List[str]]:
    try:
        resp = _request("GET", "/api/inbounds")
        if resp.status_code != 200:
            return {}
        payload: Any = resp.json() if resp.content else {}
        return _parse_inbounds_payload(payload)
    except Exception as exc:
        logging.debug("Marzban get inbounds failed: %s", exc)
        return {}


def _selected_inbounds_map() -> Dict[str, List[str]]:
    available = _available_inbounds_map()
    if not available:
        return {}

    configured_tags = _cfg_inbound_tags()
    if not configured_tags:
        return available

    selected: Dict[str, List[str]] = {}
    configured_set = set(configured_tags)
    for protocol, tags in available.items():
        filtered = [t for t in tags if t in configured_set]
        if filtered:
            selected[protocol] = filtered

    if selected:
        return selected

    logging.warning("MARZBAN_INBOUND_TAGS configured but no tags matched active inbounds; using all available tags")
    return available


def _default_proxies(inbounds_map: Dict[str, List[str]]) -> Dict[str, Dict[str, Any]]:
    protocols = list(inbounds_map.keys())
    if not protocols:
        protocols = ["vless"]
    return {protocol: {} for protocol in protocols}


def select(url: Optional[str] = None, endpoint: Optional[Any] = None) -> Optional[List[Dict[str, Any]]]:
    del url, endpoint
    users = _list_users_raw()
    if not users:
        return []
    return [_user_to_compat(user) for user in users]


def find(url: Optional[str] = None, uuid: Optional[str] = None, endpoint: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    del url, endpoint
    target = str(uuid or "").strip()
    if not target:
        return None

    for user in _list_users_raw():
        compat = _user_to_compat(user)
        if compat.get("uuid") == target:
            return compat
        if str(user.get("username") or "") == target:
            return compat
        if str(compat.get("sub_id") or "") == target:
            return compat

    return None


def _unsupported_write(op_name: str) -> None:
    logging.error(
        "Marzban provider write operation '%s' is not enabled in phase-1 migration mode",
        op_name,
    )


def insert(url: Optional[str] = None, **kwargs: Any) -> Optional[str]:
    del url
    note_text = str(kwargs.get("comment") or "")
    note_plain, note_meta = _split_note_and_meta(note_text)

    max_ips = kwargs.get("max_ips")
    if max_ips is not None:
        try:
            note_meta["max_ips"] = str(max(0, int(max_ips)))
        except Exception:
            logging.warning("Marzban insert ignored invalid max_ips: %s", max_ips)

    telegram_id = kwargs.get("telegram_id")
    if telegram_id:
        note_meta["telegram_id"] = str(telegram_id).strip()

    inbounds_map = _selected_inbounds_map()
    payload: Dict[str, Any] = {
        "username": _build_username(cast(Optional[str], kwargs.get("name")), add_suffix=True),
        "status": "active" if bool(kwargs.get("enable", True)) else "disabled",
        "expire": _days_to_expire_ts(kwargs.get("package_days", 30), cast(Optional[str], kwargs.get("start_date"))),
        "data_limit": _gb_to_bytes(kwargs.get("usage_limit_GB", 0)),
        "data_limit_reset_strategy": _normalize_mode(kwargs.get("mode", "no_reset")),
        "note": _compose_note(note_plain, note_meta),
        "proxies": _default_proxies(inbounds_map),
    }
    if inbounds_map:
        payload["inbounds"] = inbounds_map

    # Retry with fresh suffix if username collides.
    for attempt in range(3):
        try:
            if attempt:
                payload["username"] = _build_username(cast(Optional[str], kwargs.get("name")), add_suffix=True)

            resp = _request("POST", "/api/user", json=payload)
            if resp.status_code == 409:
                continue
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail_payload: Any = resp.json() if resp.content else {}
                    detail = str(detail_payload)
                except Exception:
                    detail = str(resp.text[:250])
                logging.error("Marzban insert failed HTTP %s: %s", resp.status_code, detail)
                return None

            created_payload: Any = resp.json() if resp.content else {}
            if isinstance(created_payload, dict):
                compat = _user_to_compat(cast(Dict[str, Any], created_payload))
                created_uuid = str(compat.get("uuid") or "").strip()
                if created_uuid:
                    return created_uuid

            resolved = _resolve_user_raw(str(payload.get("username") or ""))
            if resolved:
                compat = _user_to_compat(resolved)
                resolved_uuid = str(compat.get("uuid") or "").strip()
                if resolved_uuid:
                    return resolved_uuid

            return str(payload.get("username") or "")
        except Exception as exc:
            logging.error("Marzban insert error: %s", exc)
            return None

    logging.error("Marzban insert failed: username collisions after retries")
    return None


def update(url: Optional[str] = None, uuid: Optional[str] = None, endpoint: Optional[Any] = None, **kwargs: Any) -> Optional[str]:
    del url, endpoint
    target = str(uuid or "").strip()
    if not target:
        return None

    current_user = _resolve_user_raw(target)
    if not current_user:
        logging.error("Marzban update failed: user not found for id '%s'", target)
        return None

    username = str(current_user.get("username") or "").strip()
    if not username:
        return None

    payload: Dict[str, Any] = {}

    if "name" in kwargs and kwargs.get("name"):
        # Marzban UserModify doesn't support username changes.
        logging.warning("Marzban update ignored 'name' change because username rename is not supported")

    if "usage_limit_GB" in kwargs:
        payload["data_limit"] = _gb_to_bytes(kwargs.get("usage_limit_GB"))

    if "mode" in kwargs:
        payload["data_limit_reset_strategy"] = _normalize_mode(kwargs.get("mode"))

    if "enable" in kwargs:
        payload["status"] = "active" if bool(kwargs.get("enable")) else "disabled"

    if "package_days" in kwargs or "start_date" in kwargs:
        package_days = kwargs.get("package_days")
        if package_days is None:
            current_expire_ts = _normalize_expire_ts(current_user.get("expire") or current_user.get("expire_time") or 0)
            now_ts = int(time.time())
            remaining = max(0, int((current_expire_ts - now_ts) / 86400)) if current_expire_ts else 0
            package_days = remaining if remaining > 0 else 30
        payload["expire"] = _days_to_expire_ts(package_days, cast(Optional[str], kwargs.get("start_date")))

    note_plain, note_meta = _split_note_and_meta(current_user.get("note") or "")
    if "comment" in kwargs:
        note_plain = str(kwargs.get("comment") or "")
    if "max_ips" in kwargs:
        max_ips = kwargs.get("max_ips")
        if max_ips is None or str(max_ips).strip() == "":
            note_meta.pop("max_ips", None)
        else:
            try:
                note_meta["max_ips"] = str(max(0, int(max_ips)))
            except Exception:
                logging.warning("Marzban update ignored invalid max_ips: %s", max_ips)
    if "telegram_id" in kwargs:
        telegram_id = kwargs.get("telegram_id")
        if telegram_id is None or str(telegram_id).strip() == "":
            note_meta.pop("telegram_id", None)
        else:
            note_meta["telegram_id"] = str(telegram_id).strip()
    if ("comment" in kwargs) or ("max_ips" in kwargs) or ("telegram_id" in kwargs):
        payload["note"] = _compose_note(note_plain, note_meta)

    try:
        if payload:
            resp = _request("PUT", f"/api/user/{quote(username, safe='')}", json=payload)
            if resp.status_code >= 400:
                detail = ""
                try:
                    detail_payload: Any = resp.json() if resp.content else {}
                    detail = str(detail_payload)
                except Exception:
                    detail = str(resp.text[:250])
                logging.error("Marzban update failed HTTP %s: %s", resp.status_code, detail)
                return None

        if "current_usage_GB" in kwargs:
            try:
                current_usage = float(kwargs.get("current_usage_GB") or 0)
            except Exception:
                current_usage = -1
            if current_usage == 0:
                reset_resp = _request("POST", f"/api/user/{quote(username, safe='')}/reset")
                if reset_resp.status_code >= 400:
                    logging.error("Marzban update reset step failed HTTP %s", reset_resp.status_code)
                    return None
            elif current_usage > 0:
                logging.warning("Marzban update ignored unsupported current_usage_GB=%s", current_usage)

        return target
    except Exception as exc:
        logging.error("Marzban update error: %s", exc)
        return None


def delete(url: Optional[str] = None, uuid: Optional[str] = None, endpoint: Optional[Any] = None) -> bool:
    del url, endpoint
    username = _resolve_username(uuid)
    if not username:
        return False

    try:
        resp = _request("DELETE", f"/api/user/{quote(username, safe='')}")
        if 200 <= resp.status_code < 300:
            return True
        logging.error("Marzban delete failed HTTP %s", resp.status_code)
        return False
    except Exception as exc:
        logging.error("Marzban delete error: %s", exc)
        return False


def get_panel_status(url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    del url
    system_paths = ["/api/system", "/api/core", "/api/admin"]
    for path in system_paths:
        try:
            resp = _request("GET", path)
            if resp.status_code == 200:
                raw_payload: Any = resp.json() if resp.content else {}
                payload: Dict[str, Any] = cast(Dict[str, Any], raw_payload if isinstance(raw_payload, dict) else {})
                payload.setdefault("provider", "marzban")
                return payload
        except Exception as exc:
            logging.debug("Marzban status check failed on %s: %s", path, exc)

    # Return provider marker so diagnostics can still display selected backend.
    if _base_url():
        return {"provider": "marzban"}
    return None


def reset_user_usage(url: Optional[str] = None, uuid: Optional[str] = None, endpoint: Optional[Any] = None) -> Optional[str]:
    del url, endpoint
    target = str(uuid or "").strip()
    username = _resolve_username(target)
    if not username:
        return None

    try:
        resp = _request("POST", f"/api/user/{quote(username, safe='')}/reset")
        if 200 <= resp.status_code < 300:
            return target
        logging.error("Marzban reset_user_usage failed HTTP %s", resp.status_code)
        return None
    except Exception as exc:
        logging.error("Marzban reset_user_usage error: %s", exc)
        return None


def reset_user_days(url: Optional[str] = None, uuid: Optional[str] = None, package_days: int = 30, endpoint: Optional[Any] = None) -> Optional[str]:
    del url, endpoint
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    return update(uuid=uuid, package_days=package_days, start_date=today)


def device_action(url: Optional[str] = None, uuid: Optional[str] = None, device_key: Optional[str] = None, action: str = "delete") -> bool:
    del url, uuid, device_key, action
    _unsupported_write("device_action")
    return False


def block_device(url: Optional[str] = None, uuid: Optional[str] = None, device_key: Optional[str] = None) -> bool:
    del url, uuid, device_key
    _unsupported_write("block_device")
    return False


def delete_device(url: Optional[str] = None, uuid: Optional[str] = None, device_key: Optional[str] = None) -> bool:
    del url, uuid, device_key
    _unsupported_write("delete_device")
    return False
