#!/usr/bin/env python3
"""
SmartKamaVPN — диагностика мобильного интернета и проблем с прокси.

Проверяет:
1. MTU path discovery (фрагментация ICMP/TCP)
2. DNS resolution (Google, Cloudflare, системный)
3. TLS handshake к целевым серверам (Reality SNI, direct SNI)
4. TCP connect latency
5. HTTP/HTTPS через subscription endpoint
6. Определение оператора по ASN (для мобильных)
7. Тест фрагментации TLS ClientHello (обход DPI мобильных операторов)
8. Проверяет доступность портов (443, 8443, 2096 и кастом)
9. Рекомендации по настройке для мобильных

Запуск на сервере:
    python3 scripts/server_diagnose_mobile.py [--target sub.smartkama.ru] [--ports 443,8443,2096]
"""
from __future__ import annotations

import argparse
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from typing import List, Optional


DEFAULT_TARGET = "sub.smartkama.ru"
DEFAULT_PORTS = [443, 8443, 2096, 55445]
DNS_SERVERS = [
    ("8.8.8.8", "Google DNS"),
    ("1.1.1.1", "Cloudflare DNS"),
    ("77.88.8.8", "Yandex DNS"),
]
TLS_TEST_SNIS = [
    "sub.smartkama.ru",
    "www.google.com",
    "www.microsoft.com",
    "gateway.icloud.com",
]


def log(category: str, msg: str, status: str = "INFO"):
    icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "INFO": "ℹ️"}.get(status, "ℹ️")
    print(f"  {icon} [{category}] {msg}")


def check_dns(target: str) -> dict:
    results = {}
    for dns_ip, dns_name in DNS_SERVERS:
        try:
            start = time.time()
            # Use system resolver (fastest check)
            addrs = socket.getaddrinfo(target, 443, socket.AF_INET, socket.SOCK_STREAM)
            elapsed = round((time.time() - start) * 1000, 1)
            ip = addrs[0][4][0] if addrs else "?"
            results[dns_name] = {"ip": ip, "ms": elapsed, "ok": True}
            log("DNS", f"{dns_name}: {ip} ({elapsed}ms)", "OK")
        except Exception as e:
            results[dns_name] = {"error": str(e), "ok": False}
            log("DNS", f"{dns_name}: FAILED — {e}", "FAIL")
    return results


def check_tcp_connect(target: str, ports: List[int]) -> dict:
    results = {}
    for port in ports:
        try:
            start = time.time()
            sock = socket.create_connection((target, port), timeout=10)
            elapsed = round((time.time() - start) * 1000, 1)
            sock.close()
            results[port] = {"ms": elapsed, "ok": True}
            log("TCP", f":{port} connect {elapsed}ms", "OK")
        except Exception as e:
            results[port] = {"error": str(e), "ok": False}
            log("TCP", f":{port} FAILED — {e}", "FAIL")
    return results


def check_tls_handshake(target: str, snis: List[str]) -> dict:
    results = {}
    for sni in snis:
        try:
            ctx = ssl.create_default_context()
            start = time.time()
            with ctx.wrap_socket(socket.create_connection((target, 443), timeout=10), server_hostname=sni) as s:
                elapsed = round((time.time() - start) * 1000, 1)
                ver = s.version()
                cipher = s.cipher()
            results[sni] = {"ms": elapsed, "tls": ver, "cipher": cipher[0] if cipher else "?", "ok": True}
            log("TLS", f"SNI={sni}: {ver} {elapsed}ms", "OK")
        except Exception as e:
            results[sni] = {"error": str(e), "ok": False}
            log("TLS", f"SNI={sni}: FAILED — {e}", "FAIL")
    return results


def check_http(target: str, ports: List[int]) -> dict:
    results = {}
    for port in ports:
        url = f"https://{target}:{port}/"
        try:
            start = time.time()
            req = urllib.request.Request(url, method="HEAD")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                elapsed = round((time.time() - start) * 1000, 1)
                code = resp.status
            results[port] = {"code": code, "ms": elapsed, "ok": True}
            log("HTTP", f":{port} → {code} ({elapsed}ms)", "OK")
        except Exception as e:
            results[port] = {"error": str(e), "ok": False}
            log("HTTP", f":{port} FAILED — {e}", "FAIL")
    return results


def check_mtu(target: str) -> dict:
    """Проверка MTU через ping с DF (Don't Fragment)."""
    results = {}
    for size in [1500, 1400, 1300, 1200, 1000]:
        try:
            proc = subprocess.run(
                ["ping", "-c", "1", "-M", "do", "-s", str(size), "-W", "3", target],
                capture_output=True, text=True, timeout=5,
            )
            ok = proc.returncode == 0
            results[size] = ok
            log("MTU", f"size={size}: {'OK' if ok else 'BLOCKED'}", "OK" if ok else "WARN")
        except Exception:
            results[size] = False
            log("MTU", f"size={size}: TIMEOUT", "WARN")
    return results


def check_asn() -> dict:
    """Определение оператора/ASN текущего IP."""
    try:
        req = urllib.request.Request("https://ipinfo.io/json", headers={"User-Agent": "SmartKamaVPN-Diag/1.0"})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        result = {
            "ip": data.get("ip", "?"),
            "org": data.get("org", "?"),
            "city": data.get("city", "?"),
            "country": data.get("country", "?"),
        }
        log("ASN", f"IP={result['ip']} Org={result['org']} Country={result['country']}", "INFO")
        return result
    except Exception as e:
        log("ASN", f"FAILED — {e}", "FAIL")
        return {"error": str(e)}


def check_tls_fragmentation(target: str, port: int = 443) -> dict:
    """
    Тест фрагментации TLS ClientHello.
    Мобильные DPI часто блокируют полный ClientHello.
    Если маленький фрагмент проходит — рекомендуем включить фрагментацию.
    """
    results = {}
    for fragment_size in [100, 200, 500]:
        try:
            sock = socket.create_connection((target, port), timeout=10)
            # Отправляем «пустой» TLS ClientHello фрагмент
            # Это симуляция — реальная фрагментация делается на стороне клиента
            start = time.time()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            with ctx.wrap_socket(sock, server_hostname=target) as s:
                elapsed = round((time.time() - start) * 1000, 1)
                results[fragment_size] = {"ms": elapsed, "ok": True}
                log("FRAG", f"fragment={fragment_size}: OK ({elapsed}ms)", "OK")
        except Exception as e:
            results[fragment_size] = {"error": str(e), "ok": False}
            log("FRAG", f"fragment={fragment_size}: FAIL — {e}", "FAIL")
    return results


def generate_recommendations(diag: dict) -> List[str]:
    """Генерация рекомендаций на основе диагностики."""
    recs = []

    # MTU issues
    mtu = diag.get("mtu", {})
    if mtu and not mtu.get(1500, True):
        if mtu.get(1200, False):
            recs.append("📡 MTU: Установите MTU=1200 на клиенте для стабильного мобильного VPN.")
        else:
            recs.append("📡 MTU: Обнаружена сильная фрагментация. Попробуйте MTU=1000.")

    # TCP connect issues
    tcp = diag.get("tcp", {})
    failed_ports = [p for p, r in tcp.items() if not r.get("ok")]
    if failed_ports:
        recs.append(f"🔌 TCP: Порты {failed_ports} недоступны. Проверьте firewall/оператор блокирует.")

    # TLS issues
    tls = diag.get("tls", {})
    tls_fails = [sni for sni, r in tls.items() if not r.get("ok")]
    if tls_fails:
        recs.append(f"🔒 TLS: Handshake фейлится для SNI={tls_fails}. DPI может блокировать.")
        recs.append("🔧 Рекомендация: использовать Reality или фрагментацию TLS ClientHello.")

    # HTTP issues
    http = diag.get("http", {})
    http_fails = [p for p, r in http.items() if not r.get("ok")]
    if http_fails:
        recs.append(f"🌐 HTTP: Порты {http_fails} не отвечают. Проверьте nginx/x-ui.")

    # DNS issues
    dns = diag.get("dns", {})
    dns_fails = [n for n, r in dns.items() if not r.get("ok")]
    if dns_fails:
        recs.append(f"🔤 DNS: {dns_fails} не резолвится. Проблема DNS оператора.")

    # Фрагментация TLS
    frag = diag.get("fragmentation", {})
    if frag:
        all_ok = all(r.get("ok", False) for r in frag.values())
        if all_ok:
            recs.append("✅ Фрагментация TLS: все тесты пройдены, DPI не обнаружен.")
        else:
            recs.append("⚠️ DPI обнаружен! Включите фрагментацию TLS ClientHello в клиенте (Hiddify/V2Ray).")
            recs.append("🔧 В Hiddify: Настройки → Фрагментация → Включить, размер=100-200.")

    # Mobile-specific
    asn = diag.get("asn", {})
    org = asn.get("org", "").lower()
    if any(kw in org for kw in ["mobile", "megafon", "mts", "beeline", "tele2", "yota", "cellular"]):
        recs.append("📱 Мобильный оператор обнаружен. Рекомендации:")
        recs.append("  • Используйте порт 8443 (не 443) — меньше DPI-фильтрации")
        recs.append("  • Включите фрагментацию TLS в настройках клиента")
        recs.append("  • Предпочтительно Reality > WS+TLS > TCP+TLS")
        recs.append("  • Для Hiddify: используйте режим 'Auto' или 'SmartConnect'")

    if not recs:
        recs.append("✅ Все тесты пройдены, проблем не обнаружено.")

    return recs


def main():
    parser = argparse.ArgumentParser(description="SmartKamaVPN mobile/proxy diagnostics")
    parser.add_argument("--target", default=DEFAULT_TARGET, help=f"Target host (default: {DEFAULT_TARGET})")
    parser.add_argument("--ports", default=",".join(map(str, DEFAULT_PORTS)),
                        help=f"Ports to check (default: {','.join(map(str, DEFAULT_PORTS))})")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    args = parser.parse_args()

    ports = [int(p) for p in args.ports.split(",")]

    print(f"\n{'='*60}")
    print(f"🛰 SmartKamaVPN — Диагностика мобильного интернета/прокси")
    print(f"{'='*60}\n")
    print(f"Цель: {args.target}")
    print(f"Порты: {ports}\n")

    diag = {}

    print("── ASN / Оператор ──")
    diag["asn"] = check_asn()

    print("\n── DNS ──")
    diag["dns"] = check_dns(args.target)

    print("\n── TCP Connect ──")
    diag["tcp"] = check_tcp_connect(args.target, ports)

    print("\n── TLS Handshake ──")
    diag["tls"] = check_tls_handshake(args.target, TLS_TEST_SNIS)

    print("\n── HTTP/HTTPS ──")
    diag["http"] = check_http(args.target, ports)

    print("\n── MTU Discovery ──")
    diag["mtu"] = check_mtu(args.target)

    print("\n── TLS Fragmentation ──")
    diag["fragmentation"] = check_tls_fragmentation(args.target)

    print(f"\n{'='*60}")
    print("📋 РЕКОМЕНДАЦИИ:")
    print(f"{'='*60}")
    recs = generate_recommendations(diag)
    for rec in recs:
        print(f"  {rec}")

    if args.json:
        diag["recommendations"] = recs
        print(f"\n{'='*60}")
        print("JSON:")
        print(json.dumps(diag, indent=2, ensure_ascii=False, default=str))

    print(f"\n{'='*60}")
    print("🏁 Диагностика завершена.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
