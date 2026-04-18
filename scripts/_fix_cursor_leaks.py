"""
Add `finally: cur.close()` to every method in dbManager.py that opens a cursor
but never closes it.

Two patterns:
  Pattern A – cur = ... then try/except at same indent → insert finally after the except block
  Pattern B – cur = ... then body WITHOUT an outer try at cursor indent → wrap body in try/finally
"""
import re, textwrap, sys

DB_PATH = "Database/dbManager.py"

with open(DB_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()

out = []
i = 0
fixes = 0

while i < len(lines):
    line = lines[i]

    # Detect `cur = self.conn.cursor()`
    m = re.match(r'^(\s+)(cur = self\.conn\.cursor\(\))\s*$', line)
    if not m:
        out.append(line)
        i += 1
        continue

    indent = m.group(1)  # e.g. "        " (8 spaces or 2 tabs)
    indent_len = len(indent)

    # Check if there's already a `finally:` with `cur.close()` in this method
    # Scan forward until we hit the next method def or end of class
    has_finally_close = False
    method_end = len(lines)
    for j in range(i + 1, len(lines)):
        lj = lines[j]
        lj_stripped = lj.lstrip()
        # If we hit a new method/class at same or lower indent, stop
        if lj_stripped and not lj_stripped.startswith('#'):
            lj_indent = len(lj) - len(lj.lstrip())
            if lj_indent < indent_len and lj_stripped.startswith(('def ', 'class ', 'try:', 'USERS_DB')):
                method_end = j
                break
            if lj_indent == indent_len and lj_stripped.startswith('def '):
                method_end = j
                break
        if re.match(r'^\s*finally:\s*$', lj):
            # Check next line for cur.close()
            if j + 1 < len(lines) and 'cur.close()' in lines[j + 1]:
                has_finally_close = True
                break

    if has_finally_close:
        # Already fixed, skip
        out.append(line)
        i += 1
        continue

    # Now determine the pattern
    # Look at next non-empty, non-comment line after cursor
    next_code_idx = None
    for j in range(i + 1, method_end):
        lj = lines[j].strip()
        if lj and not lj.startswith('#'):
            next_code_idx = j
            break

    if next_code_idx is None:
        out.append(line)
        i += 1
        continue

    next_code = lines[next_code_idx].strip()
    next_code_indent = len(lines[next_code_idx]) - len(lines[next_code_idx].lstrip())

    # Pattern A: cur = ... then try: at same indent
    if next_code == 'try:' and next_code_indent == indent_len:
        # Find the except block at this indent level
        out.append(line)
        i += 1

        # Find end of the try/except block
        except_end = None
        in_try = False
        for j in range(i, method_end):
            lj = lines[j]
            lj_stripped = lj.strip()
            lj_indent = len(lj) - len(lj.lstrip()) if lj_stripped else 999

            if lj_stripped == 'try:' and lj_indent == indent_len:
                in_try = True

            # Look for last line of except block at indent_len + 4 or return
            if in_try and lj_indent == indent_len and lj_stripped.startswith('except'):
                # Found except, now find end of except body
                for k in range(j + 1, method_end):
                    lk = lines[k]
                    lk_stripped = lk.strip()
                    lk_indent = len(lk) - len(lk.lstrip()) if lk_stripped else 999

                    # If we find a line at or before cursor indent that's not part of except body
                    if lk_stripped and lk_indent <= indent_len:
                        except_end = k
                        break
                if except_end is None:
                    except_end = method_end
                break

        if except_end is not None:
            # Copy lines from i to except_end, then add finally
            for j in range(i, except_end):
                out.append(lines[j])
            out.append(f"{indent}finally:\n")
            out.append(f"{indent}    cur.close()\n")
            i = except_end
            fixes += 1
        else:
            # Couldn't find except, just copy
            continue

    # Pattern B: cur = ... then NOT try: (e.g., for loop or direct code)
    elif next_code_indent == indent_len:
        # Wrap everything from next line until method end in try/finally
        out.append(line)
        out.append(f"{indent}try:\n")
        i += 1

        # Find the actual end of this method body (where indent drops back)
        body_end = method_end
        for j in range(i, method_end):
            lj = lines[j]
            lj_stripped = lj.strip()
            if not lj_stripped:
                continue
            lj_indent = len(lj) - len(lj.lstrip())
            if lj_indent < indent_len:
                body_end = j
                break

        # Re-indent all body lines by 4 spaces
        for j in range(i, body_end):
            lj = lines[j]
            if lj.strip():  # non-empty
                out.append(f"    {lj}")
            else:
                out.append(lj)  # keep blank lines as-is

        out.append(f"{indent}finally:\n")
        out.append(f"{indent}    cur.close()\n")
        i = body_end
        fixes += 1
    else:
        out.append(line)
        i += 1

with open(DB_PATH, "w", encoding="utf-8") as f:
    f.writelines(out)

print(f"Applied {fixes} cursor-close fixes")
