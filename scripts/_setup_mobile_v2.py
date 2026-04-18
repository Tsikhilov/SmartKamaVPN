#!/usr/bin/env python3
"""scripts/_setup_mobile_v2.py

ПОЛНАЯ ЗАМЕНА _add_mobile_inbounds.py (v1 был сломан — не обновлял Marzban hosts table).

Что делает этот скрипт
────────────────────────
1. УДАЛЯЕТ старые нерабочие inbound-ы (mob-ws-1, mob-ws-2, mob-reality-ms, mob-reality-ap)
   из Xray config и Marzban hosts/inbounds таблиц.
2. УДАЛЯЕТ старые nginx WS location blocks (/api/m1-... /api/m2-...) из nginx conf.
3. ДОБАВЛЯЕТ 6 новых mobile inbound-ов в Xray config:
     nl-mob-ws-1        VLESS+WS, nginx:443 → 127.0.0.1:48011  (Нидерланды)
     nl-mob-ws-2        VLESS+WS, nginx:443 → 127.0.0.1:48012  (Нидерланды резерв)
     nl-mob-reality-ms  VLESS+Reality, port 2083, SNI=www.microsoft.com  (Нидерланды)
     nl-mob-reality-ap  VLESS+Reality, port 2087, SNI=www.apple.com      (Нидерланды)
     ru-mob-reality-ms  VLESS+Reality, port 2084, SNI=www.microsoft.com  (Москва)
     ru-mob-reality-ap  VLESS+Reality, port 2088, SNI=www.apple.com      (Москва)
4. ДОБАВЛЯЕТ nginx WS location blocks для nl-mob-ws-1/2.
5. ДОБАВЛЯЕТ правильные Marzban hosts entries (адрес, remarked, TLS) через PUT /api/hosts.
6. ОБНОВЛЯЕТ всех активных пользователей Marzban (заменяет старые теги на новые).
7. ОБНОВЛЯЕТ SmartKamaVPN bot DB (marzban_inbound_tags).
8. ОТКРЫВАЕТ порты 2083, 2084, 2087, 2088 на RU сервере через SSH (ufw).

Почему v1 не работал:
  PUT /api/core/config → Xray запускает inbound-ы, НО таблица hosts в Marzban пуста
  → Marzban использует внутренний порт (48001) и имя "🚀 Marz [VLESS - ws]" (fallback).

Почему v2 работает:
  PUT /api/core/config + PUT /api/hosts → Marzban генерирует ссылки с правильным
  внешним адресом (sub.smartkama.ru:443 для WS, 72.56.100.45:2083 для NL Reality,
  72.56.252.250:2084 для RU Reality) и правильным именем на русском языке.

Запуск (на NL сервере):
  cd /opt/SmartKamaVPN && .venv/bin/python3 scripts/_setup_mobile_v2.py
"""

import sys
import json
import re
import sqlite3
import shutil
import subprocess
from pathlib import Path

import requests

sys.path.insert(0, "/opt/SmartKamaVPN")
import config  # noqa: E402
import Utils.marzban_api as mapi  # noqa: E402

# ── Константы ─────────────────────────────────────────────────────────────────

NGINX_443_CONF = "/etc/nginx/conf.d/smartkama-443.conf"
BOT_DB_PATH    = "/opt/SmartKamaVPN/Database/smartkamavpn.db"

NL_IP  = "72.56.100.45"
RU_IP  = "72.56.252.250"
DOMAIN = "sub.smartkama.ru"

# Reality keypair — общий для всех Reality inbound-ов (одна пара ключей на сервер)
REALITY_PRIVATE_KEY = "YMvrrXDxs5nNuJObTdMFB8ttqWe8KGuUJJUyg7xfVF8"
REALITY_PUBLIC_KEY  = "xFtfS45HEb_mYNr1ETW3XTL2D_FdryWahS-KYTNIZGg"

# Старые теги — удалить
OLD_MOB_TAGS = ["mob-ws-1", "mob-ws-2", "mob-reality-ms", "mob-reality-ap"]

# WS пути — выглядят как HLS/CDN видеопоток (обход DPI операторов)
# ВАЖНО: /api/v2/hash → DPI fingerprint = VPN; /live/stream → DPI fingerprint = streaming
NL_WS1_PATH = "/live/stream/b7f3a91c"
NL_WS2_PATH = "/media/cdn/e5d2c8f6"

# ── Новые Xray inbound definitions ────────────────────────────────────────────

NEW_INBOUNDS = [
    # ─ NL WS-1 через nginx:443 ──────────────────────────────────────────────
    # Port 443 никогда не блокируется операторами — это обычный HTTPS.
    # nginx завершает TLS и проксирует plain WS на xray 127.0.0.1:48011.
    # Клиент видит: wss://sub.smartkama.ru/api/v2/nl-b7f3a91c — выглядит как API.
    {
        "tag":      "nl-mob-ws-1",
        "listen":   "127.0.0.1",
        "port":     48011,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "ws",
            "security": "none",
            "wsSettings": {
                "headers": {"Host": DOMAIN},
                "path": NL_WS1_PATH,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ─ NL WS-2 через nginx:443 (резервный) ───────────────────────────────────
    {
        "tag":      "nl-mob-ws-2",
        "listen":   "127.0.0.1",
        "port":     48012,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "ws",
            "security": "none",
            "wsSettings": {
                "headers": {"Host": DOMAIN},
                "path": NL_WS2_PATH,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ─ NL Reality — Яндекс.Карты API SNI (port 2083, Cloudflare совместимый) ─
    # api-maps.yandex.ru: российский гос.CDN, НИКОГДА не блокируется операторами.
    # Port 2083: порт Cloudflare панели — почти всегда открыт на мобильных операторах.
    # fingerprint=qq: QQ Browser TLS profile, менее предсказуемый чем chrome/safari.
    # show=False: убрать debug-флаг из продакшен конфига (не влияет на работу, чище логи).
    {
        "tag":      "nl-mob-reality-ms",
        "listen":   "0.0.0.0",
        "port":     2083,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "api-maps.yandex.ru:443",
                "fingerprint": "qq",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "api-maps.yandex.ru",
                    "yandex.ru",
                    "maps.yandex.ru",
                ],
                "shortIds": ["a1b2c3d4", "e5f6a7b8"],
                "show": False,
                "xver": 0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ─ NL Reality — MediaVitrina CDN SNI (port 2087, Cloudflare совместимый) ─
    # player.mediavitrina.ru: крупнейший российский видеоCDN, никогда не блокируется.
    # Используется для стриминга контента 90% рос. онлайн-кинотеатров.
    # fingerprint=qq: QQ Browser TLS profile.
    {
        "tag":      "nl-mob-reality-ap",
        "listen":   "0.0.0.0",
        "port":     2087,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "player.mediavitrina.ru:443",
                "fingerprint": "qq",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "player.mediavitrina.ru",
                    "mediavitrina.ru",
                    "mediavitrina.com",
                ],
                "shortIds": ["c9d8e7f6", "a5b4c3d2"],
                "show": False,
                "xver": 0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ─ RU Reality — Яндекс.Карты API SNI (port 2084) ─────────────────────────
    # Отдельный port 2084 для RU чтобы subscription link указывал на RU IP:2084
    # и не конфликтовал с nl-mob-reality-ms (port 2083 → NL IP).
    {
        "tag":      "ru-mob-reality-ms",
        "listen":   "0.0.0.0",
        "port":     2084,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "api-maps.yandex.ru:443",
                "fingerprint": "qq",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "api-maps.yandex.ru",
                    "yandex.ru",
                    "maps.yandex.ru",
                ],
                "shortIds": ["f1e2d3c4", "b5a6f7e8"],
                "show": False,
                "xver": 0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
    # ─ RU Reality — MediaVitrina CDN SNI (port 2088) ─────────────────────────
    {
        "tag":      "ru-mob-reality-ap",
        "listen":   "0.0.0.0",
        "port":     2088,
        "protocol": "vless",
        "settings": {"clients": [], "decryption": "none"},
        "streamSettings": {
            "network":  "tcp",
            "security": "reality",
            "realitySettings": {
                "dest":        "player.mediavitrina.ru:443",
                "fingerprint": "qq",
                "privateKey":  REALITY_PRIVATE_KEY,
                "publicKey":   REALITY_PUBLIC_KEY,
                "serverNames": [
                    "player.mediavitrina.ru",
                    "mediavitrina.ru",
                    "mediavitrina.com",
                ],
                "shortIds": ["d9c8b7a6", "e1f2a3b4"],
                "show": False,
                "xver": 0,
            },
        },
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]},
    },
]

NEW_TAGS = [ib["tag"] for ib in NEW_INBOUNDS]

# ── Marzban hosts entries ──────────────────────────────────────────────────────
# PUT /api/hosts формат: {inbound_tag: [host_obj, ...]}
# Поля соответствуют схеме таблицы hosts в Marzban SQLite.

def _build_new_hosts() -> dict:
    _ws_defaults = {
        "port": 443,
        "address": DOMAIN,
        "sni": DOMAIN,
        "host": DOMAIN,
        "security": "tls",
        "alpn": "h2,http/1.1",
        "fingerprint": "chrome",
        "allowinsecure": False,
        "is_disabled": False,
        "mux_enable": False,
        "fragment_setting": None,
        "random_user_agent": False,
        "noise_setting": None,
        "use_sni_as_host": False,
    }
    _reality_defaults = {
        "port": None,
        "sni": None,
        "host": None,
        "path": None,
        "security": "inbound_default",
        "alpn": "",
        "fingerprint": "",
        "allowinsecure": None,
        "is_disabled": False,
        "mux_enable": False,
        "fragment_setting": None,
        "random_user_agent": False,
        "noise_setting": None,
        "use_sni_as_host": False,
    }
    return {
        "nl-mob-ws-1": [{
            **_ws_defaults,
            "remark": "🇳🇱 Нидерланды - Мобильный WS ({USERNAME})",
            "path": NL_WS1_PATH,
        }],
        "nl-mob-ws-2": [{
            **_ws_defaults,
            "remark": "🇳🇱 Нидерланды - Мобильный WS-2 ({USERNAME})",
            "path": NL_WS2_PATH,
        }],
        "nl-mob-reality-ms": [{
            **_reality_defaults,
            "remark": "🇳🇱 Нидерланды - Мобильный Reality (Яндекс) ({USERNAME})",
            "address": NL_IP,
        }],
        "nl-mob-reality-ap": [{
            **_reality_defaults,
            "remark": "🇳🇱 Нидерланды - Мобильный Reality (MediaVitrina) ({USERNAME})",
            "address": NL_IP,
        }],
        "ru-mob-reality-ms": [{
            **_reality_defaults,
            "remark": "🇷🇺 Москва - Мобильный Reality (Яндекс)",
            "address": RU_IP,
        }],
        "ru-mob-reality-ap": [{
            **_reality_defaults,
            "remark": "🇷🇺 Москва - Мобильный Reality (MediaVitrina)",
            "address": RU_IP,
        }],
    }


# ── API helpers ───────────────────────────────────────────────────────────────

def _get_xray_config(token: str) -> dict:
    resp = requests.get(
        f"{config.MARZBAN_PANEL_URL}/api/core/config",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _put_xray_config(token: str, cfg: dict) -> None:
    resp = requests.put(
        f"{config.MARZBAN_PANEL_URL}/api/core/config",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=cfg,
        timeout=30,
    )
    resp.raise_for_status()


def _get_hosts(token: str) -> dict:
    resp = requests.get(
        f"{config.MARZBAN_PANEL_URL}/api/hosts",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _put_hosts(token: str, hosts: dict) -> None:
    resp = requests.put(
        f"{config.MARZBAN_PANEL_URL}/api/hosts",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=hosts,
        timeout=30,
    )
    resp.raise_for_status()


def _get_all_users(token: str) -> list:
    users: list = []
    offset = 0
    while True:
        resp = requests.get(
            f"{config.MARZBAN_PANEL_URL}/api/users",
            headers={"Authorization": f"Bearer {token}"},
            params={"offset": offset, "limit": 100},
            timeout=20,
        )
        resp.raise_for_status()
        batch = resp.json().get("users", [])
        users.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return users


def _update_user_inbounds(token: str, username: str, inbounds: dict) -> None:
    resp = requests.put(
        f"{config.MARZBAN_PANEL_URL}/api/user/{username}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"inbounds": inbounds},
        timeout=15,
    )
    resp.raise_for_status()


# ── nginx helpers ─────────────────────────────────────────────────────────────

def _remove_nginx_old_mobile_blocks(conf_text: str) -> str:
    """Удаляет старые mob-ws-1/2 location blocks из nginx conf."""
    # Паттерн: комментарий + location block (добавленные v1 скриптом)
    pattern = re.compile(
        r"\n[ \t]*# Mobile WS \[mob-ws-\d+\][^\n]*\n"
        r"[ \t]+location /api/m\d+-[a-f0-9]+ \{[^}]+\}",
        re.DOTALL,
    )
    cleaned = pattern.sub("", conf_text)
    if cleaned != conf_text:
        print("     Удалены старые nginx блоки mob-ws-1/2")
    return cleaned


def _add_nginx_mobile_ws_blocks(conf_text: str) -> str:
    """Добавляет WS location blocks для nl-mob-ws-1/2 перед 'location = /'."""
    ws_entries = [
        ("nl-mob-ws-1", 48011, NL_WS1_PATH),
        ("nl-mob-ws-2", 48012, NL_WS2_PATH),
    ]
    blocks = ""
    for tag, port, path in ws_entries:
        if path in conf_text:
            print(f"     {tag}: nginx блок уже есть ({path})")
            continue
        blocks += (
            f"\n    # Mobile WS [{tag}] — мобильные операторы обход DPI (CDN порт 443)\n"
            f"    location {path} {{\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header Upgrade $http_upgrade;\n"
            f"        proxy_set_header Connection \"upgrade\";\n"
            f"        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Real-IP $remote_addr;\n"
            f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            f"        proxy_read_timeout 86400s;\n"
            f"        proxy_send_timeout 86400s;\n"
            f"    }}"
        )
        print(f"     {tag}: добавляется nginx блок на порт {port}, path {path}")

    if not blocks:
        return conf_text

    marker = "\n    location = / {"
    if marker not in conf_text:
        marker = "\n    location / {"
    if marker not in conf_text:
        # Вставить перед закрывающей скобкой server блока
        conf_text = conf_text.rstrip().rstrip("}").rstrip() + blocks + "\n}"
        return conf_text
    return conf_text.replace(marker, blocks + marker, 1)


def _test_and_reload_nginx() -> bool:
    """nginx -t (пробует Hiddify conf если есть, затем стандартный)."""
    hiddify_conf = "/opt/hiddify-manager/nginx/nginx.conf"
    cmds = []
    if Path(hiddify_conf).exists():
        cmds.append(["/usr/sbin/nginx", "-t", "-c", hiddify_conf])
    cmds.append(["/usr/sbin/nginx", "-t"])

    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            subprocess.run(["systemctl", "reload", "nginx"], check=True)
            return True
        # Попробуем следующий вариант если 'no such file' в stderr
        if "no such file" not in result.stderr.lower():
            print(f"     ERROR nginx -t: {result.stderr.strip()}")
            return False
    print("     ERROR: все варианты nginx -t завершились с ошибкой")
    return False


# ── Bot DB helper ─────────────────────────────────────────────────────────────

def _update_bot_db(tags_to_remove: list, tags_to_add: list) -> None:
    conn = sqlite3.connect(BOT_DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM str_config WHERE key='marzban_inbound_tags'")
        row = cur.fetchone()
        current = (row[0] or "") if row else ""
        existing = [t.strip() for t in current.split(",") if t.strip()]
        # Убрать старые, добавить новые
        updated = [t for t in existing if t not in tags_to_remove]
        for t in tags_to_add:
            if t not in updated:
                updated.append(t)
        new_value = ",".join(updated)
        cur.execute(
            "UPDATE str_config SET value=? WHERE key='marzban_inbound_tags'",
            (new_value,),
        )
        conn.commit()
        print(f"     marzban_inbound_tags → {new_value}")
    finally:
        conn.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("SmartKamaVPN — Мобильные inbound-ы v2 (правильная интеграция Marzban)")
    print("=" * 70)

    # ── [1] Auth ──────────────────────────────────────────────────────────────
    token = mapi._get_access_token()
    print("[1] Marzban авторизация: OK")

    # ── [2] Обновить Xray config (удалить старые, добавить новые inbound-ы) ──
    cfg = _get_xray_config(token)
    inbounds: list = cfg.get("inbounds", [])
    existing_tags = {ib["tag"] for ib in inbounds}

    removed_ibs = [ib for ib in inbounds if ib["tag"] in OLD_MOB_TAGS]
    inbounds     = [ib for ib in inbounds if ib["tag"] not in OLD_MOB_TAGS]
    if removed_ibs:
        print(f"[2] Удалены старые inbound-ы: {[ib['tag'] for ib in removed_ibs]}")
    else:
        print("[2] Старые mob-* inbound-ы не найдены (уже чисто)")

    to_add = [ib for ib in NEW_INBOUNDS if ib["tag"] not in existing_tags]
    already = [ib["tag"] for ib in NEW_INBOUNDS if ib["tag"] in existing_tags]
    inbounds += to_add
    if already:
        print(f"[2] Уже существуют (пропущены): {already}")
    print(f"[2] Добавляются: {[ib['tag'] for ib in to_add]}")

    cfg["inbounds"] = inbounds
    _put_xray_config(token, cfg)
    print("[2] Xray config обновлён OK")

    # ── [3] nginx: удалить старые блоки, добавить новые ──────────────────────
    nginx_path = Path(NGINX_443_CONF)
    if not nginx_path.exists():
        print(f"[3] ПРЕДУПРЕЖДЕНИЕ: {NGINX_443_CONF} не найден — nginx пропущен")
    else:
        backup = NGINX_443_CONF + ".bak.mobile-v2"
        shutil.copy2(NGINX_443_CONF, backup)
        print(f"[3] Бэкап nginx → {backup}")

        text = nginx_path.read_text(encoding="utf-8")
        text = _remove_nginx_old_mobile_blocks(text)
        text = _add_nginx_mobile_ws_blocks(text)
        nginx_path.write_text(text, encoding="utf-8")

        if _test_and_reload_nginx():
            print("[3] nginx обновлён и перезагружен OK")
        else:
            print("[3] ОШИБКА nginx — восстанавливаю бэкап...")
            shutil.copy2(backup, NGINX_443_CONF)
            print("     Бэкап восстановлен. Прерывание.")
            sys.exit(1)

    # ── [4] Marzban hosts table (PUT /api/hosts) ───────────────────────────────
    # PUT /api/hosts работает как UPSERT/MERGE:
    # - Присланные теги → обновляются/создаются
    # - Остальные теги → остаются без изменений
    # Поэтому шлём ТОЛЬКО новые записи, не трогаем существующие.
    # Старые mob-ws-1/mob-ws-2 записи останутся в hosts, но будут orphaned
    # (их inbound tags уже удалены из Xray) → Marzban не сгенерирует ссылки.
    new_hosts = _build_new_hosts()
    for tag, host_list in new_hosts.items():
        remark = host_list[0]["remark"]
        addr   = host_list[0]["address"]
        port   = host_list[0].get("port") or "(inbound port)"
        print(f"     Добавляется host: {tag} → {addr}:{port}  «{remark}»")

    _put_hosts(token, new_hosts)
    print(f"[4] Marzban hosts обновлён OK ({len(new_hosts)} новых записей)")

    # ── [5] Открыть порты на RU сервере ───────────────────────────────────────
    # NL: порты 2083, 2087 уже были открыты для старых mob-reality-ms/ap.
    # RU: порты 2083, 2087 уже были открыты; 2084, 2088 — новые.
    ru_ports = [2083, 2084, 2087, 2088]
    print(f"[5] Открываем порты {ru_ports} на RU сервере ({RU_IP})...")
    ru_fw_cmd = " && ".join(f"ufw allow {p}/tcp" for p in ru_ports) + " && ufw reload"
    try:
        result = subprocess.run(
            [
                "ssh", "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10",
                "-i", "/root/.ssh/id_rsa",
                f"root@{RU_IP}",
                ru_fw_cmd,
            ],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0:
            out = result.stdout.strip().replace("\n", " | ")
            print(f"     RU firewall OK: {out}")
        else:
            print(f"     ПРЕДУПРЕЖДЕНИЕ: {result.stderr.strip()}")
            print(f"     Порты 2084, 2088 нужно открыть вручную на {RU_IP}")
    except Exception as exc:
        print(f"     ПРЕДУПРЕЖДЕНИЕ: SSH к RU не удался ({exc})")
        print(f"     Откройте вручную: ufw allow 2084/tcp && ufw allow 2088/tcp")

    # ── [6] Обновить пользователей Marzban ────────────────────────────────────
    users  = _get_all_users(token)
    active = [u for u in users if u.get("status") in ("active", "on_hold")]
    print(f"[6] Обновляем {len(active)} активных пользователей...")

    updated = errors = skipped = 0
    for user in active:
        username     = user.get("username", "")
        cur_inbounds = user.get("inbounds") or {}
        if not isinstance(cur_inbounds, dict):
            cur_inbounds = {}

        vless_tags = list(cur_inbounds.get("vless", []))
        # Убрать старые mob-* теги
        vless_tags = [t for t in vless_tags if t not in OLD_MOB_TAGS]
        # Добавить новые теги
        old_had_mob = any(
            t in (user.get("inbounds") or {}).get("vless", [])
            for t in OLD_MOB_TAGS
        )
        changed = old_had_mob
        for tag in NEW_TAGS:
            if tag not in vless_tags:
                vless_tags.append(tag)
                changed = True

        if not changed:
            skipped += 1
            continue

        new_inbounds = {**cur_inbounds, "vless": vless_tags}
        try:
            _update_user_inbounds(token, username, new_inbounds)
            updated += 1
            if updated <= 3 or updated % 20 == 0:
                print(f"     [{updated}] {username}: OK")
        except Exception as exc:
            print(f"     ОШИБКА {username}: {exc}")
            errors += 1

    print(f"[6] Пользователи: {updated} обновлено, {skipped} без изменений, {errors} ошибок")

    # ── [7] Обновить bot DB ────────────────────────────────────────────────────
    print("[7] Обновляем marzban_inbound_tags в bot DB...")
    _update_bot_db(OLD_MOB_TAGS, NEW_TAGS)

    # ── Итог ──────────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("ГОТОВО! Новые мобильные inbound-ы с правильными subscription links:")
    print()
    summary = [
        ("nl-mob-ws-1",       "Нидерланды - Мобильный WS",               f"{DOMAIN}:443  (WS/TLS)"),
        ("nl-mob-ws-2",       "Нидерланды - Мобильный WS-2",             f"{DOMAIN}:443  (WS/TLS)"),
        ("nl-mob-reality-ms", "Нидерланды - Мобильный Reality (MS)",     f"{NL_IP}:2083  (Reality/Microsoft)"),
        ("nl-mob-reality-ap", "Нидерланды - Мобильный Reality (Apple)",  f"{NL_IP}:2087  (Reality/Apple)"),
        ("ru-mob-reality-ms", "Москва - Мобильный Reality (MS)",         f"{RU_IP}:2084  (Reality/Microsoft)"),
        ("ru-mob-reality-ap", "Москва - Мобильный Reality (Apple)",      f"{RU_IP}:2088  (Reality/Apple)"),
    ]
    for tag, name, addr in summary:
        print(f"  ✓ {tag:24s}  {name}")
        print(f"    {'':24s}  → {addr}")
        print()
    print("  Перезапустите бот: systemctl restart smartkamavpn")
    print("=" * 70)


if __name__ == "__main__":
    main()
