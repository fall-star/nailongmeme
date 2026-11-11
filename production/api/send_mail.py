"""Server-side CLI for approval notifications invoked from the local admin app."""

from __future__ import annotations

import json
import sys

from .mailer import approval_email


def main() -> int:
    payload = json.load(sys.stdin)
    approval_email(
        str(payload["email"]),
        str(payload["nickname"]),
        str(payload["title"]),
        str(payload["body"]),
        str(payload.get("site_url", "https://nailong.fall-star.top")),
    )
    print("sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
