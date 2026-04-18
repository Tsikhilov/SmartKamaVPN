#!/usr/bin/env python3
import base64
import html
import json
import logging
import os
import re
import ssl
import sqlite3
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "Database", "smartkamavpn.db")
XUI_DB_PATH = os.getenv("XUI_DB_PATH") or "/etc/x-ui/x-ui.db"
HOST = "127.0.0.1"
PORT = 9101


UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_PATTERN = re.compile(UUID_RE)
PBK_PATTERN = re.compile(r"[?&]pbk=([^&#\s]+)", re.IGNORECASE)
SID_PATTERN = re.compile(r"[?&]sid=([^&#\s]*)", re.IGNORECASE)

_REALITY_PBK_CACHE = None
_REALITY_FP_CACHE = None
_REALITY_PORT_CACHE = None
_EXPORT_HOST_CACHE = None


# -------------------- Device Tracking --------------------
import datetime as _dt

_ANDROID_TV_MARKERS = (
    "android tv",
    "google tv",
    "googletv",
    "smart tv",
    "smarttv",
    "apple tv",
    "bravia",
    "shield",
    "chromecast",
    "mi box",
    "mibox",
)

# User-Agent substrings that indicate automated tools, bots, or server-side fetchers.
# Connections from these should NOT be counted as user devices.
_BOT_UA_MARKERS = (
    "curl/",
    "python-requests/",
    "python-httpx/",
    "python/",
    "telegrambot",
    "go-http-client/",
    "wget/",
    "libcurl/",
    "okhttp/",
    "java/",
    "axios/",
    "node-fetch/",
    "node.js",
    "ruby",
    "php/",
    "perl/",
    "lua-resty",
    "monitoring",
    "uptime",
    "healthcheck",
)


def _is_bot_user_agent(ua: str) -> bool:
    """Return True if the User-Agent belongs to an automated tool/bot, not a real user device."""
    if not ua:
        return False
    ua_lower = ua.lower()
    return any(marker in ua_lower for marker in _BOT_UA_MARKERS)


def _normalize_device_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"tv", "android tv", "android_tv", "smart tv", "apple tv"}:
        return "tv"
    if normalized in {"phone", "android", "ios", "iphone"}:
        return "phone"
    return "computer"


def _is_android_tv_user_agent(ua: str) -> bool:
    if any(marker in ua for marker in _ANDROID_TV_MARKERS):
        return True
    return bool(re.search(r"\baft[a-z0-9_-]*\b", ua))

def _classify_device(user_agent: str) -> tuple:
    """Classify device from User-Agent string.
    Returns (device_type, device_name, client_app).
    device_type: 'phone', 'computer', 'tv'
    """
    ua = (user_agent or "").lower()
    client_app = "unknown"
    device_name = ""
    device_type = "computer"

    # Detect VPN client app
    if "happ" in ua or "hiddify" in ua:
        client_app = "Hiddify"
    elif "v2raytun" in ua or "v2ray-tun" in ua:
        client_app = "V2RayTun"
    elif "v2ray" in ua:
        client_app = "V2Ray"
    elif "sing-box" in ua or "singbox" in ua:
        client_app = "sing-box"
    elif "nekobox" in ua or "nekoray" in ua:
        client_app = "NekoBox"
    elif "clash" in ua or "mihomo" in ua:
        client_app = "Clash"
    elif "streisand" in ua:
        client_app = "Streisand"
    elif "shadowrocket" in ua:
        client_app = "Shadowrocket"
    elif "quantumult" in ua:
        client_app = "Quantumult"
    elif "surge" in ua:
        client_app = "Surge"
    elif "stash" in ua:
        client_app = "Stash"

    # Detect device type and name
    if "ipad" in ua:
        device_type = "computer"
        device_name = "iPad"
    elif "iphone" in ua:
        device_type = "phone"
        device_name = "iPhone"
    elif _is_android_tv_user_agent(ua):
        device_type = "tv"
        if "apple tv" in ua:
            device_name = "Apple TV"
        elif "bravia" in ua:
            device_name = "Sony TV"
        else:
            device_name = "Android TV"
    elif "android" in ua:
        if "tablet" in ua or "sm-t" in ua or "gt-p" in ua:
            device_type = "computer"
            device_name = "Android Tablet"
        else:
            device_type = "phone"
            device_name = "Android"
            # Try to extract model
            m = re.search(r"android[^;]*;\s*([^)]+)", ua)
            if m:
                model = m.group(1).strip().split(" build")[0].strip()
                if model and len(model) < 40:
                    device_name = model
    elif "windows" in ua:
        device_type = "computer"
        device_name = "Windows PC"
    elif "macintosh" in ua or "mac os" in ua:
        device_type = "computer"
        device_name = "Mac"
    elif "linux" in ua:
        device_type = "computer"
        device_name = "Linux PC"

    return device_type, device_name, client_app


def _resolve_sub_uuid(sub_segment: str) -> str:
    """Resolve a subscription segment (short sub_id, marzban token, etc.) to UUID from order_subscriptions.

    Tries:
      1. Exact UUID match in order_subscriptions
      2. UUID starts with sub_segment (short prefix like '01dcf49b')
      3. id (numeric) match
      4. Marzban API: find user whose subscription_url contains the token → extract UUID
    Returns UUID string or the original sub_segment as fallback.
    """
    if not sub_segment:
        return sub_segment
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            # 1. Exact UUID
            row = conn.execute(
                "SELECT uuid FROM order_subscriptions WHERE uuid = ? LIMIT 1",
                (sub_segment,)
            ).fetchone()
            if row:
                return row[0]
            # 2. UUID prefix match
            row = conn.execute(
                "SELECT uuid FROM order_subscriptions WHERE uuid LIKE ? LIMIT 1",
                (sub_segment + '%',)
            ).fetchone()
            if row:
                return row[0]
            # 3. Numeric id match
            if sub_segment.isdigit():
                row = conn.execute(
                    "SELECT uuid FROM order_subscriptions WHERE id = ? LIMIT 1",
                    (int(sub_segment),)
                ).fetchone()
                if row:
                    return row[0]
        finally:
            conn.close()
    except Exception:
        pass
    # 4. Marzban API: resolve subscription token to user UUID
    try:
        resolved = _resolve_uuid_via_marzban(sub_segment)
        if resolved:
            return resolved
    except Exception:
        pass
    return sub_segment


def _resolve_uuid_via_marzban(sub_token: str) -> str:
    """Query Marzban API to find the user for this subscription token,
    then match their UUID(s) against order_subscriptions."""
    token = _marzban_api_token()
    if not token:
        return ""
    panel_url = os.getenv("MARZBAN_PANEL_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        # Marzban tokens are base64: {username},{timestamp}{random}
        # Decode to extract username for reliable lookup
        username = _decode_marzban_sub_token_username(sub_token)
        if not username:
            return ""
        req = urllib.request.Request(
            f"{panel_url}/api/users?search={urllib.parse.quote(username)}&limit=5",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            for user in data.get("users", []):
                if str(user.get("username") or "") != username:
                    continue
                # Collect all candidate UUIDs from this user
                candidates = []
                proxies = user.get("proxies") or {}
                for proto_cfg in proxies.values():
                    uid = str(proto_cfg.get("id") or "").strip()
                    if uid and len(uid) >= 32:
                        candidates.append(uid)
                # Also try UUID suffix from username (e.g. "test-01dcf49b")
                parts = username.rsplit("-", 1)
                if len(parts) == 2 and len(parts[1]) >= 8:
                    candidates.append(parts[1])
                # Match candidates against order_subscriptions
                conn = sqlite3.connect(DB_PATH)
                try:
                    for c in candidates:
                        row = conn.execute(
                            "SELECT uuid FROM order_subscriptions WHERE uuid = ? LIMIT 1", (c,)
                        ).fetchone()
                        if row:
                            return row[0]
                        row = conn.execute(
                            "SELECT uuid FROM order_subscriptions WHERE uuid LIKE ? LIMIT 1", (c + '%',)
                        ).fetchone()
                        if row:
                            return row[0]
                finally:
                    conn.close()
                # Fallback: return first proxy UUID
                if candidates:
                    return candidates[0]
    except Exception:
        pass
    return ""


def _decode_marzban_sub_token_username(sub_token: str) -> str:
    """Extract Marzban username from a subscription token (base64-encoded '{username},{ts}{rand}')."""
    import base64
    try:
        padded = sub_token + '=' * (-len(sub_token) % 4)
        decoded = base64.urlsafe_b64decode(padded).decode('utf-8', errors='replace')
        if ',' in decoded:
            return decoded.split(',', 1)[0].strip()
    except Exception:
        pass
    return ""


def _track_device_for_sub(path: str, headers, client_address, resolved_target_url=None):
    """Track device connection when subscription is fetched."""
    try:
        ua = headers.get("User-Agent") or ""
        if not ua or len(ua) < 3:
            return
        if _is_bot_user_agent(ua):
            return

        # Extract sub segment from path or resolved target URL
        sub_segment = ""
        # For /s/ paths, use the resolved target_url (which contains the actual /sub/ path)
        source = resolved_target_url or path
        if "/sub/" in source:
            sub_segment = source.split("/sub/", 1)[1].strip("/").split("?")[0].split("/")[0]
        elif "/s/" in source:
            sub_segment = source.split("/s/", 1)[1].strip("/").split("?")[0].split("/")[0]
        if not sub_segment:
            return

        # Resolve sub_segment to actual UUID
        sub_uuid = _resolve_sub_uuid(sub_segment)

        device_type, device_name, client_app = _classify_device(ua)
        client_ip = client_address[0] if client_address else None

        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute(
                "INSERT INTO device_connections (sub_uuid, user_agent, device_type, device_name, client_app, client_ip, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sub_uuid, user_agent) DO UPDATE SET "
                "last_seen=?, client_ip=?, device_type=?, device_name=?, client_app=?",
                (sub_uuid, ua[:500], device_type, device_name, client_app, client_ip, now, now,
                 now, client_ip, device_type, device_name, client_app)
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # Never break subscription delivery for tracking


def _get_device_limit_for_sub(sub_uuid: str) -> int:
    """Resolve the max device count for a subscription from order→plan chain.

    Returns total device limit (4 for individual / 8 for family) or 4 as default.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute(
                "SELECT o.plan_id FROM order_subscriptions os "
                "JOIN orders o ON o.id = os.order_id "
                "WHERE os.uuid = ? LIMIT 1",
                (sub_uuid,)
            ).fetchone()
            if row:
                plan_id = row[0]
                if plan_id and 2200 < int(plan_id) < 2300:
                    return 8  # family: 5 phones + 3 desktop = 8
                plan_row = conn.execute(
                    "SELECT description FROM plans WHERE id = ? LIMIT 1",
                    (plan_id,)
                ).fetchone()
                if plan_row:
                    desc = str(plan_row[0] or '').lower()
                    if '5 устрой' in desc or 'семейн' in desc or 'family' in desc:
                        return 8
        finally:
            conn.close()
    except Exception:
        pass
    return 4  # individual: 2 phones + 2 desktop = 4


def _count_devices_for_sub(sub_uuid: str) -> int:
    """Count tracked devices for a subscription UUID."""
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM device_connections WHERE sub_uuid = ?",
                (sub_uuid,)
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
    except Exception:
        return 0


def _is_device_over_limit(sub_uuid: str) -> bool:
    """Check whether the subscription has exceeded its device limit."""
    if not sub_uuid or len(sub_uuid) < 8:
        return False
    limit = _get_device_limit_for_sub(sub_uuid)
    count = _count_devices_for_sub(sub_uuid)
    return count > limit


_DEVICE_BLOCKED_PAYLOAD = (
    "# SmartKamaVPN — превышен лимит устройств\n"
    "# Зайдите в бот и удалите лишние устройства\n"
    "# в разделе Подписки → Устройства\n"
).encode("utf-8")

_DEVICE_BLOCKED_SINGBOX = {
    "log": {"level": "warn"},
    "outbounds": [
        {"type": "direct", "tag": "direct"},
        {"type": "block", "tag": "block"},
    ],
    "route": {"final": "block"},
}


# Operator-based profile ordering.
# Keys: operator slug → preferred order of transport categories.
# Categories: "ws", "grpc", "trojan", "reality", "vmess", "other".
# The default order (no operator) is ws → grpc → trojan → reality → vmess → other.
OPERATOR_PRIORITY = {
    "mts": ["reality", "trojan", "grpc", "ws", "vmess"],
    "beeline": ["reality", "trojan", "grpc", "ws", "vmess"],
    "tele2": ["ws", "grpc", "trojan", "reality", "vmess"],
    "megafon": ["trojan", "grpc", "ws", "reality", "vmess"],
    "yota": ["reality", "trojan", "grpc", "ws", "vmess"],
    # Happ desktop/mobile often follows top entries more aggressively; keep low-latency profiles first.
    "happ": ["reality", "trojan", "grpc", "ws", "vmess"],
}
DEFAULT_PRIORITY = ["ws", "grpc", "trojan", "reality", "vmess", "other"]


def is_subscription_target(target_url):
    parsed = urlparse(target_url)
    if parsed.scheme not in ("http", "https"):
        return False
    path = parsed.path or ""
    return path.endswith("/all.txt") or "/sub/" in path or path.endswith("/sub")


def is_browser_client(headers):
    ua = (headers.get("User-Agent") or "").lower()
    browser_hints = ("mozilla", "chrome", "safari", "webkit", "telegram")
    return any(h in ua for h in browser_hints)


def is_app_client(headers):
    ua = (headers.get("User-Agent") or "").lower()
    app_hints = (
        "happ",
        "hiddify",
        "v2ray",
        "v2raytun",
        "sing-box",
        "singbox",
        "clash",
        "mihomo",
        "nekobox",
    )
    return any(h in ua for h in app_hints)


def _client_hint_from_headers(headers):
    ua = (headers.get("User-Agent") or "").lower()
    if "happ" in ua:
        return "happ"
    if "v2raytun" in ua or "v2ray-tun" in ua:
        return "v2raytun"
    if "hiddify" in ua:
        return "hiddify"
    return None


def _resolve_client_hint(query, headers):
    explicit = (query.get("client") or [""])[0].lower().strip()
    if explicit:
        return explicit, True
    return _client_hint_from_headers(headers), False


def _resolve_operator_hint(operator, client_hint):
    if operator:
        return operator
    if client_hint == "happ":
        return "happ"
    return None


def build_install_page(target_url, token, meta):
    encoded = quote(target_url, safe="")

    escaped_target = html.escape(target_url, quote=True)
    deeplink_hiddify = f"hiddify://install-config?url={encoded}"
    deeplink_v2raytun = f"v2raytun://install-config?url={encoded}"
    deeplink_mtpromo = f"mtpromo://install-config?url={encoded}"
    deeplink_clash = f"clash://install-config?url={encoded}"
    deeplink_clash_meta = f"clashmeta://install-config?url={encoded}"
    raw_link = f"/s/{quote(token, safe='')}?raw=1"

    escaped_hiddify = html.escape(deeplink_hiddify, quote=True)
    escaped_v2raytun = html.escape(deeplink_v2raytun, quote=True)
    escaped_mtpromo = html.escape(deeplink_mtpromo, quote=True)
    escaped_clash = html.escape(deeplink_clash, quote=True)
    escaped_clash_meta = html.escape(deeplink_clash_meta, quote=True)
    escaped_raw_link = html.escape(raw_link, quote=True)

    remaining_days = meta.get("rd", "-")
    remaining_hours = meta.get("rh", "-")
    remaining_minutes = meta.get("rm", "-")
    usage_current = meta.get("uc", "-")
    usage_limit = meta.get("ul", "-")

    escaped_remaining = html.escape(f"{remaining_days} д. {remaining_hours} ч. {remaining_minutes} мин.", quote=True)
    escaped_usage = html.escape(f"{usage_current} / {usage_limit} ГБ", quote=True)

    js_sub_url = json.dumps(target_url)
    js_hiddify_link = json.dumps(deeplink_hiddify)

    return f"""<!doctype html>
<html lang=\"ru\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>SmartKamaVPN Установка</title>
    <style>
        :root {{
            --bg: #0b1220;
            --card: #121a2b;
            --text: #e6edf8;
            --muted: #9eb0cf;
            --accent: #3cb179;
            --accent2: #2a8cff;
            --border: #26334d;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif;
            color: var(--text);
            background: radial-gradient(circle at top right, #1a2742 0%, var(--bg) 55%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 16px;
        }}
        .card {{
            width: min(680px, 100%);
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 20px;
            box-shadow: 0 18px 48px rgba(0,0,0,.35);
        }}
        h1 {{ margin: 0 0 12px; font-size: 22px; }}
        p {{ margin: 0 0 12px; color: var(--muted); line-height: 1.5; }}
        .row {{ display: grid; gap: 10px; margin-top: 14px; }}
        .btn {{
            display: inline-block;
            text-decoration: none;
            color: #fff;
            padding: 12px 14px;
            border-radius: 10px;
            border: 1px solid transparent;
            font-weight: 600;
            text-align: center;
        }}
        .hiddify {{ background: var(--accent); }}
        .clash {{ background: var(--accent2); }}
        .secondary {{ background: #1a2438; border-color: var(--border); }}
        .url {{
            margin-top: 12px;
            padding: 10px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: #0f1728;
            color: #c9d8ef;
            word-break: break-all;
            font-size: 13px;
        }}
        .status {{ color: #9bd4b6; margin-top: 8px; font-size: 13px; }}
        .meta {{
            margin-top: 12px;
            padding: 12px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: #0f1728;
            color: #d8e5f7;
            font-size: 14px;
            line-height: 1.5;
        }}
    </style>
</head>
<body>
    <main class=\"card\">
        <h1>Открыть VPN-подписку</h1>
        <p>Пробуем автоматически открыть Hiddify. Если не сработало, используйте кнопки ниже.</p>
        <div class=\"meta\">
            ⏳ Осталось: {escaped_remaining}<br/>
            📊 Трафик: {escaped_usage}
        </div>
        <div class=\"row\">
            <a class=\"btn hiddify\" href=\"{escaped_hiddify}\">Открыть в Hiddify</a>
            <a class=\"btn hiddify\" href=\"{escaped_v2raytun}\">Открыть в V2RayTun</a>
            <a class=\"btn hiddify\" href=\"{escaped_mtpromo}\">Открыть в MTPromo</a>
            <a class=\"btn clash\" href=\"{escaped_clash}\">Открыть в Clash</a>
            <a class=\"btn clash\" href=\"{escaped_clash_meta}\">Открыть в Clash Meta</a>
            <a class=\"btn secondary\" href=\"{escaped_raw_link}\">Показать исходную ссылку подписки</a>
            <button class=\"btn secondary\" id=\"copyBtn\" type=\"button\">Скопировать ссылку подписки</button>
        </div>
        <div class=\"status\" id=\"status\">Пробуем запустить приложение...</div>
        <div class=\"url\" id=\"subUrl\">{escaped_target}</div>
    </main>
    <script>
        (function () {{
            const subUrl = {js_sub_url};
            const hiddifyLink = {js_hiddify_link};
            const status = document.getElementById('status');
            const copyBtn = document.getElementById('copyBtn');

            copyBtn.addEventListener('click', async function () {{
                try {{
                    await navigator.clipboard.writeText(subUrl);
                    status.textContent = 'Ссылка подписки скопирована.';
                }} catch (e) {{
                    status.textContent = 'Не удалось скопировать. Скопируйте вручную.';
                }}
            }});

            setTimeout(function () {{
                window.location.href = hiddifyLink;
            }}, 120);
        }})();
    </script>
</body>
</html>
""".encode("utf-8")


def _fetch_sub_userinfo(sub_url):
    """Fetch subscription-userinfo header from upstream to get usage data."""
    try:
        payload, _, headers = proxy_subscription_source(sub_url)
        if payload is None:
            return None
        info_str = (headers or {}).get("subscription-userinfo", "")
        if not info_str:
            return None
        info = {}
        for part in info_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                info[k.strip()] = v.strip()
        return info
    except Exception:
        return None


def _get_devices_for_sub(sub_id):
    """Get device connections for a subscription from SQLite."""
    try:
        resolved = _resolve_sub_uuid(sub_id)
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT device_type, device_name, client_app, last_seen FROM device_connections WHERE sub_uuid=? ORDER BY last_seen DESC",
            (resolved,),
        ).fetchall()
        conn.close()
        return [{"type": _normalize_device_type(r[0]), "name": r[1], "app": r[2], "last_seen": r[3]} for r in rows]
    except Exception:
        return []


def build_subscription_page(sub_id, sub_url, meta=None, devices=None):
    """Build a rich HTML subscription info page with SmartKamaVPN branding."""
    encoded = quote(sub_url, safe="")
    escaped_sub_url = html.escape(sub_url, quote=True)

    # Deeplinks for all supported apps
    apps = [
        {"name": "Hiddify Next", "scheme": "hiddify", "desc": "Рекомендуемый", "platforms": "iOS · Android · Windows · Mac · Linux",
         "color": "#6C63FF", "dl_android": "https://play.google.com/store/apps/details?id=app.hiddify.com", "dl_ios": "https://apps.apple.com/app/hiddify-proxy-vpn/id6596777532"},
        {"name": "Happ (V2RayTun)", "scheme": "v2raytun", "desc": "Sing-Box клиент", "platforms": "Android",
         "color": "#5B8DEF", "dl_android": "https://play.google.com/store/apps/details?id=com.v2raytun.android", "dl_ios": ""},
        {"name": "Streisand", "scheme": "streisand", "desc": "Простой и лёгкий", "platforms": "iOS",
         "color": "#E74C8B", "dl_android": "", "dl_ios": "https://apps.apple.com/app/streisand/id6450534064"},
    ]

    app_cards_html = ""
    for app in apps:
        deeplink = f"{app['scheme']}://install-config?url={encoded}"
        escaped_dl = html.escape(deeplink, quote=True)
        dl_links = ""
        if app.get("dl_android"):
            dl_links += f'<a class="store-link" href="{html.escape(app["dl_android"], quote=True)}" target="_blank">Google Play</a>'
        if app.get("dl_ios"):
            dl_links += f'<a class="store-link" href="{html.escape(app["dl_ios"], quote=True)}" target="_blank">App Store</a>'
        app_cards_html += f"""
        <div class="app-row">
            <div class="app-left">
                <div class="app-dot" style="background:{app['color']}"></div>
                <div>
                    <div class="app-name">{html.escape(app['name'])}</div>
                    <div class="app-desc">{html.escape(app['desc'])} &middot; {html.escape(app['platforms'])}</div>
                    <div class="store-links">{dl_links}</div>
                </div>
            </div>
            <div class="app-btns">
                <a class="btn accent" href="{escaped_dl}">Подключить</a>
                <button class="btn ghost copy-btn" data-link="{escaped_dl}" type="button">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
            </div>
        </div>"""

    # Meta info
    meta = meta or {}
    remaining_days = meta.get("rd", "-")
    remaining_hours = meta.get("rh", "-")
    remaining_minutes = meta.get("rm", "-")
    usage_current = meta.get("uc", "-")
    usage_limit = meta.get("ul", "-")

    progress_pct = 0
    try:
        uc = float(str(usage_current).replace(",", "."))
        ul = float(str(usage_limit).replace(",", "."))
        if ul > 0:
            progress_pct = min(100, int((uc / ul) * 100))
    except (ValueError, TypeError):
        pass

    days_class = "ok"
    try:
        rd = int(remaining_days)
        if rd <= 3:
            days_class = "crit"
        elif rd <= 7:
            days_class = "warn"
    except (ValueError, TypeError):
        pass

    progress_color = "#ef4444" if progress_pct > 80 else "#f59e0b" if progress_pct > 60 else "#10b981"

    # Devices section
    devices_html = ""
    devices = devices or []
    if devices:
        dev_icons = {"phone": "📱", "computer": "💻", "tv": "📺"}
        for d in devices[:10]:
            icon = dev_icons.get(_normalize_device_type(d.get("type")), "💻")
            dname = html.escape(d.get("name") or "Неизвестно")
            dapp = html.escape(d.get("app") or "—")
            dlast = html.escape(d.get("last_seen") or "—")
            devices_html += f"""
            <div class="dev-row">
                <span class="dev-icon">{icon}</span>
                <div class="dev-info"><span class="dev-name">{dname}</span><span class="dev-app">{dapp}</span></div>
                <span class="dev-time">{dlast}</span>
            </div>"""
    else:
        devices_html = '<div class="empty-state">Устройства появятся после первого подключения</div>'

    js_sub_url = json.dumps(sub_url)

    # FAQ items
    faq_items = [
        ("Как подключиться?", "Нажмите кнопку «Подключить» напротив нужного приложения. Если приложение установлено — конфигурация импортируется автоматически. Если нет — сначала установите его из магазина."),
        ("Какое приложение выбрать?", "<b>Hiddify Next</b> — универсальный, работает на всех платформах. <b>Happ</b> (V2RayTun) — хорошо работает на Android. <b>Streisand</b> — для iOS."),
        ("Подписка не работает?", "Нажмите кнопку обновления подписки в приложении (иконка 🔄). Если не помогло — удалите подписку и добавьте заново через кнопку «Подключить»."),
        ("Можно ли использовать на нескольких устройствах?", "Да, подписку можно добавить на несколько устройств. Количество одновременных подключений зависит от вашего тарифа."),
        ("Как продлить подписку?", "Напишите нашему боту в Telegram — @SmartKamaVPN_bot. Оплатите продление и подписка обновится автоматически."),
    ]
    faq_html = ""
    for i, (q, a) in enumerate(faq_items):
        faq_html += f"""
        <details class="faq-item"><summary>{html.escape(q)}</summary><div class="faq-answer">{a}</div></details>"""

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>SmartKamaVPN — Подписка</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#0a0e1a;--surf:#111827;--surf2:#1f2937;--border:#374151;--text:#f3f4f6;--muted:#9ca3af;--accent:#6366f1;--accent2:#818cf8;--green:#10b981;--yellow:#f59e0b;--red:#ef4444;--radius:12px}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:var(--text);background:var(--bg);min-height:100vh}}
.wrap{{max-width:480px;margin:0 auto;padding:0 16px 32px}}

/* ── Hero Banner ── */
.hero{{
  position:relative;overflow:hidden;
  background:linear-gradient(135deg,#1e1b4b 0%,#312e81 30%,#4338ca 60%,#6366f1 100%);
  padding:32px 20px 28px;text-align:center;
  border-radius:0 0 24px 24px;margin-bottom:20px;
}}
.hero::before{{
  content:'';position:absolute;top:-40%;right:-20%;width:300px;height:300px;
  background:radial-gradient(circle,rgba(99,102,241,.3) 0%,transparent 70%);
  border-radius:50%;
}}
.hero::after{{
  content:'';position:absolute;bottom:-30%;left:-15%;width:250px;height:250px;
  background:radial-gradient(circle,rgba(16,185,129,.2) 0%,transparent 70%);
  border-radius:50%;
}}
.hero-content{{position:relative;z-index:1}}
.logo-shield{{
  display:inline-flex;align-items:center;justify-content:center;
  width:64px;height:64px;border-radius:18px;
  background:rgba(255,255,255,.12);backdrop-filter:blur(8px);
  margin-bottom:12px;font-size:32px;
}}
.hero h1{{font-size:22px;font-weight:800;letter-spacing:-.5px;margin-bottom:4px}}
.hero .brand-sub{{color:rgba(255,255,255,.65);font-size:13px}}

/* ── Cards ── */
.card{{background:var(--surf);border:1px solid var(--border);border-radius:var(--radius);padding:16px;margin-bottom:12px}}
.card-head{{display:flex;align-items:center;gap:8px;margin-bottom:14px}}
.card-head svg{{width:18px;height:18px;color:var(--accent2);flex-shrink:0}}
.card-label{{font-size:13px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}

/* ── Stats ── */
.stats-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.stat{{background:var(--surf2);border-radius:10px;padding:14px;border:1px solid var(--border)}}
.stat-lbl{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.3px}}
.stat-val{{font-size:22px;font-weight:800;margin-top:2px}}
.stat-val.ok{{color:var(--green)}}.stat-val.warn{{color:var(--yellow)}}.stat-val.crit{{color:var(--red)}}
.stat-val small{{font-size:12px;font-weight:400;color:var(--muted)}}
.progress-wrap{{margin-top:14px}}
.progress-lbl{{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:6px}}
.progress-bar{{height:6px;background:var(--surf2);border-radius:3px;overflow:hidden}}
.progress-fill{{height:100%;border-radius:3px;transition:width .4s ease}}

/* ── Sub link ── */
.link-box{{display:flex;align-items:center;gap:8px;background:var(--surf2);border:1px solid var(--border);border-radius:10px;padding:10px 12px}}
.link-text{{flex:1;font-family:'SF Mono',Monaco,Consolas,monospace;font-size:11px;color:#d1d5db;word-break:break-all;line-height:1.4}}
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:6px;text-decoration:none;color:#fff;padding:8px 14px;border-radius:8px;font-size:13px;font-weight:600;border:none;cursor:pointer;white-space:nowrap;transition:all .15s}}
.btn.accent{{background:var(--accent)}}.btn.accent:active{{background:#4f46e5}}
.btn.green{{background:var(--green)}}.btn.green:active{{background:#059669}}
.btn.ghost{{background:transparent;border:1px solid var(--border);color:var(--muted);padding:8px 10px}}
.btn.ghost:active{{border-color:var(--accent);color:var(--accent)}}
.btn.copied{{background:var(--green)!important;border-color:var(--green)!important;color:#fff!important}}

/* ── Apps ── */
.app-row{{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border)}}
.app-row:last-child{{border-bottom:none}}
.app-left{{display:flex;align-items:flex-start;gap:10px;flex:1;min-width:0}}
.app-dot{{width:10px;height:10px;border-radius:50%;margin-top:5px;flex-shrink:0}}
.app-name{{font-size:14px;font-weight:700}}
.app-desc{{font-size:11px;color:var(--muted);margin-top:1px}}
.store-links{{display:flex;gap:8px;margin-top:4px}}
.store-link{{font-size:11px;color:var(--accent2);text-decoration:none}}
.store-link:hover{{text-decoration:underline}}
.app-btns{{display:flex;gap:6px;flex-shrink:0}}

/* ── Devices ── */
.dev-row{{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)}}
.dev-row:last-child{{border-bottom:none}}
.dev-icon{{font-size:18px}}.dev-info{{flex:1;display:flex;flex-direction:column}}
.dev-name{{font-size:13px;font-weight:600}}.dev-app{{font-size:11px;color:var(--muted)}}
.dev-time{{font-size:11px;color:var(--muted);white-space:nowrap}}
.empty-state{{text-align:center;color:var(--muted);padding:16px;font-size:13px}}

/* ── FAQ ── */
.faq-item{{border-bottom:1px solid var(--border)}}
.faq-item:last-child{{border-bottom:none}}
.faq-item summary{{padding:12px 0;font-size:14px;font-weight:600;cursor:pointer;list-style:none;display:flex;align-items:center;justify-content:space-between}}
.faq-item summary::after{{content:'＋';color:var(--muted);font-size:16px;transition:transform .2s}}
.faq-item[open] summary::after{{content:'−'}}
.faq-answer{{padding:0 0 12px;font-size:13px;color:var(--muted);line-height:1.6}}
.faq-answer b{{color:var(--text);font-weight:600}}

/* ── Footer ── */
.footer{{text-align:center;padding:20px 0 8px;color:var(--muted);font-size:11px}}
.footer a{{color:var(--accent2);text-decoration:none}}

/* ── Toast ── */
.toast{{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--accent);color:#fff;padding:10px 20px;border-radius:10px;font-size:14px;font-weight:600;opacity:0;transition:all .3s;pointer-events:none;z-index:100}}
.toast.show{{transform:translateX(-50%) translateY(0);opacity:1}}
</style>
</head>
<body>
<div class="hero">
    <div class="hero-content">
        <div class="logo-shield">🛡️</div>
        <h1>SmartKamaVPN</h1>
        <div class="brand-sub">Быстрый и надёжный VPN-сервис</div>
    </div>
</div>

<div class="wrap">
    <!-- Status -->
    <div class="card">
        <div class="card-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
            <span class="card-label">Статус подписки</span>
        </div>
        <div class="stats-grid">
            <div class="stat">
                <div class="stat-lbl">Осталось</div>
                <div class="stat-val {days_class}">{html.escape(str(remaining_days))} <small>дн.</small></div>
            </div>
            <div class="stat">
                <div class="stat-lbl">Часы : Минуты</div>
                <div class="stat-val ok">{html.escape(str(remaining_hours))}:{html.escape(str(remaining_minutes))}</div>
            </div>
        </div>
        <div class="progress-wrap">
            <div class="progress-lbl">
                <span>Трафик: {html.escape(str(usage_current))} ГБ</span>
                <span>из {html.escape(str(usage_limit))} ГБ</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill" style="width:{progress_pct}%;background:{progress_color}"></div>
            </div>
        </div>
    </div>

    <!-- Subscription Link -->
    <div class="card">
        <div class="card-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            <span class="card-label">Ссылка подписки</span>
        </div>
        <div class="link-box">
            <div class="link-text" id="subUrl">{escaped_sub_url}</div>
            <button class="btn green" id="copyMain" type="button">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                Копировать
            </button>
        </div>
    </div>

    <!-- Apps -->
    <div class="card">
        <div class="card-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"/><line x1="12" y1="18" x2="12.01" y2="18"/></svg>
            <span class="card-label">Подключение</span>
        </div>
        <p style="font-size:12px;color:var(--muted);margin-bottom:8px">Выберите приложение и нажмите «Подключить» — конфигурация импортируется автоматически.</p>
        {app_cards_html}
    </div>

    <!-- Devices -->
    <div class="card">
        <div class="card-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
            <span class="card-label">Устройства</span>
        </div>
        {devices_html}
    </div>

    <!-- FAQ -->
    <div class="card">
        <div class="card-head">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
            <span class="card-label">Частые вопросы</span>
        </div>
        {faq_html}
    </div>

    <div class="footer">
        SmartKamaVPN &copy; 2026 &middot;
        <a href="https://t.me/SmartKamaVPN_bot">Telegram-бот</a>
    </div>
</div>

<div class="toast" id="toast"></div>
<script>
(function(){{
    const subUrl={js_sub_url};
    const toast=document.getElementById('toast');
    let tt;
    function showToast(m){{toast.textContent=m;toast.classList.add('show');clearTimeout(tt);tt=setTimeout(()=>toast.classList.remove('show'),2000)}}
    async function cp(text,btn){{
        try{{
            await navigator.clipboard.writeText(text);
            if(btn){{const o=btn.innerHTML;btn.classList.add('copied');btn.innerHTML='✓';setTimeout(()=>{{btn.classList.remove('copied');btn.innerHTML=o}},1500)}}
            showToast('Скопировано!');
        }}catch(e){{showToast('Ошибка копирования')}}
    }}
    document.getElementById('copyMain').addEventListener('click',function(){{cp(subUrl,this)}});
    document.querySelectorAll('.copy-btn').forEach(b=>b.addEventListener('click',function(){{cp(this.dataset.link,this)}}));
}})();
</script>
</body>
</html>
""".encode("utf-8")


_RU_INJECT_JS = """
<script>
(function(){
  var MAX_TRIES = 80;
  var tries = 0;
    var textMap = {
        'Welcome': 'Добро пожаловать',
        'Choose your preferred language:': 'Выберите предпочитаемый язык:',
        'Import To App': 'Импорт в приложение',
        'Copy Link': 'Скопировать ссылку',
        'Open Telegram': 'Открыть Telegram',
        'Setup Guide': 'Инструкция по настройке',
        'Remaining time': 'Оставшееся время',
        'Days': 'Дни',
        'Hours': 'Часы',
        'Minutes': 'Минуты',
        'Total': 'Всего',
        'Used': 'Использовано',
        'Remaining': 'Осталось',
        'Account': 'Аккаунт',
        'Profile': 'Профиль',
        'Download QR': 'Скачать QR',
        'Download': 'Скачать',
        'Copy': 'Скопировать',
        'Open': 'Открыть',
        'Subscription': 'Подписка',
        'No Time Limit': 'Без ограничения по времени',
        'No Data Limit': 'Без лимита трафика',
        'Remaining Traffic': 'Оставшийся трафик',
        'Remaining Time': 'Оставшееся время',
        'Support': 'Поддержка',
        'View More': 'Показать больше',
        'Home': 'Главная',
        'Devices': 'Устройства',
        'Settings': 'Параметры',
        'Dashboard': 'Панель управления',
        'Traffic': 'Трафик',
        'Data': 'Данные',
        'Time': 'Время',
        'Expiration': 'Окончание',
        'Admin': 'Администратор',
        'User': 'Пользователь',
        'Active': 'Активно',
        'Inactive': 'Неактивно',
        'Disabled': 'Отключено',
        'Enable': 'Включить',
        'Disable': 'Отключить',
        'Delete': 'Удалить',
        'Edit': 'Редактировать',
        'Save': 'Сохранить',
        'Cancel': 'Отмена',
        'Confirm': 'Подтвердить',
        'Loading': 'Загрузка',
        'Error': 'Ошибка',
        'Success': 'Успешно',
        'Warning': 'Предупреждение',
        'Info': 'Информация',
        'Yes': 'Да',
        'No': 'Нет',
        'OK': 'ОК',
        'Close': 'Закрыть',
        'Back': 'Назад',
        'Next': 'Далее',
        'Previous': 'Назад',
        'First': 'Первая',
        'Last': 'Последняя',
        'Page': 'Страница',
        'Search': 'Поиск',
        'Filter': 'Фильтр',
        'Sort': 'Сортировка',
        'Export': 'Экспорт',
        'Import': 'Импорт',
        'Share': 'Поделиться',
        'Logout': 'Выход',
        'Login': 'Вход',
        'Register': 'Регистрация',
        'Password': 'Пароль',
        'Username': 'Имя пользователя',
        'Email': 'Электронная почта',
        'Phone': 'Телефон',
        'Address': 'Адрес',
        'Name': 'Имя',
        'Language': 'Язык',
        'Theme': 'Тема'
    };

    function translateNodeText(root){
        try {
            var walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT, null);
            var n;
            while ((n = walker.nextNode())) {
                var val = (n.nodeValue || '').trim();
                if (!val) continue;
                if (textMap[val]) {
                    n.nodeValue = n.nodeValue.replace(val, textMap[val]);
                    continue;
                }

                // Dynamic fragments (with variables) that are not exact dictionary keys.
                if (val.indexOf('Welcome, ') === 0) {
                    n.nodeValue = n.nodeValue.replace('Welcome, ', 'Добро пожаловать, ');
                    continue;
                }
                if (val.indexOf('Used Traffic: ') === 0) {
                    n.nodeValue = n.nodeValue.replace('Used Traffic: ', 'Использовано трафика: ');
                    continue;
                }
            }
        } catch(e) {}
    }

    function translateAttrs(){
        try {
            ['button','a','span','div','h1','h2','h3','p','label'].forEach(function(sel){
                document.querySelectorAll(sel).forEach(function(el){
                    var t = (el.innerText || '').trim();
                    if (textMap[t] && el.childElementCount === 0) {
                        el.innerText = textMap[t];
                    }
                });
            });
            document.querySelectorAll('[placeholder]').forEach(function(el){
                var p = (el.getAttribute('placeholder') || '').trim();
                if (textMap[p]) el.setAttribute('placeholder', textMap[p]);
            });
            document.querySelectorAll('[title]').forEach(function(el){
                var t = (el.getAttribute('title') || '').trim();
                if (textMap[t]) el.setAttribute('title', textMap[t]);
            });
        } catch(e) {}
    }

    function applyFallbackRU(){
        translateNodeText(document.body);
        translateAttrs();
    }

  function trySetLang(){
    tries++;
    // Attempt 1: patch i18next instance exposed by React
        if(window.__i18n_patched){
            applyFallbackRU();
            return;
        }
    var candidates = [];
    // look for i18next on window
    Object.keys(window).forEach(function(k){
      try{
        var v=window[k];
        if(v && typeof v.changeLanguage==='function' && typeof v.language==='string'){
          candidates.push(v);
        }
      }catch(e){}
    });
    if(candidates.length){
      candidates.forEach(function(i18n){
        if(i18n.language!=='ru') i18n.changeLanguage('ru').catch(function(){});
      });
      window.__i18n_patched=true;
            applyFallbackRU();
      return;
    }
    // Attempt 2: try React fiber tree to find i18n context
    var root=document.getElementById('root');
    if(root){
      var fk=Object.keys(root).find(function(k){return k.startsWith('__reactFiber')||k.startsWith('__reactInternalInstance');});
      if(fk){
        var node=root[fk];
        var depth=0;
        while(node && depth<200){
          var mi=node.memoizedProps||node.pendingProps||{};
          if(mi && mi.i18n && typeof mi.i18n.changeLanguage==='function'){
            if(mi.i18n.language!=='ru') mi.i18n.changeLanguage('ru').catch(function(){});
            window.__i18n_patched=true;
                        applyFallbackRU();
            return;
          }
          node=node.return||null;
          depth++;
        }
      }
    }
    if(tries<MAX_TRIES) setTimeout(trySetLang, 100);
        applyFallbackRU();
  }

    // Keep UI translated even when React rerenders asynchronously.
    var mo = new MutationObserver(function(){ applyFallbackRU(); });
    document.addEventListener('DOMContentLoaded', function(){
        applyFallbackRU();
        try {
            mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
        } catch(e) {}
    });

  setTimeout(trySetLang, 200);
})();
</script>
""".encode("utf-8")

def _build_home_url(target_url):
    """Given a sub/?asn=unknown style URL build the ?home=true counterpart."""
    parsed = urlparse(target_url)
    parts = [p for p in parsed.path.split("/") if p]
    # Find UUID segment
    uuid_idx = None
    for i, p in enumerate(parts):
        if UUID_PATTERN.fullmatch(p):
            uuid_idx = i
            break
    if uuid_idx is None:
        return None
    # Rebuild path: /<client_path>/<uuid>/
    home_path = "/" + "/".join(parts[:uuid_idx + 1]) + "/"
    home_url = urlunparse((parsed.scheme, parsed.netloc, home_path, "", "home=true", ""))
    return home_url


def proxy_home_page(target_url):
    """Fetch the Hiddify home page, inject Russian language switcher, return bytes."""
    home_url = _build_home_url(target_url)
    if not home_url:
        return None
    try:
        req = urllib.request.Request(
            home_url,
            headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
                "Accept": "text/html,*/*",
                "Accept-Language": "ru-RU,ru;q=0.9",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            page_bytes = resp.read()
    except Exception:
        return None

    # Keep relative JS/CSS/image paths valid when page is served from /s/<token>?home=1.
    try:
        base_tag = f'<base href="{home_url}">'.encode('utf-8')
        if b'<head>' in page_bytes:
            page_bytes = page_bytes.replace(b'<head>', b'<head>' + base_tag, 1)
        elif b'</head>' in page_bytes:
            page_bytes = page_bytes.replace(b'</head>', base_tag + b'</head>', 1)
    except Exception:
        pass

    # Inject the language-switch script before </body>
    inject_point = b"</body>"
    if inject_point in page_bytes:
        page_bytes = page_bytes.replace(inject_point, _RU_INJECT_JS + inject_point, 1)
    else:
        page_bytes = page_bytes + _RU_INJECT_JS

    # Patch page title
    page_bytes = page_bytes.replace(b"<title>Hiddify | Panel</title>", b"<title>SmartKamaVPN</title>")

    return page_bytes


# ---------------------------------------------------------------------------
# Marzban short sub_id → real subscription token resolver
# ---------------------------------------------------------------------------
_MARZBAN_TOKEN_CACHE = {"token": None, "expires": 0}


def _marzban_api_token():
    """Get (cached) Marzban admin JWT token."""
    import time
    now = time.time()
    if _MARZBAN_TOKEN_CACHE["token"] and now < _MARZBAN_TOKEN_CACHE["expires"]:
        return _MARZBAN_TOKEN_CACHE["token"]
    panel_url = os.getenv("MARZBAN_PANEL_URL", "http://127.0.0.1:8000").rstrip("/")
    username = os.getenv("MARZBAN_USERNAME", "")
    password = os.getenv("MARZBAN_PASSWORD", "")
    if not username or not password:
        return None
    try:
        data = urlencode({"username": username, "password": password}).encode()
        req = urllib.request.Request(
            f"{panel_url}/api/admin/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            tok = body.get("access_token")
            if tok:
                _MARZBAN_TOKEN_CACHE["token"] = tok
                _MARZBAN_TOKEN_CACHE["expires"] = now + 3500
                return tok
    except Exception:
        pass
    return None


def _resolve_marzban_sub_path(short_id):
    """Resolve a short bot sub_id to the actual Marzban /sub/<token> path.

    Searches Marzban users for one whose username ends with the short_id
    and returns the subscription token extracted from subscription_url.
    """
    token = _marzban_api_token()
    if not token:
        return None
    panel_url = os.getenv("MARZBAN_PANEL_URL", "http://127.0.0.1:8000").rstrip("/")
    try:
        req = urllib.request.Request(
            f"{panel_url}/api/users?search={short_id}&limit=5",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            for user in data.get("users", []):
                sub_url = user.get("subscription_url", "")
                if "/sub/" in sub_url:
                    real_token = sub_url.split("/sub/")[-1].split("?")[0]
                    if real_token and real_token != short_id:
                        return f"/sub/{real_token}"
    except Exception:
        pass
    return None


def proxy_subscription_source(target_url):
    try:
        parsed = urlparse(target_url)
        export_host = _load_export_host()
        if (
            parsed.scheme == "https"
            and parsed.path.startswith("/sub/")
            and export_host
            and parsed.hostname == export_host
            and (parsed.port in (None, 2096))
        ):
            target_url = urlunparse(("http", "127.0.0.1:8000", parsed.path, parsed.params, parsed.query, parsed.fragment))
            parsed = urlparse(target_url)

        open_kwargs = {"timeout": 20}
        headers = {
            "User-Agent": "SmartKamaShortlink/1.0",
            "Accept": "text/plain,*/*",
        }
        if parsed.scheme == "https" and parsed.hostname in ("127.0.0.1", "localhost"):
            open_kwargs["context"] = ssl._create_unverified_context()
            if export_host:
                headers["Host"] = export_host
        req = urllib.request.Request(
            target_url,
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "text/plain; charset=utf-8")
            passthrough_headers = {}
            for h in (
                "profile-title",
                "subscription-userinfo",
                "profile-web-page-url",
                "support-url",
                "profile-update-interval",
                "content-disposition",
            ):
                v = resp.headers.get(h)
                if v:
                    if h == "profile-title":
                        v = _encode_profile_title(v)
                    passthrough_headers[h] = v
            return data, content_type, passthrough_headers
    except Exception:
        return None, None, {}


def _extract_uuid(target_url, text):
    path_match = re.search(rf"/(?:[^/]+)/({UUID_RE})/", target_url)
    if path_match:
        return path_match.group(1)
    text_match = re.search(rf"vless://({UUID_RE})@", text, flags=re.IGNORECASE)
    if text_match:
        return text_match.group(1)
    return None


def _extract_reality_keys(text):
    pbk_match = PBK_PATTERN.search(text)
    sid_match = SID_PATTERN.search(text)
    if not pbk_match:
        return None, None
    pbk = pbk_match.group(1)
    sid = sid_match.group(1) if sid_match else "f9"
    sid = sid or "f9"
    return pbk, sid


def _load_reality_public_key():
    global _REALITY_PBK_CACHE
    if _REALITY_PBK_CACHE:
        return _REALITY_PBK_CACHE

    env_pbk = (os.getenv("THREEXUI_REALITY_PUBLIC_KEY") or "").strip()
    if env_pbk:
        _REALITY_PBK_CACHE = env_pbk
        return _REALITY_PBK_CACHE

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute(
            "SELECT value FROM str_config WHERE key='threexui_reality_public_key' LIMIT 1"
        ).fetchone()
        if row and row[0]:
            _REALITY_PBK_CACHE = str(row[0]).strip()
    except Exception:
        return None
    finally:
        conn.close()

    return _REALITY_PBK_CACHE


def _load_reality_port_map():
    global _REALITY_PORT_CACHE
    if _REALITY_PORT_CACHE is not None:
        return _REALITY_PORT_CACHE

    port_map = {}
    if not os.path.exists(XUI_DB_PATH):
        _REALITY_PORT_CACHE = port_map
        return _REALITY_PORT_CACHE

    conn = sqlite3.connect(XUI_DB_PATH)
    try:
        rows = conn.execute("SELECT port, stream_settings FROM inbounds").fetchall()
        for port, raw_stream in rows:
            try:
                stream = json.loads(raw_stream or "{}")
            except Exception:
                continue
            if str(stream.get("security") or "").lower() != "reality":
                continue

            reality = dict(stream.get("realitySettings") or {})
            short_ids = reality.get("shortIds") or []
            sid = str(short_ids[0]).strip() if short_ids else ""
            fp = str(reality.get("fingerprint") or "").strip().lower()
            pbk = str(reality.get("publicKey") or "").strip()
            try:
                port_key = int(port)
            except Exception:
                continue
            port_map[port_key] = {
                "pbk": pbk or None,
                "fp": fp or None,
                "sid": sid or None,
            }
    except Exception:
        port_map = {}
    finally:
        conn.close()

    _REALITY_PORT_CACHE = port_map
    return _REALITY_PORT_CACHE


def _load_reality_fingerprint():
    global _REALITY_FP_CACHE
    if _REALITY_FP_CACHE:
        return _REALITY_FP_CACHE

    env_fp = (os.getenv("THREEXUI_REALITY_FINGERPRINT") or "").strip().lower()
    if env_fp:
        _REALITY_FP_CACHE = env_fp
        return _REALITY_FP_CACHE

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute(
            "SELECT value FROM str_config WHERE key='threexui_reality_fingerprint' LIMIT 1"
        ).fetchone()
        if row and row[0]:
            _REALITY_FP_CACHE = str(row[0]).strip().lower()
    except Exception:
        return "chrome"
    finally:
        conn.close()

    return _REALITY_FP_CACHE or "chrome"


def _load_export_host(request_host=None):
    global _EXPORT_HOST_CACHE
    if _EXPORT_HOST_CACHE:
        return _EXPORT_HOST_CACHE

    env_host = (os.getenv("SUB_EXPORT_HOST") or "").strip()
    if env_host:
        _EXPORT_HOST_CACHE = env_host.split(":", 1)[0].strip()
        return _EXPORT_HOST_CACHE

    if os.path.exists(XUI_DB_PATH):
        conn = sqlite3.connect(XUI_DB_PATH)
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='subDomain' LIMIT 1").fetchone()
            if row and row[0]:
                _EXPORT_HOST_CACHE = str(row[0]).strip().split(":", 1)[0]
                return _EXPORT_HOST_CACHE
        except Exception:
            pass
        finally:
            conn.close()

    host = (request_host or "").strip()
    if host:
        host = host.split(":", 1)[0].strip()
        if host not in ("127.0.0.1", "localhost"):
            return host


def _build_external_host(headers):
    forwarded_host = (headers.get("X-Forwarded-Host") or headers.get("Host") or "").split(",", 1)[0].strip()
    export_host = _load_export_host(forwarded_host)
    host = export_host or forwarded_host or "sub.smartkama.ru"
    host_only = host.split(":", 1)[0].strip()

    forwarded_port = (headers.get("X-Forwarded-Port") or "").split(",", 1)[0].strip()
    if not forwarded_port and ":" in forwarded_host and not forwarded_host.startswith("["):
        forwarded_port = forwarded_host.rsplit(":", 1)[-1].strip()

    proto = (headers.get("X-Forwarded-Proto") or "https").split(",", 1)[0].strip().lower() or "https"
    if forwarded_port and ((proto == "https" and forwarded_port != "443") or (proto == "http" and forwarded_port != "80")):
        return f"{host_only}:{forwarded_port}"
    return host_only
    return None


def _rewrite_uri_host(line: str, export_host: str | None) -> str:
    if not export_host:
        return line
    if not (line.startswith("vless://") or line.startswith("trojan://")):
        return line
    # REALITY inbounds must keep raw server IP for correct sing-box operation
    _uri_q = line.split("#", 1)[0]
    _params_q = dict(parse_qsl(urlparse(_uri_q).query, keep_blank_values=True))
    if (_params_q.get("security") or "").lower() == "reality":
        return line


    uri_part, sep, fragment = line.partition("#")
    parsed = urlparse(uri_part)
    userinfo, at, hostport = parsed.netloc.rpartition("@")
    host, colon, port = hostport.rpartition(":")
    if not colon:
        host = hostport
        port = ""
    netloc_host = f"{export_host}:{port}" if port else export_host
    new_netloc = f"{userinfo}@{netloc_host}" if at else netloc_host
    rebuilt = urlunparse((parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return rebuilt + (sep + fragment if sep else "")


def _resolve_reality_params(line: str, fallback_pbk, fallback_fp, fallback_sid):
    uri_part = line.split("#", 1)[0]
    parsed = urlparse(uri_part)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if (params.get("security") or "").lower() != "reality":
        return fallback_pbk, fallback_fp, fallback_sid

    hostport = parsed.netloc.rsplit("@", 1)[-1]
    _, _, port_text = hostport.rpartition(":")
    port = int(port_text) if port_text.isdigit() else None
    port_map = _load_reality_port_map()
    port_params = port_map.get(port) if port is not None else None

    pbk = params.get("pbk") or (port_params or {}).get("pbk") or fallback_pbk
    fp = params.get("fp") or (port_params or {}).get("fp") or fallback_fp
    sid = params.get("sid") or (port_params or {}).get("sid") or fallback_sid
    return pbk, fp, sid


def _classify_line(line: str) -> str:
    """Return a category string: ws, grpc, trojan, reality, vmess, other."""
    lower = (line or "").strip().lower()
    if lower.startswith("vless://"):
        parsed = urlparse(line.split("#", 1)[0])
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        sec = (params.get("security") or "").lower()
        net = (params.get("type") or "").lower()
        if net == "ws" and sec == "tls":
            return "ws"
        if net == "grpc" and sec == "tls":
            return "grpc"
        if sec == "reality":
            return "reality"
        return "other"
    if lower.startswith("trojan://"):
        return "trojan"
    if lower.startswith("vmess://"):
        return "vmess"
    return "other"


def _decode_subscription_text(payload: bytes):
    """Decode raw subscription payload into text lines. Returns (text, was_base64)."""
    try:
        text = payload.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None, False

    if "vless://" not in text and "vmess://" not in text and "trojan://" not in text:
        try:
            padded = text + "=" * ((4 - len(text) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if "vless://" in decoded or "vmess://" in decoded or "trojan://" in decoded:
                return decoded, True
        except Exception:
            return None, False
    return text, False


def _inject_reality_params(line: str, pbk, fp, sid) -> str:
    """Ensure Reality VLESS line has pbk/fp/sid/flow."""
    if not line.startswith("vless://"):
        return line
    uri_part, sep, fragment = line.partition("#")
    parsed = urlparse(uri_part)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if (params.get("security") or "").lower() != "reality":
        return line
    if pbk and not params.get("pbk"):
        params["pbk"] = pbk
    if not params.get("fp"):
        params["fp"] = fp
    if not params.get("sid"):
        params["sid"] = sid
    if params.get("flow") != "xtls-rprx-vision":
        params["flow"] = "xtls-rprx-vision"
    rebuilt = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params,
                          urlencode(params, doseq=True), parsed.fragment))
    return rebuilt + (sep + fragment if sep else "")


def _inject_browser_fingerprint(line: str, browser_fp: str = "chrome") -> str:
    """Ensure browser fingerprint is explicitly set for supported protocols."""
    if not line:
        return line

    if line.startswith("vless://") or line.startswith("trojan://"):
        uri_part, sep, fragment = line.partition("#")
        parsed = urlparse(uri_part)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if params.get("fp") != browser_fp:
            params["fp"] = browser_fp
        rebuilt = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(params, doseq=True),
            parsed.fragment,
        ))
        return rebuilt + (sep + fragment if sep else "")

    if line.startswith("vmess://"):
        try:
            raw = line[len("vmess://"):]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            cfg = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
            cfg["fp"] = browser_fp
            encoded = base64.b64encode(
                json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("utf-8")
            return f"vmess://{encoded}"
        except Exception:
            return line

    return line


def _get_normalized_lines(payload: bytes, export_host=None):
    """Parse and normalize subscription payload into list of proxy lines."""
    text, _ = _decode_subscription_text(payload)
    if not text:
        return []

    fallback_pbk = _load_reality_public_key()
    fallback_fp = _load_reality_fingerprint()
    extracted_pbk, extracted_sid = _extract_reality_keys(text)
    default_pbk = extracted_pbk or fallback_pbk
    default_sid = extracted_sid or "f9"

    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith("vless://") or lower.startswith("trojan://") or lower.startswith("vmess://"):
            if line.startswith("vless://"):
                pbk, fp, sid = _resolve_reality_params(line, default_pbk, fallback_fp, default_sid)
                line = _inject_reality_params(line, pbk, fp, sid)
            line = _inject_browser_fingerprint(line, "chrome")
            line = _rewrite_uri_host(line, export_host)
            lines.append(line)
    return lines


def _operator_rank(operator: str):
    """Return a mapping from category → rank for given operator."""
    order = OPERATOR_PRIORITY.get((operator or "").lower().strip(), DEFAULT_PRIORITY)
    return {cat: idx for idx, cat in enumerate(order)}


def _sort_lines_by_operator(lines, operator=None):
    """Sort proxy lines by operator priority."""
    ranks = _operator_rank(operator)
    def key_fn(line):
        cat = _classify_line(line)
        return ranks.get(cat, len(ranks))
    return sorted(lines, key=key_fn)


def _parse_vless_line(line: str):
    """Parse a VLESS URI into a dict with fields needed for sing-box outbound."""
    uri_part, _, fragment = line.partition("#")
    parsed = urlparse(uri_part)
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # netloc: <uuid>@<host>:<port>
    userinfo = parsed.netloc
    uuid_part, _, hostport = userinfo.partition("@")
    host, _, port_s = hostport.rpartition(":")
    if not host:
        host = hostport
    port = int(port_s) if port_s.isdigit() else 443
    tag = unquote(fragment) if fragment else f"vless-{host}:{port}"
    tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]{4,}\)\s*$', '', tag).strip()
    return {
        "uuid": uuid_part,
        "server": host,
        "port": port,
        "tag": tag,
        "security": (params.get("security") or "none").lower(),
        "network": (params.get("type") or "tcp").lower(),
        "sni": params.get("sni") or params.get("serverName") or host,
        "flow": params.get("flow") or "",
        "pbk": params.get("pbk") or "",
        "sid": params.get("sid") or "",
        "fp": params.get("fp") or "",
        "alpn": params.get("alpn") or "",
        "serviceName": params.get("serviceName") or params.get("path") or "",
        "wsPath": params.get("path") or "/",
        "wsHost": params.get("host") or host,
    }


def _parse_trojan_line(line: str):
    """Parse a trojan:// URI."""
    uri_part, _, fragment = line.partition("#")
    parsed = urlparse(uri_part)
    password = parsed.username or ""
    host = parsed.hostname or ""
    port = parsed.port or 443
    params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    tag = unquote(fragment) if fragment else f"trojan-{host}:{port}"
    tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]{4,}\)\s*$', '', tag).strip()
    return {
        "password": password,
        "server": host,
        "port": port,
        "tag": tag,
        "sni": params.get("sni") or host,
        "fp": params.get("fp") or "",
        "alpn": params.get("alpn") or "",
        "network": (params.get("type") or "tcp").lower(),
    }


def _build_singbox_outbound(line: str):
    """Convert a proxy line into a sing-box outbound dict."""
    lower = line.lower()

    if lower.startswith("vless://"):
        p = _parse_vless_line(line)
        out = {
            "type": "vless",
            "tag": p["tag"],
            "server": p["server"],
            "server_port": p["port"],
            "uuid": p["uuid"],
        }

        if p["security"] == "tls":
            tls = {"enabled": True, "server_name": p["sni"], "insecure": False}
            if p["alpn"]:
                tls["alpn"] = p["alpn"].split(",")
            tls["utls"] = {"enabled": True, "fingerprint": p["fp"] or "chrome"}
            out["tls"] = tls

            if p["network"] == "ws":
                out["transport"] = {
                    "type": "ws",
                    "path": p["wsPath"],
                    "headers": {"Host": p["wsHost"]},
                }
            elif p["network"] == "grpc":
                out["transport"] = {
                    "type": "grpc",
                    "service_name": p["serviceName"],
                }
            elif p["network"] == "xhttp":
                xhttp_t: dict = {"type": "xhttp", "path": p["wsPath"]}
                # host header: prefer explicit host param, else use SNI domain
                h = p["wsHost"] if p["wsHost"] and p["wsHost"] != p["server"] else p["sni"]
                if h:
                    xhttp_t["host"] = h
                out["transport"] = xhttp_t

        elif p["security"] == "reality":
            tls = {
                "enabled": True,
                "server_name": p["sni"],
                "insecure": False,
                "reality": {
                    "enabled": True,
                    "public_key": p["pbk"],
                    "short_id": p["sid"],
                },
                "utls": {"enabled": True, "fingerprint": p["fp"] or "chrome"},
            }
            out["tls"] = tls
            if p["flow"]:
                out["flow"] = p["flow"]

        return out

    if lower.startswith("trojan://"):
        p = _parse_trojan_line(line)
        out = {
            "type": "trojan",
            "tag": p["tag"],
            "server": p["server"],
            "server_port": p["port"],
            "password": p["password"],
            "tls": {
                "enabled": True,
                "server_name": p["sni"],
                "insecure": False,
                "utls": {"enabled": True, "fingerprint": p["fp"] or "chrome"},
            },
        }
        if p["alpn"]:
            out["tls"]["alpn"] = p["alpn"].split(",")
        if p["network"] == "grpc":
            out["transport"] = {"type": "grpc", "service_name": "grpc"}
        return out

    # VMess — basic support
    if lower.startswith("vmess://"):
        try:
            raw = line[len("vmess://"):]
            padded = raw + "=" * ((4 - len(raw) % 4) % 4)
            cfg = json.loads(base64.b64decode(padded).decode("utf-8", errors="ignore"))
        except Exception:
            return None
        tag = cfg.get("ps") or f"vmess-{cfg.get('add')}:{cfg.get('port')}"
        tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]{4,}\)\s*$', '', tag).strip()
        out = {
            "type": "vmess",
            "tag": tag,
            "server": cfg.get("add", ""),
            "server_port": int(cfg.get("port", 443)),
            "uuid": cfg.get("id", ""),
            "alter_id": int(cfg.get("aid", 0)),
            "security": cfg.get("scy", "auto"),
        }
        net = (cfg.get("net") or "tcp").lower()
        tls_v = (cfg.get("tls") or "").lower()
        if tls_v == "tls":
            out["tls"] = {
                "enabled": True,
                "server_name": cfg.get("sni") or cfg.get("add", ""),
                "insecure": False,
                "utls": {"enabled": True, "fingerprint": cfg.get("fp") or "chrome"},
            }
        if net == "ws":
            out["transport"] = {
                "type": "ws",
                "path": cfg.get("path", "/"),
                "headers": {"Host": cfg.get("host") or cfg.get("add", "")},
            }
        elif net == "grpc":
            out["transport"] = {
                "type": "grpc",
                "service_name": cfg.get("path") or "grpc",
            }
        return out

    return None



def _collect_server_ips(outbounds):
    import re as _re
    ips = []
    seen = set()
    for ob in outbounds:
        srv = ob.get('server', '')
        if _re.match(r'^\d+\.\d+\.\d+\.\d+$', srv) and srv not in seen:
            ips.append(srv + '/32')
            seen.add(srv)
    return ips


_SINGBOX_DIRECT_DOMAINS_DEFAULT = (
    # --- Connectivity checks (universal) ---
    "captive.apple.com",
    "www.apple.com",
    "connectivitycheck.android.com",
    "connectivitycheck.gstatic.com",
    "clients3.google.com",
    "www.gstatic.com",
    "msftconnecttest.com",
    "www.msftconnecttest.com",
    "msftncsi.com",
    "www.msftncsi.com",
    "detectportal.firefox.com",
    "nmcheck.gnome.org",
    "networkcheck.kde.org",
    # --- NTP ---
    "time.apple.com",
    "time.windows.com",
    "time.google.com",
    "pool.ntp.org",
    "ntp.ru",
    "pool.ntp.ru",
    # --- MTS captive portal & auth ---
    "captive.mts.ru",
    "login.mts.ru",
    "auth.mts.ru",
    "internet.mts.ru",
    "lk.mts.ru",
    "start.mts.ru",
    # --- Beeline captive portal & auth ---
    "captive.beeline.ru",
    "hotspot.beeline.ru",
    "wifi.beeline.ru",
    "auth.beeline.ru",
    "login.beeline.ru",
    "my.beeline.ru",
    # --- Tele2 captive portal & auth ---
    "captive.tele2.ru",
    "auth.tele2.ru",
    "internet.tele2.ru",
    "my.tele2.ru",
    "lk.tele2.ru",
    # --- Yota captive portal & auth ---
    "captive.yota.ru",
    "hotspot.yota.ru",
    "yota.ru",
    "my.yota.ru",
    # --- Megafon captive portal & auth ---
    "captive.megafon.ru",
    "internet.megafon.ru",
    "my.megafon.ru",
    "login.megafon.ru.com",
    "time.google.com",
    "pool.ntp.org",
    "ntp.ru",
    "pool.ntp.ru",
    # --- MTS captive portal & auth ---
    "captive.mts.ru",
    "login.mts.ru",
    "auth.mts.ru",
    "internet.mts.ru",
    "lk.mts.ru",
    "start.mts.ru",
    # --- Beeline captive portal & auth ---
    "captive.beeline.ru",
    "hotspot.beeline.ru",
    "wifi.beeline.ru",
    "auth.beeline.ru",
    "login.beeline.ru",
    "my.beeline.ru",
    # --- Tele2 captive portal & auth ---
    "captive.tele2.ru",
    "auth.tele2.ru",
    "internet.tele2.ru",
    "my.tele2.ru",
    "lk.tele2.ru",
    # --- Yota captive portal & auth ---
    "captive.yota.ru",
    "hotspot.yota.ru",
    "yota.ru",
    "my.yota.ru",
    # --- Megafon captive portal & auth ---
    "captive.megafon.ru",
    "internet.megafon.ru",
    "my.megafon.ru",
    "login.megafon.ru",
)

_SINGBOX_DIRECT_SUFFIXES_DEFAULT = (
    "local",
    "lan",
    "localdomain",
    "home.arpa",
)


def _env_csv(name: str, defaults) -> list:
    raw = os.getenv(name, "")
    items = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if items:
        return items
    return list(defaults)


def _build_singbox_config(proxy_lines, operator=None):
    """Build a full sing-box JSON configuration from proxy lines."""
    ordered = _sort_lines_by_operator(proxy_lines, operator)

    outbounds = []
    proxy_tags = []
    for line in ordered:
        ob = _build_singbox_outbound(line)
        if ob:
            outbounds.append(ob)
            proxy_tags.append(ob["tag"])

    if not proxy_tags:
        return None

    direct_domains = _env_csv("SMARTKAMA_SINGBOX_DIRECT_DOMAINS", _SINGBOX_DIRECT_DOMAINS_DEFAULT)
    direct_suffixes = _env_csv("SMARTKAMA_SINGBOX_DIRECT_SUFFIXES", _SINGBOX_DIRECT_SUFFIXES_DEFAULT)

    # url_test group — automatic best-latency selection
    url_test = {
        "type": "urltest",
        "tag": "auto",
        "outbounds": list(proxy_tags),
        "url": "https://www.gstatic.com/generate_204",
        "interval": "1m",
        "tolerance": 50,
        }

    # selector — manual pick through UI
    selector = {
        "type": "selector",
        "tag": "proxy",
        "outbounds": ["auto", "direct"] + list(proxy_tags),
        "default": "auto",
    }

    # Fixed utility outbounds
    direct = {"type": "direct", "tag": "direct"}
    block = {"type": "block", "tag": "block"}
    dns_out = {"type": "dns", "tag": "dns-out"}

    all_outbounds = [selector, url_test] + outbounds + [direct, block, dns_out]

    config = {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {
                    "tag": "dns-proxy",
                    "address": "https://1.1.1.1/dns-query",
                    "address_resolver": "dns-direct",
                    "detour": "proxy",
                },
                {
                    "tag": "dns-direct",
                    "address": "https://77.88.8.8/dns-query",
                    "detour": "direct",
                },
                {
                    "tag": "dns-block",
                    "address": "rcode://success",
                },
            ],
            "rules": [
                {"outbound": ["any"], "server": "dns-direct"},
                {"clash_mode": "Direct", "server": "dns-direct"},
                {"clash_mode": "Global", "server": "dns-proxy"},
                {"domain": direct_domains, "server": "dns-direct"},
                {"domain_suffix": direct_suffixes, "server": "dns-direct"},
                {
                    "rule_set": "geosite-category-ads-all",
                    "server": "dns-block",
                    "disable_cache": True,
                },
            ],
            "final": "dns-proxy",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "inet4_address": "172.19.0.1/30",
                "inet6_address": "fdfe:dcba:9876::1/126",
                "auto_route": True,
                "strict_route": True,
                "sniff": True,
            },
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": 2080,
                "sniff": True,
            },
            {
                "type": "http",
                "tag": "http-in",
                "listen": "127.0.0.1",
                "listen_port": 2090,
                "sniff": True,
            },
        ],
        "outbounds": all_outbounds,
        "route": {
            "auto_detect_interface": True,
            "final": "proxy",
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
                {"clash_mode": "Direct", "outbound": "direct"},
                {"clash_mode": "Global", "outbound": "proxy"},
                {
                    "rule_set": "geosite-category-ads-all",
                    "outbound": "block",
                },
                {
                    "domain": direct_domains,
                    "outbound": "direct",
                },
                {
                    "domain_suffix": direct_suffixes,
                    "outbound": "direct",
                },
                {
                    "ip_cidr": _collect_server_ips(outbounds) + ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
                    "outbound": "direct",
                },
            ],
            "rule_set": [
                {
                    "tag": "geosite-category-ads-all",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ads-all.srs",
                    "download_detour": "direct",
                },
            ],
        },

    }
    return config


def _encode_profile_title(title: str) -> str:
    """Encode profile-title for HTTP header (base64 for non-ASCII, plain for ASCII)."""
    try:
        title.encode('latin-1')
        return title
    except UnicodeEncodeError:
        return "base64:" + base64.b64encode(title.encode('utf-8')).decode('ascii')


def _normalize_subscription_payload(target_url, payload, operator=None, export_host=None):
    if not payload:
        return payload

    try:
        text = payload.decode("utf-8", errors="ignore").strip()
    except Exception:
        return payload

    was_base64 = False
    working_text = text
    if "vless://" not in working_text and "vmess://" not in working_text and "trojan://" not in working_text:
        try:
            padded = working_text + "=" * ((4 - len(working_text) % 4) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
            if "vless://" in decoded or "vmess://" in decoded or "trojan://" in decoded:
                working_text = decoded
                was_base64 = True
        except Exception:
            return payload

    fallback_pbk = _load_reality_public_key()
    fallback_fp = _load_reality_fingerprint()
    extracted_pbk, extracted_sid = _extract_reality_keys(working_text)
    default_pbk = extracted_pbk or fallback_pbk
    sid_default = extracted_sid or "f9"

    ranks = _operator_rank(operator)

    def _line_rank(subscription_line: str) -> int:
        cat = _classify_line(subscription_line)
        return ranks.get(cat, len(ranks))

    out_lines = []
    protocol_bucket = []
    changed = False
    for raw_line in working_text.splitlines():
        line = (raw_line or "").strip()
        if not line:
            continue

        if line.startswith("trojan://") or line.startswith("vmess://"):
            normalized_line = _inject_browser_fingerprint(line, "chrome")
            if normalized_line != line:
                changed = True
            protocol_bucket.append(normalized_line)
            continue

        if not line.startswith("vless://"):
            out_lines.append(line)
            continue

        uri_part, sep, fragment = line.partition("#")
        parsed = urlparse(uri_part)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if (params.get("security") or "").lower() == "reality":
            pbk, fp, sid = _resolve_reality_params(line, default_pbk, fallback_fp, sid_default)
            if pbk and not params.get("pbk"):
                params["pbk"] = pbk
                changed = True
            if not params.get("fp"):
                params["fp"] = fp
                changed = True
            if not params.get("sid"):
                params["sid"] = sid
                changed = True
            if params.get("flow") != "xtls-rprx-vision":
                params["flow"] = "xtls-rprx-vision"
                changed = True

        rebuilt = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(params, doseq=True),
            parsed.fragment,
        ))
        vless_line = rebuilt + (sep + fragment if sep else "")
        vless_line = _inject_browser_fingerprint(vless_line, "chrome")
        protocol_bucket.append(_rewrite_uri_host(vless_line, export_host))

    if protocol_bucket:
        sorted_protocols = sorted(protocol_bucket, key=_line_rank)
        if sorted_protocols != protocol_bucket:
            changed = True
        out_lines.extend(sorted_protocols)

    # Operator override always forces reorder even if no Reality params changed.
    if operator and not changed and protocol_bucket:
        changed = True

    if not changed:
        return payload

    rebuilt = "\n".join(out_lines) + "\n"
    if was_base64:
        return base64.b64encode(rebuilt.encode("utf-8"))
    return rebuilt.encode("utf-8")


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS short_links (
            token TEXT PRIMARY KEY,
            target_url TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS short_link_aliases (
            token TEXT PRIMARY KEY,
            canonical_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(canonical_token) REFERENCES short_links(token) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS short_links_meta (
            token TEXT PRIMARY KEY,
            remaining_days INTEGER,
            remaining_hours INTEGER,
            remaining_minutes INTEGER,
            usage_current REAL,
            usage_limit REAL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(token) REFERENCES short_links(token) ON DELETE CASCADE
        )
        """
    )
    conn.commit()


def _resolve_canonical_token(conn, token):
    row = conn.execute("SELECT token FROM short_links WHERE token=?", (token,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT canonical_token FROM short_link_aliases WHERE token=?", (token,)).fetchone()
    return row[0] if row else None


def find_target(token):
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute("SELECT target_url FROM short_links WHERE token=?", (token,)).fetchone()
        if row:
            return row[0]
        row = conn.execute(
            """
            SELECT sl.target_url
            FROM short_link_aliases sla
            JOIN short_links sl ON sl.token = sla.canonical_token
            WHERE sla.token=?
            """,
            (token,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def update_target(token, target_url):
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        canonical_token = _resolve_canonical_token(conn, token) or token
        conn.execute("UPDATE short_links SET target_url=? WHERE token=?", (target_url, canonical_token))
        conn.commit()
    finally:
        conn.close()


def _load_client_proxy_path():
    env_path = (os.getenv("HIDDIFY_CLIENT_PROXY_PATH") or "").strip("/")
    if env_path:
        return env_path

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute("SELECT value FROM str_config WHERE key='hiddify_client_proxy_path' LIMIT 1").fetchone()
        if row and row[0]:
            return str(row[0]).strip("/")
    except Exception:
        return None
    finally:
        conn.close()
    return None


def _rewrite_subscription_path(url, client_path):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return url

    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 3:
        return url
    if parts[0] == client_path:
        return url

    import re
    if not re.fullmatch(UUID_RE, parts[1]):
        return url

    subpath = "/".join(parts[2:])
    sub_target = (
        subpath == "all.txt"
        or subpath == "sub"
        or subpath.startswith("sub/")
        or subpath.startswith("clash/")
        or subpath.startswith("full-singbox")
        or subpath.startswith("singbox")
    )
    if not sub_target:
        return url

    new_path = "/" + "/".join([client_path, parts[1]] + parts[2:])
    return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))


def _normalize_target_url(target_url):
    client_path = _load_client_proxy_path()
    if not client_path:
        return target_url

    parsed = urlparse(target_url)
    if parsed.scheme in ("http", "https"):
        return _rewrite_subscription_path(target_url, client_path)

    # Normalize deep-link wrappers like hiddify://install-config?url=<http...>
    if parsed.scheme in ("hiddify", "v2raytun", "mtpromo", "clash", "clashmeta"):
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        raw_inner = q.get("url")
        if not raw_inner:
            return target_url
        fixed_inner = _rewrite_subscription_path(raw_inner, client_path)
        if fixed_inner == raw_inner:
            return target_url
        q["url"] = fixed_inner
        new_query = urlencode(q, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

    return target_url


def find_meta(token):
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        canonical_token = _resolve_canonical_token(conn, token) or token
        row = conn.execute(
            """
            SELECT remaining_days, remaining_hours, remaining_minutes, usage_current, usage_limit
            FROM short_links_meta WHERE token=?
            """,
            (canonical_token,),
        ).fetchone()
        if not row:
            return None
        return {
            "rd": row[0],
            "rh": row[1],
            "rm": row[2],
            "uc": row[3],
            "ul": row[4],
        }
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    def send_header(self, keyword, value):
        """Override: auto-encode non-ASCII header values so send_header never raises
        UnicodeEncodeError (Python's BaseHTTPServer encodes headers as latin-1 strict).
        """
        if isinstance(value, str):
            try:
                value.encode('latin-1')
            except UnicodeEncodeError:
                value = "base64:" + base64.b64encode(value.encode('utf-8')).decode('ascii')
        super().send_header(keyword, value)

    def _handle_redirect(self, send_body=True):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        export_host = _load_export_host(self.headers.get("Host"))
        raw_operator = (query.get("op") or [""])[0].lower().strip() or None
        client_hint, has_explicit_client_hint = _resolve_client_hint(query, self.headers)
        operator = _resolve_operator_hint(raw_operator, client_hint)

        # Track device for /sub/ requests (UUID resolved inside)
        if path.startswith("/sub/"):
            _track_device_for_sub(self.path, self.headers, self.client_address)

        if path.startswith("/sub/"):
            # ── Device limit enforcement ──
            sub_segment = path.split("/sub/", 1)[1].strip("/").split("?")[0].split("/")[0]
            resolved_uuid = _resolve_sub_uuid(sub_segment) if sub_segment else ""
            if resolved_uuid and _is_device_over_limit(resolved_uuid):
                ua_lower = (self.headers.get("User-Agent") or "").lower()
                fmt_param = (query.get("format") or [""])[0].lower().strip()
                _sb_only = ("sing-box", "singbox", "nekobox")
                wants_sb = (fmt_param in ("singbox", "sing-box", "json") or
                            any(h in ua_lower for h in _sb_only))
                _dev_title = _encode_profile_title("SmartKamaVPN - лимит устройств")
                if wants_sb:
                    body = json.dumps(_DEVICE_BLOCKED_SINGBOX, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("profile-title", _dev_title)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if send_body:
                        self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("profile-title", _dev_title)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if send_body:
                        self.wfile.write(_DEVICE_BLOCKED_PAYLOAD)
                return

            target = f"http://127.0.0.1:8000{path}"
            if parsed.query:
                target = f"{target}?{parsed.query}"
            payload, content_type, upstream_headers = proxy_subscription_source(target)
            # Fallback: resolve short bot sub_id → real Marzban token
            if payload is None:
                sub_segment = path.split("/sub/", 1)[1].strip("/")
                if sub_segment and len(sub_segment) <= 32:
                    resolved = _resolve_marzban_sub_path(sub_segment)
                    if resolved:
                        fallback_target = f"http://127.0.0.1:8000{resolved}"
                        if parsed.query:
                            fallback_target = f"{fallback_target}?{parsed.query}"
                        payload, content_type, upstream_headers = proxy_subscription_source(fallback_target)
            if payload is not None:
                # Auto-detect sing-box native clients and serve JSON format.
                # NOTE: Hiddify/Happ works better with base64 proxy links (vless://, trojan://)
                # — serving full sing-box JSON to Happ causes it to show a single "custom" entry.
                ua_lower = (self.headers.get("User-Agent") or "").lower()
                fmt_param = (query.get("format") or [""])[0].lower().strip()
                _sb_clients = ("sing-box", "singbox", "nekobox")
                wants_singbox = (fmt_param in ("singbox", "sing-box", "json") or
                                 any(h in ua_lower for h in _sb_clients))
                if wants_singbox:
                    try:
                        lines = _get_normalized_lines(payload, export_host=export_host)
                        config = _build_singbox_config(lines, operator=operator)
                        if config:
                            body = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.send_header("profile-title", "SmartKamaVPN")
                            self.send_header("content-disposition", 'attachment; filename="SmartKamaVPN.json"')
                            for hk, hv in (upstream_headers or {}).items():
                                if hk.lower() != "profile-title":
                                    self.send_header(hk, hv)
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            if send_body:
                                self.wfile.write(body)
                            return
                    except Exception:
                        pass  # fallback to base64 below
                payload = _normalize_subscription_payload(target, payload, operator=operator, export_host=export_host)
                self.send_response(200)
                self.send_header("Content-Type", content_type or "text/plain; charset=utf-8")
                if "profile-title" not in (upstream_headers or {}):
                    self.send_header("profile-title", "SmartKamaVPN")
                for hk, hv in (upstream_headers or {}).items():
                    self.send_header(hk, hv)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if send_body:
                    self.wfile.write(payload)
                return

        # ── Subscription info page: /p/<sub_id> ──
        if path.startswith("/p/"):
            sub_id = path.split("/p/", 1)[1].strip("/")
            if sub_id and len(sub_id) <= 64:
                sub_url = f"http://127.0.0.1:8000/sub/{sub_id}"
                payload, _, upstream_headers = proxy_subscription_source(sub_url)
                if payload is None and len(sub_id) <= 32:
                    resolved = _resolve_marzban_sub_path(sub_id)
                    if resolved:
                        sub_url = f"http://127.0.0.1:8000{resolved}"
                        payload, _, upstream_headers = proxy_subscription_source(sub_url)

                if payload is not None:
                    # Parse subscription-userinfo header for meta
                    info_str = (upstream_headers or {}).get("subscription-userinfo", "")
                    meta = {}
                    if info_str:
                        info_parts = {}
                        for part in info_str.split(";"):
                            part = part.strip()
                            if "=" in part:
                                k, v = part.split("=", 1)
                                info_parts[k.strip()] = v.strip()
                        # subscription-userinfo: upload=X; download=Y; total=Z; expire=T
                        try:
                            up = int(info_parts.get("upload", 0))
                            down = int(info_parts.get("download", 0))
                            total = int(info_parts.get("total", 0))
                            expire = int(info_parts.get("expire", 0))
                            used_gb = (up + down) / (1024 ** 3)
                            total_gb = total / (1024 ** 3)
                            remaining_gb = max(0, total_gb - used_gb)
                            meta["uc"] = f"{used_gb:.2f}"
                            meta["ul"] = f"{total_gb:.0f}"
                            if expire > 0:
                                import time
                                remaining_sec = max(0, expire - time.time())
                                rd = int(remaining_sec // 86400)
                                rh = int((remaining_sec % 86400) // 3600)
                                rm = int((remaining_sec % 3600) // 60)
                                meta["rd"] = str(rd)
                                meta["rh"] = str(rh)
                                meta["rm"] = str(rm)
                            else:
                                meta["rd"] = "∞"
                                meta["rh"] = "0"
                                meta["rm"] = "0"
                        except (ValueError, TypeError):
                            pass

                    # Build external subscription URL for the page
                    ext_host = _build_external_host(self.headers)
                    ext_sub_url = f"https://{ext_host}/sub/{sub_id}"

                    devices = _get_devices_for_sub(sub_id)

                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    if send_body:
                        self.wfile.write(build_subscription_page(sub_id, ext_sub_url, meta, devices))
                    return

        if path.startswith("/s/"):
            token = path.split("/s/", 1)[1].strip("/")
            if token:
                target = find_target(token)
                if target:
                    # Track device with resolved target URL (contains real /sub/ path)
                    _track_device_for_sub(self.path, self.headers, self.client_address, resolved_target_url=target)
                    normalized_target = _normalize_target_url(target)
                    if normalized_target != target:
                        try:
                            update_target(token, normalized_target)
                        except Exception:
                            pass
                        target = normalized_target

                    # ── Device limit enforcement for /s/ ──
                    if is_subscription_target(target):
                        _s_sub_seg = ""
                        if "/sub/" in target:
                            _s_sub_seg = target.split("/sub/", 1)[1].strip("/").split("?")[0].split("/")[0]
                        _s_uuid = _resolve_sub_uuid(_s_sub_seg) if _s_sub_seg else ""
                        if _s_uuid and _is_device_over_limit(_s_uuid):
                            ua_lower = (self.headers.get("User-Agent") or "").lower()
                            _sb_only = ("sing-box", "singbox", "nekobox")
                            wants_sb = any(h in ua_lower for h in _sb_only)
                            _dev_title = _encode_profile_title("SmartKamaVPN - лимит устройств")
                            if wants_sb:
                                body = json.dumps(_DEVICE_BLOCKED_SINGBOX, ensure_ascii=False).encode("utf-8")
                                self.send_response(200)
                                self.send_header("Content-Type", "application/json; charset=utf-8")
                                self.send_header("profile-title", _dev_title)
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                if send_body:
                                    self.wfile.write(body)
                            else:
                                self.send_response(200)
                                self.send_header("Content-Type", "text/plain; charset=utf-8")
                                self.send_header("profile-title", _dev_title)
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                if send_body:
                                    self.wfile.write(_DEVICE_BLOCKED_PAYLOAD)
                            return

                    force_app_redirect = (query.get("app") or [""])[0] == "1" or has_explicit_client_hint
                    force_web_page = (query.get("web") or [""])[0] == "1"
                    fmt = (query.get("format") or [""])[0].lower().strip()
                    if is_subscription_target(target):
                        # sing-box JSON config endpoint
                        if fmt == "singbox":
                            payload, _, _ = proxy_subscription_source(target)
                            if payload is not None:
                                lines = _get_normalized_lines(payload, export_host=export_host)
                                config = _build_singbox_config(lines, operator=operator)
                                if config:
                                    body = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
                                    self.send_response(200)
                                    self.send_header("Content-Type", "application/json; charset=utf-8")
                                    self.send_header("profile-title", "SmartKamaVPN")
                                    self.send_header("content-disposition", 'attachment; filename="SmartKamaVPN.json"')
                                    self.send_header("Cache-Control", "no-store")
                                    self.end_headers()
                                    if send_body:
                                        self.wfile.write(body)
                                    return

                        if (query.get("raw") or [""])[0] == "1":
                            payload, content_type, upstream_headers = proxy_subscription_source(target)
                            if payload is not None:
                                payload = _normalize_subscription_payload(target, payload, operator=operator, export_host=export_host)
                                self.send_response(200)
                                self.send_header("Content-Type", content_type or "text/plain; charset=utf-8")
                                if "profile-title" not in (upstream_headers or {}):
                                    self.send_header("profile-title", "SmartKamaVPN")
                                for hk, hv in (upstream_headers or {}).items():
                                    self.send_header(hk, hv)
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                if send_body:
                                    self.wfile.write(payload)
                                return

                        if force_app_redirect:
                            payload, content_type, upstream_headers = proxy_subscription_source(target)
                            if payload is not None:
                                payload = _normalize_subscription_payload(target, payload, operator=operator, export_host=export_host)
                                self.send_response(200)
                                self.send_header("Content-Type", content_type or "text/plain; charset=utf-8")
                                if "profile-title" not in (upstream_headers or {}):
                                    self.send_header("profile-title", "SmartKamaVPN")
                                for hk, hv in (upstream_headers or {}).items():
                                    self.send_header(hk, hv)
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                if send_body:
                                    self.wfile.write(payload)
                                return

                        # App clients get normalized content directly (pbk/fp/sid/order) for better compatibility.
                        if is_app_client(self.headers):
                            payload, content_type, upstream_headers = proxy_subscription_source(target)
                            if payload is not None:
                                payload = _normalize_subscription_payload(target, payload, operator=operator, export_host=export_host)
                                self.send_response(200)
                                self.send_header("Content-Type", content_type or "text/plain; charset=utf-8")
                                if "profile-title" not in (upstream_headers or {}):
                                    self.send_header("profile-title", "SmartKamaVPN")
                                for hk, hv in (upstream_headers or {}).items():
                                    self.send_header(hk, hv)
                                self.send_header("Cache-Control", "no-store")
                                self.end_headers()
                                if send_body:
                                    self.wfile.write(payload)
                                return

                        if not force_web_page:
                            self.send_response(302)
                            self.send_header("Location", target)
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            return

                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        if send_body:
                            meta = find_meta(token) or {
                                "rd": (query.get("rd") or ["-"])[0],
                                "rh": (query.get("rh") or ["-"])[0],
                                "rm": (query.get("rm") or ["-"])[0],
                                "uc": (query.get("uc") or ["-"])[0],
                                "ul": (query.get("ul") or ["-"])[0],
                            }
                            self.wfile.write(build_install_page(target, token, meta))
                    else:
                        self.send_response(302)
                        self.send_header("Location", target)
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                    return

        # ── Fallback: try resolving root-level path as a short link token ──
        # Supports name-based links like /ИмяПодписки or /myname
        root_token = path.strip("/")
        if root_token and "/" not in root_token:
            from urllib.parse import unquote as _unquote
            decoded_token = _unquote(root_token)
            target = find_target(decoded_token) or find_target(root_token)
            if target:
                _track_device_for_sub(self.path, self.headers, self.client_address, resolved_target_url=target)
                normalized_target = _normalize_target_url(target)
                if normalized_target != target:
                    try:
                        update_target(decoded_token, normalized_target)
                    except Exception:
                        pass
                    target = normalized_target

                if is_subscription_target(target):
                    _n_sub_seg = ""
                    if "/sub/" in target:
                        _n_sub_seg = target.split("/sub/", 1)[1].strip("/").split("?")[0].split("/")[0]
                    _n_uuid = _resolve_sub_uuid(_n_sub_seg) if _n_sub_seg else ""
                    if _n_uuid and _is_device_over_limit(_n_uuid):
                        ua_lower = (self.headers.get("User-Agent") or "").lower()
                        fmt_param = (query.get("format") or [""])[0].lower().strip()
                        _sb_only = ("sing-box", "singbox", "nekobox")
                        wants_sb = (fmt_param in ("singbox", "sing-box", "json") or
                                    any(h in ua_lower for h in _sb_only))
                        if wants_sb:
                            body = json.dumps(_DEVICE_BLOCKED_SINGBOX, ensure_ascii=False).encode("utf-8")
                            self.send_response(200)
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.send_header("Cache-Control", "no-store")
                            self.end_headers()
                            if send_body:
                                self.wfile.write(body)
                            return
                        self.send_response(200)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        if send_body:
                            self.wfile.write(_DEVICE_BLOCKED_PAYLOAD)
                        return

                force_app_redirect = (query.get("app") or [""])[0] == "1" or has_explicit_client_hint
                force_web_page = (query.get("web") or [""])[0] == "1"
                if force_web_page or (not force_app_redirect and not is_subscription_target(target)):
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                elif is_subscription_target(target):
                    self.send_response(302)
                    app_target = target + ("&" if "?" in target else "?") + "app=1"
                    self.send_header("Location", app_target)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                else:
                    self.send_response(302)
                    self.send_header("Location", target)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                return

        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if send_body:
            self.wfile.write(b"Not found")

    def do_GET(self):
        try:
            self._handle_redirect(send_body=True)
        except Exception as e:
            logging.error("Unhandled error in do_GET: %s", e, exc_info=True)
            try:
                self.send_error(500)
            except Exception:
                pass

    def do_HEAD(self):
        try:
            self._handle_redirect(send_body=False)
        except Exception as e:
            logging.error("Unhandled error in do_HEAD: %s", e, exc_info=True)
            try:
                self.send_error(500)
            except Exception:
                pass

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), Handler)
    server.serve_forever()
