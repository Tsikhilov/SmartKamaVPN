#!/usr/bin/env python3
"""
Fix sing-box Hy2 + TUIC5 configs: add proper users so clients can authenticate.
Run on server: python3 /tmp/fix_singbox_users.py
"""
import json
import subprocess
import sys
import uuid

HY2_CONFIG = "/opt/singbox/hy2-server.json"
TUIC_CONFIG = "/opt/singbox/tuic5-server.json"

# Credentials (shared secret — used in client URI too)
HY2_USER_NAME = "smartkama"
HY2_USER_PASS = "SmKm_Hy2_2026_Pass!"

TUIC_UUID = str(uuid.uuid4())
TUIC_USER_PASS = "SmKm_Tuic_2026_Pass!"

SERVER_IP = "72.56.100.45"
SERVER_SNI = "sub.smartkama.ru"

HY2_OBFS_PASS = "SmKm_Obs2026_s@l4m4nd3r!"
HY2_PORT = 8443
TUIC_PORT = 9445


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved {path}")


def fix_hy2():
    print("\n=== Fixing Hysteria2 config ===")
    cfg = load_json(HY2_CONFIG)
    inbound = cfg["inbounds"][0]
    inbound["users"] = [{"name": HY2_USER_NAME, "password": HY2_USER_PASS}]
    save_json(HY2_CONFIG, cfg)
    print(f"  User: {HY2_USER_NAME} / {HY2_USER_PASS}")


def fix_tuic():
    print("\n=== Fixing TUIC5 config ===")
    cfg = load_json(TUIC_CONFIG)
    inbound = cfg["inbounds"][0]
    inbound["users"] = [{"uuid": TUIC_UUID, "password": TUIC_USER_PASS}]
    save_json(TUIC_CONFIG, cfg)
    print(f"  UUID: {TUIC_UUID}")
    print(f"  Pass: {TUIC_USER_PASS}")


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print(f"  $ {cmd}")
    if r.stdout.strip():
        print(f"    {r.stdout.strip()}")
    if r.returncode != 0 and r.stderr.strip():
        print(f"  ERR: {r.stderr.strip()[:300]}", file=sys.stderr)
    return r.returncode


def validate(config_path):
    r = subprocess.run(
        ["/usr/local/bin/sing-box", "check", "-c", config_path],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        print(f"  OK: {config_path} — validation passed")
    else:
        print(f"  FAIL: {config_path}\n  {r.stdout}\n  {r.stderr}", file=sys.stderr)
        return False
    return True


def restart_services():
    print("\n=== Restarting sing-box services ===")
    for svc in ("singbox-hy2", "singbox-tuic5"):
        run(f"systemctl restart {svc}")
        run(f"systemctl is-active {svc}")


def show_client_uris():
    print("\n=== Client connection URIs ===")

    hy2_uri = (
        f"hysteria2://{HY2_USER_PASS}@{SERVER_IP}:{HY2_PORT}"
        f"?obfs=salamander&obfs-password={HY2_OBFS_PASS}"
        f"&sni={SERVER_SNI}&insecure=0"
        f"#SmartKama-Hy2-NL"
    )
    print(f"\n[Hysteria2]\n{hy2_uri}")

    tuic_uri = (
        f"tuic://{TUIC_UUID}:{TUIC_USER_PASS}@{SERVER_IP}:{TUIC_PORT}"
        f"?congestion_control=bbr&udp_relay_mode=native"
        f"&alpn=h3&sni={SERVER_SNI}&allow_insecure=0"
        f"#SmartKama-TUIC5-NL"
    )
    print(f"\n[TUIC5]\n{tuic_uri}")

    # Save to a file for easy retrieval
    out = "/tmp/singbox_client_uris.txt"
    with open(out, "w") as f:
        f.write(f"# Hysteria2\n{hy2_uri}\n\n# TUIC5\n{tuic_uri}\n")
    print(f"\nAlso saved to {out}")


def check_ports():
    print("\n=== Port / process status ===")
    run("ss -ulpn | grep -E '8443|9445'")
    run("systemctl status singbox-hy2 --no-pager -l | tail -6")
    run("systemctl status singbox-tuic5 --no-pager -l | tail -6")


def main():
    fix_hy2()
    fix_tuic()

    print("\n=== Validating configs ===")
    ok = validate(HY2_CONFIG) and validate(TUIC_CONFIG)
    if not ok:
        print("Config validation failed — aborting restart", file=sys.stderr)
        sys.exit(1)

    restart_services()
    check_ports()
    show_client_uris()
    print("\nDone. Copy URIs above to v2rayNG / Streisand / etc.")


if __name__ == "__main__":
    main()
