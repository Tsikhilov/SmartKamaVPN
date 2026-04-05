#!/bin/bash
# Certbot deploy-hook: copy renewed certs to Marzban and reload services.
# Installed at /etc/letsencrypt/renewal-hooks/deploy/marzban-certs.sh

DOMAIN="sub.smartkama.ru"
DEST="/var/lib/marzban/certs/$DOMAIN"
SRC="/etc/letsencrypt/live/$DOMAIN"

if [ ! -d "$SRC" ]; then
    echo "[marzban-certs] source $SRC not found, skipping"
    exit 0
fi

mkdir -p "$DEST"
cp -f "$SRC/fullchain.pem" "$DEST/fullchain.pem"
cp -f "$SRC/privkey.pem"   "$DEST/privkey.pem"
chmod 644 "$DEST/fullchain.pem"
chmod 600 "$DEST/privkey.pem"

echo "[marzban-certs] certs copied to $DEST"

# Restart Marzban to pick up new certs
cd /opt/marzban && docker compose restart
echo "[marzban-certs] marzban restarted"

# Reload nginx (sub proxy uses the same certs)
nginx -t && systemctl reload nginx
echo "[marzban-certs] nginx reloaded"
