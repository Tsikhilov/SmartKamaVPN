#!/usr/bin/env bash
# deploy_v2_configs.sh — Развёртывание 8 Marzban inbounds + sing-box Hy2/TUIC
# SmartKamaVPN — Нидерланды (multi-node ready)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MARZBAN_DIR="/opt/marzban"
MARZBAN_DATA="/var/lib/marzban"
SINGBOX_DIR="/opt/singbox"
CERT_DIR="$MARZBAN_DATA/certs/sub.smartkama.ru"

echo "=== SmartKamaVPN v2 Config Deployment ==="
echo "Server: $(hostname) ($(curl -s ifconfig.me))"
echo ""

# ---- 1. Backup current xray config ----
echo "[1/6] Backing up current xray_config.json..."
cp "$MARZBAN_DATA/xray_config.json" "$MARZBAN_DATA/xray_config.json.bak.$(date +%s)"
echo "  OK: backup created"

# ---- 2. Deploy new xray config (8 inbounds) ----
echo "[2/6] Deploying xray_config_v2.json..."
cp "$SCRIPT_DIR/xray_config_v2.json" "$MARZBAN_DATA/xray_config.json"
echo "  OK: 8 inbounds deployed"

# ---- 3. Open port 2053 (new XHTTP inbound) ----
echo "[3/6] Opening firewall port 2053 (XHTTP)..."
if command -v ufw >/dev/null 2>&1; then
    ufw allow 2053/tcp 2>/dev/null || true
    echo "  OK: ufw rule added"
elif command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p tcp --dport 2053 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p tcp --dport 2053 -j ACCEPT
    echo "  OK: iptables rule added"
else
    echo "  WARN: no firewall tool found, ensure port 2053 is open"
fi

# ---- 4. Restart Marzban ----
echo "[4/6] Restarting Marzban..."
marzban restart -n
sleep 8
docker ps --format '{{.Names}}|{{.Status}}' | grep marzban
echo "  OK: Marzban restarted"

# ---- 5. Deploy sing-box for Hysteria2 + TUIC ----
echo "[5/6] Setting up sing-box services..."
mkdir -p "$SINGBOX_DIR"

# Install sing-box if not present
if ! command -v sing-box >/dev/null 2>&1; then
    echo "  Installing sing-box..."
    SINGBOX_VER="1.11.0"
    ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
    curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VER}/sing-box-${SINGBOX_VER}-linux-${ARCH}.tar.gz" \
        -o /tmp/singbox.tar.gz
    tar xzf /tmp/singbox.tar.gz -C /tmp/
    install -m 755 "/tmp/sing-box-${SINGBOX_VER}-linux-${ARCH}/sing-box" /usr/local/bin/sing-box
    rm -rf /tmp/singbox.tar.gz "/tmp/sing-box-${SINGBOX_VER}-linux-${ARCH}"
    echo "  OK: sing-box ${SINGBOX_VER} installed"
fi

# Copy sing-box configs
cp "$SCRIPT_DIR/singbox_hysteria2.json" "$SINGBOX_DIR/hy2.json"
cp "$SCRIPT_DIR/singbox_tuic.json" "$SINGBOX_DIR/tuic.json"

# Create systemd units
cat > /etc/systemd/system/singbox-hy2.service <<'UNIT'
[Unit]
Description=sing-box Hysteria2 (SmartKamaVPN NL)
After=network.target marzban.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/sing-box run -c /opt/singbox/hy2.json
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/singbox-tuic.service <<'UNIT'
[Unit]
Description=sing-box TUIC v5 (SmartKamaVPN NL)
After=network.target marzban.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/sing-box run -c /opt/singbox/tuic.json
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
UNIT

# Open ports for Hy2 and TUIC (UDP!)
if command -v ufw >/dev/null 2>&1; then
    ufw allow 8444/udp 2>/dev/null || true
    ufw allow 8445/udp 2>/dev/null || true
elif command -v iptables >/dev/null 2>&1; then
    iptables -C INPUT -p udp --dport 8444 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p udp --dport 8444 -j ACCEPT
    iptables -C INPUT -p udp --dport 8445 -j ACCEPT 2>/dev/null || \
        iptables -I INPUT -p udp --dport 8445 -j ACCEPT
fi

systemctl daemon-reload
# НЕ включаем автоматически — пользователь должен настроить пароли!
echo "  OK: systemd units created (NOT started — edit passwords first!)"
echo "  WARN: Edit /opt/singbox/hy2.json — замените REPLACE_WITH_AUTH_PASSWORD"
echo "  WARN: Edit /opt/singbox/tuic.json — замените REPLACE_WITH_USER_UUID и REPLACE_WITH_AUTH_PASSWORD"
echo ""
echo "  After editing passwords, run:"
echo "    systemctl enable --now singbox-hy2"
echo "    systemctl enable --now singbox-tuic"

# ---- 6. Verify ----
echo "[6/6] Verification..."
echo "  Marzban ports:"
ss -tlnp | grep -E ':443 |:9443 |:10443 |:11443 |:12443 |:2053 |:15443 |:16443 ' || true
echo ""
echo "  Marzban container:"
docker ps --format '{{.Names}} {{.Status}}' | grep marzban || true
echo ""

echo "=== DEPLOYMENT COMPLETE ==="
echo ""
echo "Tag mapping (set in Marzban panel → Inbound Remarks):"
echo "  nl-direct-ws      → Нидерланды - Быстрый (Прямой)"
echo "  nl-reality-1      → Нидерланды - Белый список 1 (YT + IG + TG)"
echo "  nl-reality-2      → Нидерланды - Белый список 2 (расширенный)"
echo "  nl-reality-3      → Нидерланды - Полный туннель"
echo "  nl-reality-4      → Нидерланды - Универсальный LTE"
echo "  nl-stealth-xhttp  → Нидерланды - Максимальная маскировка"
echo "  nl-backup-grpc    → Нидерланды - Запасной TLS"
echo "  nl-backup-trojan  → Нидерланды - Запасной Trojan"
echo ""
echo "Update MARZBAN_INBOUND_TAGS in bot config:"
echo "  nl-direct-ws,nl-reality-1,nl-reality-2,nl-reality-3,nl-reality-4,nl-stealth-xhttp,nl-backup-grpc,nl-backup-trojan"
