#!/usr/bin/env python3
"""Patch Marzban core.py to support new Xray v26+ x25519 output format.

Old: 'Private key: ...\nPublic key: ...'
New: 'PrivateKey: ...\nPassword (PublicKey): ...\nHash32: ...'
"""
import sys

PATH = "/code/app/xray/core.py"

with open(PATH) as f:
    lines = f.readlines()

# Find and fix the regex line (may be split across 2 lines from previous bad patch)
patched = False
new_lines = []
skip_next = False
for i, line in enumerate(lines):
    if skip_next:
        skip_next = False
        continue
    stripped = line.rstrip()
    # Match the broken multi-line version
    if "re.match(r'" in stripped and "Private key" in stripped and stripped.endswith("(.+)"):
        # This is the first half of a broken split - join with next line
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        new_line = "        m = re.match(r'(?:Private key|PrivateKey): (.+)\\n(?:Public key|Password \\(PublicKey\\)): (.+)', output)\n"
        new_lines.append(new_line)
        skip_next = True
        patched = True
    elif "re.match(r'Private key:" in stripped:
        # Original single-line version
        new_line = "        m = re.match(r'(?:Private key|PrivateKey): (.+)\\n(?:Public key|Password \\(PublicKey\\)): (.+)', output)\n"
        new_lines.append(new_line)
        patched = True
    else:
        new_lines.append(line)

if patched:
    with open(PATH, "w") as f:
        f.writelines(new_lines)
    print("PATCHED_OK")
else:
    print("PATTERN_NOT_FOUND")
    sys.exit(1)
