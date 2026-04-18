#!/usr/bin/env python3
"""
scripts/_upgrade_mobile_cdn.py
═══════════════════════════════════════════════════════════════════════════════

╔══════════════════════════════════════════════════════════════════════════════╗
║         ПРОМТ: Мобильный VPN для обхода DPI операторов РФ                   ║
║         Анализ реально работающих конфигов → максимальная маскировка        ║
╚══════════════════════════════════════════════════════════════════════════════╝

ЗАДАЧА
──────
Настроить VPN-прокси для работы через мобильный интернет Билайн/МТС/Мегафон/Теле2.
DPI (Deep Packet Inspection) этих операторов умеет блокировать:
  • Голые TCP-соединения с нестандартным паттерном
  • VPN-протоколы с характерными заголовками (OpenVPN, WireGuard)
  • WebSocket с путями, похожими на хэш-токены (/api/xxx-hash)
  • Reality с иностранными SNI, которые оператор может дросселировать

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
АНАЛИЗ РЕАЛЬНО РАБОТАЮЩИХ КОНФИГОВ (18.04.2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✅ Config 1 — WS "🇳🇱 LTE Универсальный" (работает на всех операторах):
      address: 81.161.98.162:10065
      network: ws
      path:    /live/stream?module=video-cdn&app_client_id=cc8c59b8-...
      host:    kinograd.online      ← российский онлайн-кинотеатр
      SNI:     kinograd.online      ← реальный TLS cert
      alpn:    h3, h2, http/1.1     ← максимальная совместимость
      fp:      qq                   ← непредсказуемый fingerprint

      ВЫВОД: Путь выглядит как запрос к видео CDN (DPI видит "стриминг")

  ✅ Config 2 — Reality "🇩🇪 LTE Универсальный" (api-maps.yandex.ru):
      address: b0129fq55-1mr4.ru-a1.y-tun.com:40443
      network: tcp + reality
      flow:    xtls-rprx-vision     ← КРИТИЧНО: защита от TLS-в-TLS зондирования
      SNI:     api-maps.yandex.ru   ← Яндекс.Карты API (НИКОГДА не блокируется)
      fp:      qq

      ВЫВОД: Yandex SNI ← российские операторы не блокируют собственные CDN

  ✅ Config 3 — Reality "🇩🇪 LTE Универсальный" (player.mediavitrina.ru):
      address: a01b9-fq55-1mmi.cdn.mediavitrina.com:9443
      network: tcp + reality
      flow:    xtls-rprx-vision
      SNI:     player.mediavitrina.ru  ← MediaVitrina CDN (крупнейший видеоCDN РФ)
      fp:      qq

      ВЫВОД: Российские CDN домены — операторы их приоритизируют, не блокируют

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ЛУЧШИЕ SNI ДЛЯ РОССИЙСКИХ МОБИЛЬНЫХ ОПЕРАТОРОВ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Tier 1 — НИКОГДА не блокируется ни одним оператором:
    • api-maps.yandex.ru      — Яндекс.Карты API, гос. инфраструктура
    • player.mediavitrina.ru  — MediaVitrina, поставляет видео для 90% рос. ТВ
    • player.vgtrk.com        — ВГТРК (федеральное ТВ, неприкосновенно)
    • gosuslugi.ru            — Госуслуги (юридически не может быть заблокирован)

  Tier 2 — Почти никогда не блокируется:
    • vk.com / vk-cdn.net     — ВКонтакте, собственный CDN
    • yandex.ru / ya.ru       — Яндекс (в целом)
    • mail.ru / okcdn.ru      — Одноклассники/Mail.ru CDN

  ❌ НЕ ИСПОЛЬЗОВАТЬ для РФ мобильных (иностранные CDN):
    • www.microsoft.com       — Azure CDN дросселируется в ряде регионов
    • www.apple.com           — то же самое
    • google.com / youtube.com — заблокированы/дросселируются у части операторов

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
УЯЗВИМОСТИ ПРЕДЫДУЩЕЙ КОНФИГУРАЦИИ (v2) → ИСПРАВЛЕНО В v3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [VULN-1] WS пути выглядят как VPN:
    БЫЛО:   /api/v2/nl-b7f3a91c  ← DPI: "API endpoint с хэш-суффиксом = VPN"
    СТАЛО:  /live/stream/b7f3a91c ← DPI: "HLS видеопоток от CDN"

  [VULN-2] Reality SNI — иностранные CDN (дросселируются у ряда операторов):
    БЫЛО:   www.microsoft.com, www.apple.com
    СТАЛО:  api-maps.yandex.ru, player.mediavitrina.ru

  [VULN-3] fingerprint предсказуем:
    БЫЛО:   chrome, safari  ← DPI может сравнить с реальным Chrome/Safari
    СТАЛО:  qq              ← QQ Browser, менее предсказуемый профиль

  [VULN-4] show: True в realitySettings — debug-флаг в продакшене:
    БЫЛО:   "show": True    ← лишний вывод в логах, не нужен
    СТАЛО:  "show": False

  [VULN-5] ALPN не включает h3:
    БЫЛО:   h2, http/1.1
    СТАЛО:  h2, http/1.1  (h3/QUIC требует UDP в nginx — отдельная задача)

АРХИТЕКТУРА (2 сервера):
━━━━━━━━━━━━━━━━━━━━━━━
  NL (72.56.100.45):   nginx:443 → Xray WS (48011, 48012) + Reality (2083, 2087)
  RU (72.56.252.250):  НЕТ nginx, Reality только (2084, 2088)

Запуск: cd /opt/SmartKamaVPN && .venv/bin/python3 scripts/_upgrade_mobile_cdn.py
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, "/opt/SmartKamaVPN")
import config          # noqa: E402
import Utils.marzban_api as mapi  # noqa: E402

# ── Константы ─────────────────────────────────────────────────────────────────

NGINX_443_CONF = "/etc/nginx/conf.d/smartkama-443.conf"
NL_IP  = "72.56.100.45"
RU_IP  = "72.56.252.250"
DOMAIN = "sub.smartkama.ru"

# ── WS пути: было vs стало ────────────────────────────────────────────────────
# Было: /api/v2/... — очевидный API hash путь (DPI fingerprint = VPN)
# Стало: /live/stream/... — выглядит как HLS/CDN видеопоток (DPI fingerprint = streaming)
WS_UPGRADES = {
    "nl-mob-ws-1": {
        "old_path":  "/api/v2/nl-b7f3a91c",
        "new_path":  "/live/stream/b7f3a91c",   # HLS CDN стриминг
        "port":      48011,
        "new_remark": "🇳🇱 Нидерланды - Мобильный WS ({USERNAME})",
    },
    "nl-mob-ws-2": {
        "old_path":  "/api/v2/nl-e5d2c8f6",
        "new_path":  "/media/cdn/e5d2c8f6",     # медиа CDN
        "port":      48012,
        "new_remark": "🇳🇱 Нидерланды - Мобильный WS-2 ({USERNAME})",
    },
}

# ── Reality: было vs стало ────────────────────────────────────────────────────
# SNI изменяем на российские CDN — операторы РФ их никогда не блокируют.
# fingerprint: chrome/safari → qq (менее предсказуемый TLS fingerprint)
# show: True → False (убрать debug-флаг из продакшен конфига)
REALITY_UPGRADES = {
    "nl-mob-reality-ms": {
        "dest":        "api-maps.yandex.ru:443",
        "serverNames": ["api-maps.yandex.ru", "yandex.ru", "maps.yandex.ru"],
        "fingerprint": "qq",
        "show":        False,
        "new_remark":  "🇳🇱 Нидерланды - Мобильный Reality (Яндекс) ({USERNAME})",
    },
    "nl-mob-reality-ap": {
        "dest":        "player.mediavitrina.ru:443",
        "serverNames": ["player.mediavitrina.ru", "mediavitrina.ru", "mediavitrina.com"],
        "fingerprint": "qq",
        "show":        False,
        "new_remark":  "🇳🇱 Нидерланды - Мобильный Reality (MediaVitrina) ({USERNAME})",
    },
    "ru-mob-reality-ms": {
        "dest":        "api-maps.yandex.ru:443",
        "serverNames": ["api-maps.yandex.ru", "yandex.ru", "maps.yandex.ru"],
        "fingerprint": "qq",
        "show":        False,
        "new_remark":  "🇷🇺 Москва - Мобильный Reality (Яндекс)",
    },
    "ru-mob-reality-ap": {
        "dest":        "player.mediavitrina.ru:443",
        "serverNames": ["player.mediavitrina.ru", "mediavitrina.ru", "mediavitrina.com"],
        "fingerprint": "qq",
        "show":        False,
        "new_remark":  "🇷🇺 Москва - Мобильный Reality (MediaVitrina)",
    },
}


# ── API helpers ────────────────────────────────────────────────────────────────

def _api_get(token: str, path: str) -> dict:
    r = requests.get(
        f"{config.MARZBAN_PANEL_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _api_put(token: str, path: str, payload: dict) -> dict:
    r = requests.put(
        f"{config.MARZBAN_PANEL_URL}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Xray config patch ─────────────────────────────────────────────────────────

def patch_xray_config(token: str) -> bool:
    """Обновляет Xray inbound-ы: WS пути + Reality SNI/fingerprint/show."""
    cfg = _api_get(token, "/api/core/config")
    inbounds = cfg.get("inbounds", [])
    changed = False

    for ib in inbounds:
        tag = ib.get("tag", "")
        ss = ib.get("streamSettings", {})

        # ── WS: обновить путь ──────────────────────────────────────────────
        if tag in WS_UPGRADES:
            upd = WS_UPGRADES[tag]
            ws = ss.get("wsSettings", {})
            cur_path = ws.get("path", "")
            if cur_path == upd["old_path"]:
                ws["path"] = upd["new_path"]
                print(f"   [{tag}] path: {upd['old_path']} → {upd['new_path']}")
                changed = True
            elif cur_path == upd["new_path"]:
                print(f"   [{tag}] path уже актуален ({upd['new_path']})")
            else:
                print(f"   [{tag}] ВНИМАНИЕ: неожиданный путь '{cur_path}' — пропуск")

        # ── Reality: обновить SNI, dest, fingerprint, show ────────────────
        if tag in REALITY_UPGRADES:
            upd = REALITY_UPGRADES[tag]
            rs = ss.get("realitySettings", {})

            old_dest = rs.get("dest", "")
            old_sni  = rs.get("serverNames", [])
            old_fp   = rs.get("fingerprint", "")
            old_show = rs.get("show", None)

            rs["dest"]        = upd["dest"]
            rs["serverNames"] = upd["serverNames"]
            rs["fingerprint"] = upd["fingerprint"]
            rs["show"]        = upd["show"]

            print(f"   [{tag}]")
            print(f"     dest:        {old_dest} → {upd['dest']}")
            print(f"     serverNames: {old_sni[:1]} → {upd['serverNames'][:1]}")
            print(f"     fingerprint: {old_fp!r} → {upd['fingerprint']!r}")
            print(f"     show:        {old_show} → {upd['show']}")
            changed = True

    if changed:
        _api_put(token, "/api/core/config", cfg)
        print("   ✅ Xray config сохранён")
    else:
        print("   ℹ️  Xray config уже актуален — изменений нет")

    return changed


# ── Marzban hosts patch ───────────────────────────────────────────────────────

def patch_marzban_hosts(token: str) -> bool:
    """Обновляет Marzban hosts: пути WS + новые remarks для Reality."""
    hosts = _api_get(token, "/api/hosts")
    hosts_update: dict = {}

    # WS — обновить путь и remark
    for tag, upd in WS_UPGRADES.items():
        if tag not in hosts:
            print(f"   [{tag}] запись в hosts не найдена — пропуск")
            continue
        entries = hosts[tag]
        if not isinstance(entries, list) or not entries:
            continue
        h = entries[0]
        changed_entry = False
        if h.get("path") == upd["old_path"]:
            h["path"] = upd["new_path"]
            print(f"   [{tag}] path: {upd['old_path']} → {upd['new_path']}")
            changed_entry = True
        if h.get("remark") != upd["new_remark"]:
            h["remark"] = upd["new_remark"]
            changed_entry = True
        if changed_entry:
            hosts_update[tag] = entries

    # Reality — обновить только remark (адрес и порт не меняются)
    for tag, upd in REALITY_UPGRADES.items():
        if tag not in hosts:
            print(f"   [{tag}] запись в hosts не найдена — пропуск")
            continue
        entries = hosts[tag]
        if not isinstance(entries, list) or not entries:
            continue
        h = entries[0]
        if h.get("remark") != upd["new_remark"]:
            h["remark"] = upd["new_remark"]
            print(f"   [{tag}] remark → {upd['new_remark']}")
            hosts_update[tag] = entries

    if hosts_update:
        _api_put(token, "/api/hosts", hosts_update)
        print("   ✅ Marzban hosts обновлён")
        return True
    else:
        print("   ℹ️  Marzban hosts уже актуален")
        return False


# ── nginx patch ───────────────────────────────────────────────────────────────

def patch_nginx(token: str) -> bool:
    """Обновляет WS location пути в nginx: /api/v2/... → /live/stream/... и /media/cdn/..."""
    nginx_path = Path(NGINX_443_CONF)
    if not nginx_path.exists():
        print(f"   ПРОПУСК: {NGINX_443_CONF} не найден (не NL сервер?)")
        return False

    text = nginx_path.read_text(encoding="utf-8")
    orig = text
    changed = False

    for tag, upd in WS_UPGRADES.items():
        if upd["old_path"] in text:
            text = text.replace(
                f"location {upd['old_path']}",
                f"location {upd['new_path']}",
            )
            print(f"   [{tag}] nginx location: {upd['old_path']} → {upd['new_path']}")
            changed = True
        elif upd["new_path"] in text:
            print(f"   [{tag}] nginx location уже актуален ({upd['new_path']})")
        else:
            print(f"   [{tag}] ВНИМАНИЕ: путь не найден в nginx conf!")

    if not changed:
        print("   ℹ️  nginx conf уже актуален")
        return False

    # Бэкап + запись
    backup = NGINX_443_CONF + ".bak.cdn-upgrade"
    shutil.copy2(NGINX_443_CONF, backup)
    nginx_path.write_text(text, encoding="utf-8")

    # Проверка конфига
    hiddify_conf = "/opt/hiddify-manager/nginx/nginx.conf"
    if Path(hiddify_conf).exists():
        test_cmd = ["/usr/sbin/nginx", "-t", "-c", hiddify_conf]
    else:
        test_cmd = ["/usr/sbin/nginx", "-t"]

    result = subprocess.run(test_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"   ❌ nginx -t ОШИБКА: {result.stderr.strip()}")
        shutil.copy2(backup, NGINX_443_CONF)
        print("   ⚠️  Бэкап восстановлен. Исправьте конфиг вручную.")
        sys.exit(1)

    subprocess.run(["systemctl", "reload", "nginx"], check=True)
    print("   ✅ nginx перезагружен")
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  SmartKamaVPN — Апгрейд мобильных прокси (CDN маскировка v3)   ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    # Аутентификация
    token = mapi._get_access_token()
    print("[AUTH] Marzban: OK")
    print()

    # [1] Xray config
    print("[1] Обновление Xray inbound-ов:")
    patch_xray_config(token)
    print()

    # [2] nginx
    print("[2] Обновление nginx location блоков:")
    patch_nginx(token)
    print()

    # [3] Marzban hosts
    print("[3] Обновление Marzban hosts (путь, remark):")
    patch_marzban_hosts(token)
    print()

    # ── Итог ──────────────────────────────────────────────────────────────────
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  АПГРЕЙД ЗАВЕРШЁН                                               ║")
    print("╠══════════════════════════════════════════════════════════════════╣")
    print("║                                                                  ║")
    print("║  WS (CDN маскировка):                                           ║")
    print(f"║    nl-mob-ws-1: {DOMAIN}:443/live/stream/b7f3a91c    ║")
    print(f"║    nl-mob-ws-2: {DOMAIN}:443/media/cdn/e5d2c8f6      ║")
    print("║                                                                  ║")
    print("║  Reality (Российские CDN SNI, fp=qq):                          ║")
    print(f"║    nl-mob-reality-ms: {NL_IP}:2083 → api-maps.yandex.ru  ║")
    print(f"║    nl-mob-reality-ap: {NL_IP}:2087 → player.mediavitrina ║")
    print(f"║    ru-mob-reality-ms: {RU_IP}:2084 → api-maps.yandex.ru  ║")
    print(f"║    ru-mob-reality-ap: {RU_IP}:2088 → player.mediavitrina ║")
    print("║                                                                  ║")
    print("║  Следующий шаг:                                                  ║")
    print("║    systemctl restart smartkamavpn                               ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    print("⚠️  Пользователи должны обновить конфиги в приложениях!")
    print("   QR-коды и deeplinks в боте будут указывать на новые пути.")
    print()


if __name__ == "__main__":
    main()
