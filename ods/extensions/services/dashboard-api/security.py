"""API key authentication for ODS Dashboard API."""

import logging
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

# The dashboard container's entrypoint reads this file to inject the key into
# its nginx config, so the path is a contract between the two services.
# Overridable so tests do not have to write to an absolute path.
KEY_FILE = Path(os.environ.get("DASHBOARD_API_KEY_PATH", "/data/dashboard-api-key.txt"))

DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY")
_key_was_generated = False
if not DASHBOARD_API_KEY:
    DASHBOARD_API_KEY = secrets.token_urlsafe(32)
    _key_was_generated = True


def persist_generated_key() -> None:
    """Write a generated key to disk. Called from app startup, not at import.

    This used to run at import time, which meant anything that imported this
    module — a test, a lint pass, a one-off script — created
    /data/dashboard-api-key.txt as a side effect. Outside a container that
    path resolves against the filesystem root, so it silently created a
    directory and a secret file at the root of whatever drive it ran on.

    Doing it from the lifespan keeps the behaviour the service needs (the
    dashboard entrypoint reads this file) while making an import inert.
    """
    if not _key_was_generated:
        return
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(DASHBOARD_API_KEY)
    KEY_FILE.chmod(0o600)
    logger.warning(
        "DASHBOARD_API_KEY not set. Generated temporary key and wrote to %s (mode 0600). "
        "Set DASHBOARD_API_KEY in your .env file for production.", KEY_FILE
    )

security_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(credentials: HTTPAuthorizationCredentials = Security(security_scheme)):
    """Verify API key for protected endpoints."""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide Bearer token in Authorization header.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, and the presented token is attacker-controlled, so a str compare
    # turns an unauthenticated request into a 500 instead of a 403.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), DASHBOARD_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return credentials.credentials
