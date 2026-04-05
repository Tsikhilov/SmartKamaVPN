#!/bin/bash
set -e
cd /opt/SmartKamaVPN
export MARZBAN_PANEL_URL=http://127.0.0.1:8000
export MARZBAN_USERNAME=Tsikhilovk
export MARZBAN_PASSWORD='Haker05dag$'
timeout 60 .venv/bin/python scripts/server_ops_guard.py --mode all 2>&1 || true
echo "EXIT_CODE=$?"
