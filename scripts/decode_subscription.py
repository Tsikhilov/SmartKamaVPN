#!/usr/bin/env python3
"""
Decode and display Marzban subscription content to see current node names.
"""
import json
import base64
import gzip
import subprocess
import sys
import urllib.parse


def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True)
    return r.stdout


def get_token():
    out = subprocess.run(
        'curl -s http://127.0.0.1:8000/api/admin/token '
        '-d username=Tsikhilovk -d password=Haker05dag$',
        shell=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)["access_token"]


def fetch_sub(sub_token, fmt="v2ray"):
    """Fetch subscription in specific format."""
    r = subprocess.run(
        f'curl -s -H "User-Agent: v2rayNG/1.8.0" '
        f'"http://127.0.0.1:2096/sub/{sub_token}?client={fmt}"',
        shell=True, capture_output=True
    )
    return r.stdout


def decode_and_show(content: bytes, fmt: str):
    """Try to decode subscription content."""
    print(f"\n  Format: {fmt}, raw length: {len(content)} bytes")

    # Try gzip
    try:
        content = gzip.decompress(content)
        print("  (gzip-decompressed)")
    except Exception:
        pass

    text = content.decode("utf-8", errors="replace")

    if fmt == "v2ray":
        # Base64 encoded list of URIs
        try:
            decoded = base64.b64decode(text + "==").decode("utf-8", errors="replace")
        except Exception:
            decoded = text

        lines = decoded.strip().split("\n")
        print(f"  Lines: {len(lines)}")
        for line in lines[:20]:
            line = line.strip()
            if not line:
                continue
            if "#" in line:
                remark = urllib.parse.unquote(line.split("#")[-1])
                proto = line.split("://")[0] if "://" in line else "?"
                print(f"  [{proto}] remark: {remark}")
            elif line.startswith("vmess://"):
                try:
                    inner = base64.b64decode(line[8:] + "==").decode("utf-8", errors="replace")
                    d = json.loads(inner)
                    print(f"  [vmess] ps: {d.get('ps', '?')} add: {d.get('add', '?')}")
                except Exception as e:
                    print(f"  [vmess] parse error: {e}")
            else:
                print(f"  raw: {line[:100]}")
    elif fmt == "singbox":
        try:
            d = json.loads(text)
            for ib in d.get("outbounds", []):
                name = ib.get("tag", ib.get("name", "?"))
                typ = ib.get("type", "?")
                if typ not in ("direct", "dns", "block", "selector", "urltest"):
                    print(f"  [{typ}] tag/name: {name}")
        except Exception as e:
            print(f"  singbox parse error: {e}")
            print(f"  raw: {text[:300]}")
    else:
        print(f"  raw: {text[:400]}")


def main():
    token = get_token()
    print(f"Token: {token[:20]}...")

    # Get users
    r = subprocess.run(
        f'curl -s -H "Authorization: Bearer {token}" http://127.0.0.1:8000/api/users',
        shell=True, capture_output=True, text=True
    )
    users = json.loads(r.stdout).get("users", [])
    print(f"Users: {len(users)}")

    for user in users[:1]:  # Check first user only
        sub_url = user.get("subscription_url", "")
        if "/sub/" not in sub_url:
            continue
        sub_token = sub_url.split("/sub/")[-1]
        print(f"\n=== User: {user['username']} ===")
        print(f"Sub token: {sub_token[:40]}...")

        for fmt in ("v2ray", "singbox", "clash"):
            content = fetch_sub(sub_token, fmt)
            if content:
                decode_and_show(content, fmt)
            else:
                print(f"\n  [{fmt}] empty response")


if __name__ == "__main__":
    main()
