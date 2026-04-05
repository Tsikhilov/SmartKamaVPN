#!/usr/bin/env python3
"""Configure fail2ban jails for SmartKamaVPN and ensure it is running."""
import subprocess, os, sys

JAIL_CONF = """\
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
"""

FILTER_CONF = """\
[Definition]
failregex = ^.*"(POST|GET) .* HTTP.*" (401|403).*$
ignoreregex =
"""

os.makedirs("/etc/fail2ban/jail.d", exist_ok=True)
os.makedirs("/etc/fail2ban/filter.d", exist_ok=True)

with open("/etc/fail2ban/jail.d/smartkama.conf", "w") as f:
    f.write(JAIL_CONF)

with open("/etc/fail2ban/filter.d/marzban-auth.conf", "w") as f:
    f.write(FILTER_CONF)

print("fail2ban jail config written")

r = subprocess.run(["systemctl", "enable", "fail2ban"], capture_output=True, text=True)
r = subprocess.run(["systemctl", "restart", "fail2ban"], capture_output=True, text=True)
if r.returncode != 0:
    print("restart stderr:", r.stderr.strip())
    sys.exit(1)

import time; time.sleep(2)
r = subprocess.run(["systemctl", "is-active", "fail2ban"], capture_output=True, text=True)
status = r.stdout.strip()
print("fail2ban status:", status)
if status != "active":
    r2 = subprocess.run(["journalctl", "-u", "fail2ban", "-n", "10", "--no-pager"], capture_output=True, text=True)
    print(r2.stdout)
    sys.exit(1)

r = subprocess.run(["fail2ban-client", "status"], capture_output=True, text=True)
print(r.stdout.strip())
print("fail2ban configured OK")
