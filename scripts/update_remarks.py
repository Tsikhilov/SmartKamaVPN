#!/usr/bin/env python3
"""
Update Marzban hosts table with Russian remark names.
Run: python3 /tmp/update_remarks.py
"""
import sqlite3
import subprocess

DB = "/var/lib/marzban/db.sqlite3"

# Map: inbound_tag → Russian remark name
REMARKS = {
    "nl-direct-ws":     "🇳🇱 Нидерланды - Быстрый (Прямой) ({USERNAME})",
    "nl-reality-1":     "🇳🇱 Нидерланды - Белый список 1 ({USERNAME})",
    "nl-reality-2":     "🇳🇱 Нидерланды - Белый список 2 ({USERNAME})",
    "nl-reality-3":     "🇳🇱 Нидерланды - Полный туннель ({USERNAME})",
    "nl-reality-4":     "🇳🇱 Нидерланды - Универсальный LTE ({USERNAME})",
    "nl-stealth-xhttp": "🇳🇱 Нидерланды - Макс. маскировка ({USERNAME})",
    "nl-backup-grpc":   "🇳🇱 Нидерланды - Запасной TLS ({USERNAME})",
    "nl-backup-trojan": "🇳🇱 Нидерланды - Запасной Trojan ({USERNAME})",
}

con = sqlite3.connect(DB)
cur = con.cursor()

print("=== Current hosts table ===")
rows = cur.execute("SELECT id, inbound_tag, remark FROM hosts ORDER BY id").fetchall()
for r in rows:
    print(f"  id={r[0]} tag={r[1]} remark={r[2][:50]}")

print("\n=== Updating remarks ===")
updated = 0
for tag, new_remark in REMARKS.items():
    result = cur.execute(
        "UPDATE hosts SET remark = ? WHERE inbound_tag = ?",
        (new_remark, tag)
    )
    if result.rowcount > 0:
        print(f"  OK: {tag} → {new_remark[:60]}")
        updated += result.rowcount
    else:
        print(f"  SKIP (no row): {tag}")

con.commit()
con.close()

print(f"\nUpdated {updated} rows.")

# Also disable old-style duplicate hosts (IDs 1-7 with VLESS_/TROJAN_ style tags)
print("\n=== Checking old-style tags ===")
con2 = sqlite3.connect(DB)
cur2 = con2.cursor()
old_rows = cur2.execute(
    "SELECT id, inbound_tag FROM hosts WHERE inbound_tag LIKE 'VLESS_%' OR inbound_tag LIKE 'TROJAN_%'"
).fetchall()
if old_rows:
    print(f"  Found {len(old_rows)} old-style hosts entries (disabling them)")
    for r in old_rows:
        print(f"    id={r[0]} tag={r[1]}")
    # Disable them (set is_disabled=1)
    cur2.execute(
        "UPDATE hosts SET is_disabled = 1 WHERE inbound_tag LIKE 'VLESS_%' OR inbound_tag LIKE 'TROJAN_%'"
    )
    con2.commit()
    print("  Disabled.")
else:
    print("  None found - clean.")
con2.close()

print("\n=== Final hosts table ===")
con3 = sqlite3.connect(DB)
rows_final = con3.execute(
    "SELECT id, inbound_tag, remark, is_disabled FROM hosts ORDER BY id"
).fetchall()
for r in rows_final:
    status = "[disabled]" if r[3] else "[active]"
    print(f"  id={r[0]} {status} tag={r[1]}")
    print(f"         remark: {r[2]}")
con3.close()

# Restart Marzban to pick up changes
print("\n=== Restarting Marzban ===")
r = subprocess.run("cd /opt/marzban && docker compose restart marzban", shell=True,
                   capture_output=True, text=True)
print("  stdout:", r.stdout.strip()[:200])
if r.returncode != 0 and r.stderr:
    print("  stderr:", r.stderr.strip()[:200])
print(f"  exit: {r.returncode}")

print("\nDone! Check subscription now.")
