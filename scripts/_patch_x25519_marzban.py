#!/usr/bin/env python3
"""Patch Marzban core.py to handle new Xray x25519 output format."""

SRC = "/var/lib/marzban/core.py.patched"

with open(SRC) as f:
    content = f.read()

# Old regex that matches only old Xray format
old_pattern = "r'Private key: (.+)\\nPublic key: (.+)'"

# New regex that matches both old and new Xray output format
new_pattern = "r'Private\\s*[Kk]ey:\\s*(.+)\\n(?:Public\\s*[Kk]ey|Password\\s*\\(PublicKey\\)):\\s*(.+)'"

if old_pattern in content:
    content = content.replace(old_pattern, new_pattern)
    with open(SRC, 'w') as f:
        f.write(content)
    print("PATCHED successfully")
else:
    print("Pattern not found - maybe already patched?")
    for i, line in enumerate(content.split('\n'), 1):
        if 'Private' in line and 'match' in line:
            print(f"  Line {i}: {line.strip()}")
