#!/usr/bin/env python3
import base64
import html
import json
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
    tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]*\)\s*$', '', tag).strip()
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
    tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]*\)\s*$', '', tag).strip()
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
        tag = re.sub(r'\s+\([a-zA-Z0-9][a-zA-Z0-9._-]*\)\s*$', '', tag).strip()
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
                {
                    "rule_set": "geosite-category-ads-all",
                    "server": "dns-block",
                    "disable_cache": True,
                },
            ],
            "final": "dns-proxy",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [],
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
                    "rule_set": ["geoip-ru", "geosite-category-ru"],
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
                {
                    "tag": "geoip-ru",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs",
                    "download_detour": "direct",
                },
                {
                    "tag": "geosite-category-ru",
                    "type": "remote",
                    "format": "binary",
                    "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs",
                    "download_detour": "direct",
                },
            ],
        },

    }
    return config


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


def find_target(token):
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        row = conn.execute("SELECT target_url FROM short_links WHERE token=?", (token,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def update_target(token, target_url):
    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_table(conn)
        conn.execute("UPDATE short_links SET target_url=? WHERE token=?", (target_url, token))
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
        row = conn.execute(
            """
            SELECT remaining_days, remaining_hours, remaining_minutes, usage_current, usage_limit
            FROM short_links_meta WHERE token=?
            """,
            (token,),
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
    def _handle_redirect(self, send_body=True):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        export_host = _load_export_host(self.headers.get("Host"))
        raw_operator = (query.get("op") or [""])[0].lower().strip() or None
        client_hint, has_explicit_client_hint = _resolve_client_hint(query, self.headers)
        operator = _resolve_operator_hint(raw_operator, client_hint)

        if path.startswith("/sub/"):
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
                # Auto-detect sing-box capable clients (happ, hiddify, sing-box, nekobox...)
                # and serve sing-box JSON format instead of base64 for better UI display
                ua_lower = (self.headers.get("User-Agent") or "").lower()
                fmt_param = (query.get("format") or [""])[0].lower().strip()
                _sb_clients = ("happ", "sing-box", "singbox", "hiddify", "nekobox")
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
                for hk, hv in (upstream_headers or {}).items():
                    self.send_header(hk, hv)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if send_body:
                    self.wfile.write(payload)
                return

        if path.startswith("/s/"):
            token = path.split("/s/", 1)[1].strip("/")
            if token:
                target = find_target(token)
                if target:
                    normalized_target = _normalize_target_url(target)
                    if normalized_target != target:
                        try:
                            update_target(token, normalized_target)
                        except Exception:
                            pass
                        target = normalized_target

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
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        if send_body:
            self.wfile.write(b"Not found")

    def do_GET(self):
        self._handle_redirect(send_body=True)

    def do_HEAD(self):
        self._handle_redirect(send_body=False)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = HTTPServer((HOST, PORT), Handler)
    server.serve_forever()
