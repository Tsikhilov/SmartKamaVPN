#!/usr/bin/env bash
# ==============================================================================
# SmartKamaVPN — sing-box (Hysteria2 + TUIC v5) + UFW + fail2ban deploy
# Usage: bash /tmp/deploy_singbox.sh
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}==>${NC} $*"; }

CERT_DIR="/var/lib/marzban/certs/sub.smartkama.ru"
SINGBOX_DIR="/opt/singbox"
SINGBOX_BIN="/usr/local/bin/sing-box"

# ─── 1. install sing-box ───────────────────────────────────────────────────────
step "Installing sing-box..."
if command -v sing-box &>/dev/null; then
    SB_INSTALLED=$(sing-box version 2>/dev/null | head -1 || echo "unknown")
    ok "sing-box already installed: $SB_INSTALLED"
else
    LATEST=$(curl -sfL https://api.github.com/repos/SagerNet/sing-box/releases/latest \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo "")
    if [[ -z "$LATEST" ]]; then
        # fallback version if GitHub API is unreachable
        LATEST="v1.10.7"
        warn "GitHub API unreachable, using fallback version $LATEST"
    fi
    VER="${LATEST#v}"
    ARCH="amd64"
    URL="https://github.com/SagerNet/sing-box/releases/download/${LATEST}/sing-box-${VER}-linux-${ARCH}.tar.gz"
    ok "Downloading sing-box ${LATEST}..."
    cd /tmp
    curl -fsSL -o singbox.tar.gz "$URL"
    tar -xzf singbox.tar.gz
    mv "/tmp/sing-box-${VER}-linux-${ARCH}/sing-box" "$SINGBOX_BIN"
    chmod 755 "$SINGBOX_BIN"
    rm -rf singbox.tar.gz "/tmp/sing-box-${VER}-linux-${ARCH}"
    ok "sing-box ${LATEST} installed at $SINGBOX_BIN"
fi

mkdir -p "$SINGBOX_DIR"

# ─── 2. Hysteria2 config ───────────────────────────────────────────────────────
step "Writing Hysteria2 server config..."
cat > "${SINGBOX_DIR}/hy2-server.json" << 'EHJSON'
{
  "log": {
    "level": "info",
    "timestamp": true,
    "output": "/var/log/singbox-hy2.log"
  },
  "inbounds": [
    {
      "type": "hysteria2",
      "tag": "nl-hy2",
      "listen": "0.0.0.0",
      "listen_port": 8443,
      "sniff": true,
      "sniff_override_destination": false,
      "obfs": {
        "type": "salamander",
        "password": "SmKm_Obs2026_s@l4m4nd3r!"
      },
      "users": [],
      "ignore_client_bandwidth": false,
      "tls": {
        "enabled": true,
        "certificate_path": "/var/lib/marzban/certs/sub.smartkama.ru/fullchain.pem",
        "key_path": "/var/lib/marzban/certs/sub.smartkama.ru/privkey.pem",
        "alpn": ["h3"]
      }
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" },
    { "type": "block",  "tag": "block"  }
  ],
  "route": {
    "rules": [
      { "ip_is_private": true, "outbound": "block" }
    ],
    "final": "direct"
  }
}
EHJSON
ok "Hysteria2 config written"

# ─── 3. TUIC v5 config ────────────────────────────────────────────────────────
step "Writing TUIC v5 server config..."
cat > "${SINGBOX_DIR}/tuic5-server.json" << 'ETJSON'
{
  "log": {
    "level": "info",
    "timestamp": true,
    "output": "/var/log/singbox-tuic.log"
  },
  "inbounds": [
    {
      "type": "tuic",
      "tag": "nl-tuic5",
      "listen": "0.0.0.0",
      "listen_port": 9445,
      "sniff": true,
      "sniff_override_destination": false,
      "users": [],
      "congestion_control": "bbr",
      "auth_timeout": "3s",
      "zero_rtt_handshake": false,
      "heartbeat": "10s",
      "tls": {
        "enabled": true,
        "certificate_path": "/var/lib/marzban/certs/sub.smartkama.ru/fullchain.pem",
        "key_path": "/var/lib/marzban/certs/sub.smartkama.ru/privkey.pem",
        "alpn": ["h3"]
      }
    }
  ],
  "outbounds": [
    { "type": "direct", "tag": "direct" },
    { "type": "block",  "tag": "block"  }
  ],
  "route": {
    "rules": [
      { "ip_is_private": true, "outbound": "block" }
    ],
    "final": "direct"
  }
}
ETJSON
ok "TUIC v5 config written"

# ─── 4. Validate configs ──────────────────────────────────────────────────────
step "Validating configs..."
sing-box check -c "${SINGBOX_DIR}/hy2-server.json"  && ok "hy2-server.json valid"
sing-box check -c "${SINGBOX_DIR}/tuic5-server.json" && ok "tuic5-server.json valid"

# ─── 5. systemd units ─────────────────────────────────────────────────────────
step "Creating systemd units..."

cat > /etc/systemd/system/singbox-hy2.service << EHUNIT
[Unit]
Description=sing-box Hysteria2 (nl-hy2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${SINGBOX_BIN} run -c ${SINGBOX_DIR}/hy2-server.json
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576
LimitNPROC=65536
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EHUNIT

cat > /etc/systemd/system/singbox-tuic5.service << ETUNIT
[Unit]
Description=sing-box TUIC v5 (nl-tuic5)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${SINGBOX_BIN} run -c ${SINGBOX_DIR}/tuic5-server.json
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576
LimitNPROC=65536
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
ETUNIT

ok "systemd units written"

# ─── 6. Enable and start ──────────────────────────────────────────────────────
step "Enabling and starting services..."
systemctl daemon-reload
systemctl enable singbox-hy2 singbox-tuic5
systemctl restart singbox-hy2 singbox-tuic5
sleep 3
systemctl is-active singbox-hy2  && ok "singbox-hy2 ACTIVE"  || warn "singbox-hy2 NOT active"
systemctl is-active singbox-tuic5 && ok "singbox-tuic5 ACTIVE" || warn "singbox-tuic5 NOT active"

# ─── 7. UFW rules ─────────────────────────────────────────────────────────────
step "Configuring UFW firewall..."

# Allow all existing xray TCP ports
for PORT in 22 80 443 2053 9443 10443 11443 12443 15443 16443; do
    ufw allow "${PORT}/tcp" &>/dev/null && ok "TCP $PORT allowed"
done

# Allow new UDP protocols
ufw allow 8443/udp  && ok "UDP 8443 (Hysteria2) allowed"
ufw allow 9445/udp  && ok "UDP 9445  (TUIC v5) allowed"

# Rate-limit SSH
ufw limit 22/tcp &>/dev/null && ok "SSH rate-limit applied"

echo ""
ufw status verbose
ok "UFW configured"

# ─── 8. fail2ban ──────────────────────────────────────────────────────────────
step "Installing fail2ban..."
apt-get install -y -q fail2ban

cat > /etc/fail2ban/jail.d/smartkama.conf << 'EFJAIL'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 10

[sshd]
enabled  = true
port     = ssh
logpath  = %(sshd_log)s
backend  = systemd
maxretry = 5

[marzban-auth]
enabled   = true
port      = 443,444,8443,9445
filter    = marzban-auth
logpath   = /var/lib/marzban/logs/access.log
maxretry  = 20
bantime   = 1800
EFJAIL

# filter for marzban (generic HTTP 401/403 catcher)
cat > /etc/fail2ban/filter.d/marzban-auth.conf << 'EFFILTER'
[Definition]
failregex = ^.*"(POST|GET) .* HTTP.*" (401|403).*$
ignoreregex =
EFFILTER

systemctl enable fail2ban
systemctl restart fail2ban
sleep 2
systemctl is-active fail2ban && ok "fail2ban ACTIVE" || warn "fail2ban NOT active"

# ─── 9. Cert reload hook for sing-box ─────────────────────────────────────────
step "Adding cert-reload hook for sing-box..."
HOOK_FILE="/var/lib/marzban/certs/sub.smartkama.ru/reload-singbox.sh"
cat > "$HOOK_FILE" << 'EHHOOK'
#!/usr/bin/env bash
systemctl restart singbox-hy2 singbox-tuic5 || true
EHHOOK
chmod +x "$HOOK_FILE"

# Add to crontab if not already present
CRON_LINE="0 3 * * * /var/lib/marzban/certs/sub.smartkama.ru/reload-singbox.sh"
(crontab -l 2>/dev/null | grep -qF "reload-singbox" || (crontab -l 2>/dev/null; echo "$CRON_LINE")) | crontab -
ok "Cert reload hook registered in crontab"

# ─── 10. Final verification ───────────────────────────────────────────────────
step "Final verification..."
echo ""
echo "=== UDP listeners ==="
ss -ulnp | grep -E '8443|9445' || warn "No UDP listeners on 8443/9445"

echo ""
echo "=== sing-box Hy2 log (last 5 lines) ==="
journalctl -u singbox-hy2 --no-pager -n 5 2>/dev/null || tail -5 /var/log/singbox-hy2.log 2>/dev/null || echo "(no log yet)"

echo ""
echo "=== sing-box TUIC log (last 5 lines) ==="
journalctl -u singbox-tuic5 --no-pager -n 5 2>/dev/null || tail -5 /var/log/singbox-tuic.log 2>/dev/null || echo "(no log yet)"

echo ""
echo "=== UFW status ==="
ufw status numbered | head -30

echo ""
echo "=== fail2ban status ==="
fail2ban-client status 2>/dev/null

echo ""
ok "=== DEPLOY COMPLETE ==="
echo "  Hysteria2:  UDP 8443 | obfs=salamander | certs: ${CERT_DIR}"
echo "  TUIC v5:    UDP 9445 | BBR | certs: ${CERT_DIR}"
echo "  UFW:        all required ports open"
echo "  fail2ban:   sshd + marzban-auth jails active"
