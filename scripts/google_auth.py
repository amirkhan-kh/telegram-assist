"""Obtain a Google OAuth refresh token for Calendar/Meet — run locally, once.

Prerequisite: a Desktop-app OAuth client downloaded from
https://console.cloud.google.com (APIs & Services -> Credentials -> Create
credentials -> OAuth client ID -> Desktop app -> Download JSON). Save it next to
this repo as ``credentials.json`` (or pass the path as an argument).

Usage::

    python -m scripts.google_auth                  # uses ./credentials.json
    python -m scripts.google_auth path/to/creds.json

A browser window opens for consent. On success the script writes
``GOOGLE_CLIENT_ID`` / ``GOOGLE_CLIENT_SECRET`` / ``GOOGLE_OAUTH_REFRESH_TOKEN``
straight into ``.env`` (and prints them as a fallback).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.integrations.google.oauth import SCOPES


def _update_env(values: dict[str, str], env_path: str = ".env") -> bool:
    """Set each ``KEY=value`` in ``.env`` (update in place, else append).

    Returns ``True`` when ``.env`` exists and was written, ``False`` otherwise.
    """
    path = Path(env_path)
    if not path.exists():
        return False
    pending = dict(values)
    lines = path.read_text().splitlines()
    out: list[str] = []
    for line in lines:
        match = re.match(r"^([A-Z_][A-Z0-9_]*)=", line)
        key = match.group(1) if match else None
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    for key, value in pending.items():  # keys not already present
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    return True


def main(creds_path: str = "credentials.json") -> None:
    path = Path(creds_path)
    if not path.is_file():
        raise SystemExit(
            f"'{creds_path}' topilmadi. Google Cloud Console'dan 'Desktop app' "
            "OAuth client JSON'ini yuklab, shu nom bilan saqlang "
            "(yoki yo'lini argument qilib bering)."
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:  # pragma: no cover - dependency is in requirements
        raise SystemExit(
            "google-auth-oauthlib o'rnatilmagan. `pip install -r requirements.txt`."
        ) from None

    flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=SCOPES)
    # access_type=offline + prompt=consent guarantees a refresh token is issued.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    info = json.loads(path.read_text())
    client = info.get("installed") or info.get("web") or {}
    refresh_token = getattr(creds, "refresh_token", None)
    if not refresh_token:
        raise SystemExit(
            "Refresh token qaytmadi. Google hisobida ilovaga ruxsatni olib "
            "tashlab, qaytadan urinib ko'ring."
        )

    values = {
        "GOOGLE_CLIENT_ID": client.get("client_id", ""),
        "GOOGLE_CLIENT_SECRET": client.get("client_secret", ""),
        "GOOGLE_OAUTH_REFRESH_TOKEN": refresh_token,
    }

    print("\n" + "=" * 60)
    print("Google ruxsati olindi.")
    print("=" * 60)
    if _update_env(values):
        print("\n✅ .env fayli yangilandi (Google sozlamalari yozildi).")
        print("Endi botni qayta ishga tushiring: python -m app.main")
    else:
        print("\n.env topilmadi — quyidagilarni qo'lda joylang:\n")
        for key, value in values.items():
            print(f"{key}={value}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "credentials.json")
