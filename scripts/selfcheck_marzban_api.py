#!/usr/bin/env python3
"""Marzban API self-check.

Checks:
1) Marzban config availability,
2) auth + status endpoint,
3) users list read in compatibility adapter.

Optional write smoke (disabled by default):
- set MARZBAN_SELFCHECK_WRITE=1 to run create -> read -> update -> reset -> delete.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Utils import marzban_api  # noqa: E402


def main() -> int:
    if not marzban_api.is_enabled():
        print("MARZBAN_SELF_CHECK_FAILED: MARZBAN_PANEL_URL is empty")
        return 1

    status = marzban_api.get_panel_status()
    if not isinstance(status, dict):
        print("MARZBAN_SELF_CHECK_FAILED: status endpoint unavailable")
        return 1

    users = marzban_api.select() or []

    write_enabled = str(os.environ.get("MARZBAN_SELFCHECK_WRITE", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    write_result = "skipped"

    if write_enabled:
        created_uuid: str | None = None
        try:
            created_uuid = marzban_api.insert(
                name="selfcheck",
                usage_limit_GB=1,
                package_days=1,
                comment="selfcheck",
                mode="no_reset",
            )
            if not created_uuid:
                print("MARZBAN_SELF_CHECK_FAILED: create test user failed")
                return 1

            found = marzban_api.find(uuid=created_uuid)
            if not isinstance(found, dict):
                print("MARZBAN_SELF_CHECK_FAILED: read created user failed")
                return 1

            updated = marzban_api.update(uuid=created_uuid, usage_limit_GB=2, comment="selfcheck-updated")
            if not updated:
                print("MARZBAN_SELF_CHECK_FAILED: update created user failed")
                return 1

            reset = marzban_api.reset_user_usage(uuid=created_uuid)
            if not reset:
                print("MARZBAN_SELF_CHECK_FAILED: reset created user usage failed")
                return 1

            deleted = marzban_api.delete(uuid=created_uuid)
            if not deleted:
                print("MARZBAN_SELF_CHECK_FAILED: delete created user failed")
                return 1

            created_uuid = None
            write_result = "ok"
        finally:
            if created_uuid:
                marzban_api.delete(uuid=created_uuid)

    print("MARZBAN_SELF_CHECK_OK")
    print(f"provider={marzban_api.provider_name()}")
    print(f"users_count={len(users)}")
    print(f"write_smoke={write_result}")
    print("status_sample=" + json.dumps(status, ensure_ascii=True)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
