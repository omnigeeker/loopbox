"""Bearer-token authentication for the loopbox HTTP service.

On first use a random token is generated and persisted to
``<LOOPBOX_HOME>/auth.json`` (default ``~/.loopbox/auth.json``) with
owner-only file permissions (0600). Clients authenticate exactly like they
would against E2B: send the token in the ``X-API-Key`` header. An
``Authorization: Bearer <token>`` header is accepted as an alias.

Set ``LOOPBOX_NO_AUTH=1`` to bypass authentication entirely (useful for
local development and tests).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Any

from loopbox.store import home

ENV_NO_AUTH = "LOOPBOX_NO_AUTH"
AUTH_FILE = "auth.json"
TOKEN_PREFIX = "lbx_"


def auth_disabled() -> bool:
    """Return True when ``LOOPBOX_NO_AUTH=1`` disables authentication."""
    return os.environ.get(ENV_NO_AUTH) == "1"


def token_path() -> Path:
    """Return the path of the persisted auth token."""
    return home() / AUTH_FILE


def get_or_create_token() -> str:
    """Return the local bearer token, generating and persisting it once.

    A corrupt or empty token file is transparently replaced with a fresh
    token rather than raising.
    """
    path = token_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            token = data.get("token")
            if isinstance(token, str) and token:
                return token
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    _write_token_file(path, token)
    return token


def is_authorized(headers: Any) -> bool:
    """Check request headers against the local bearer token.

    ``headers`` is any mapping-like object with a ``.get`` method, such as
    the ``http.client.HTTPMessage`` carried by a request handler. Always
    returns True when :func:`auth_disabled` is true.
    """
    if auth_disabled():
        return True
    provided = headers.get("X-API-Key")
    if not provided:
        provided = _bearer_token(headers.get("Authorization"))
    if not provided:
        return False
    return hmac.compare_digest(provided, get_or_create_token())


def _bearer_token(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def _write_token_file(path: Path, token: str) -> None:
    """Atomically persist ``token`` with owner-only (0600) permissions."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".auth-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "token": token}, fh, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
