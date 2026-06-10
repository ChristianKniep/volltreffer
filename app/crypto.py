"""Symmetric encryption for credentials at rest.

Provider credentials (bearer tokens, etc.) are stored encrypted in the DB so a
leaked database file alone does not expose anyone's betting-site account. The
key is derived from the ``APP_SECRET_KEY`` env var — set a long random value in
production. If it is unset we fall back to a fixed dev key and warn, so the app
still boots locally, but credentials encrypted under the dev key are not secret.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

from cryptography.fernet import Fernet, InvalidToken

_DEV_KEY = "dev-insecure-key-change-me"


def _fernet() -> Fernet:
    secret = os.environ.get("APP_SECRET_KEY")
    if not secret:
        secret = _DEV_KEY
        if not getattr(_fernet, "_warned", False):
            print("WARNING: APP_SECRET_KEY not set — using an insecure dev key. "
                  "Stored credentials are NOT secure.", file=sys.stderr)
            _fernet._warned = True  # type: ignore[attr-defined]
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_dict(data: dict) -> bytes:
    return _fernet().encrypt(json.dumps(data).encode("utf-8"))


def decrypt_dict(blob: bytes | None) -> dict:
    if not blob:
        return {}
    try:
        return json.loads(_fernet().decrypt(bytes(blob)).decode("utf-8"))
    except (InvalidToken, ValueError):
        # Wrong/rotated key or corrupt blob — treat as "no credentials".
        return {}
