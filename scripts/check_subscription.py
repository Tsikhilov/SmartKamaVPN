#!/usr/bin/env python3
"""
Check subscription content and prepare inbound remark rename plan.
"""
import json
import base64
import subprocess
import sys


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout.strip()


def get_token():
    out = run(
        'curl -s http://127.0.0.1:8000/api/admin/token '
        '-d username=Tsikhilovk -d password=Haker05dag\$'
    )
    try:
        return json.loads(out)["access_token"]
    except Exception as e:
        print("Auth error:", e, out, file=sys.stderr)
        sys.exit(1)


def get_sub_content(token, sub_path):
    # Try 2096 port (public)
    out = run(f'curl -s "http://127.0.0.1:2096/sub/{sub_path}"')
    if out:
        return out
    # Try 8000 API port
    out = run(f'curl -s -H "Authorization: Bearer {token}" "http://127.0.0.1:8000/sub/{sub_path}"')
    return out


def decode_sub(content):
    """Decode base64 subscription."""
    try:
        decoded = base64.b64decode(content + "==").decode("utf-8", errors="replace")
        return decoded
    except Exception:
        return content


def show_node_names(sub_content):
    """Extract and show ps/remark fields from vmess/vless/trojan/ss configs."""
    lines = sub_content.strip().split("\n")
    names = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("vmess://"):
            try:
                inner = base64.b64decode(line[8:] + "==").decode("utf-8", errors="replace")
                d = json.loads(inner)
                names.append(f"vmess: {d.get('ps', d.get('add', '?'))}")
            except Exception:
                names.append(f"vmess: [parse error] {line[:60]}")
        elif "://" in line:
            # vless, trojan, hy2, tuic — remark is after #
            proto = line[:line.index("://")]
            remark = line.split("#")[-1] if "#" in line else "?"
            names.append(f"{proto}: {remark}")
    return names


def main():
    token = get_token()
    print(f"Token OK: {token[:20]}...")

    # Get all users to find sub tokens
    users_raw = run(f'curl -s -H "Authorization: Bearer {token}" http://127.0.0.1:8000/api/users')
    users_data = json.loads(users_raw)
    users = users_data.get("users", [])
    print(f"\nUsers found: {len(users)}")

    for user in users[:3]:  # Check first 3 users
        sub_url = user.get("subscription_url", "")
        username = user["username"]
        # Extract sub token from url like http://...../sub/TOKEN
        if "/sub/" in sub_url:
            sub_token = sub_url.split("/sub/")[-1]
        else:
            continue

        print(f"\n--- User: {username} ---")
        print(f"Sub URL: {sub_url}")

        content = get_sub_content(token, sub_token)
        if not content:
            print("  [empty subscription]")
            continue

        decoded = decode_sub(content)
        names = show_node_names(decoded)
        if names:
            print("  Node names in subscription:")
            for n in names:
                print(f"    {n}")
        else:
            # Show raw first 200 chars
            print(f"  Raw sub (first 200): {decoded[:200]}")

    print("\n=== Current xray inbound tags ===")
    cfg = json.load(open("/var/lib/marzban/xray_config.json"))
    for ib in cfg["inbounds"]:
        print(f"  tag={ib['tag']} port={ib['port']} proto={ib.get('protocol','?')}")


if __name__ == "__main__":
    main()
