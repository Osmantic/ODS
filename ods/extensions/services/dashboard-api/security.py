"""API key authentication for ODS Dashboard API."""

import logging
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)


def _get_or_create_api_key() -> str:
    env_key = os.environ.get("DASHBOARD_API_KEY")
    if env_key:
        return env_key
    key_file = Path("/data/dashboard-api-key.txt")
    try:
        stored = key_file.read_text().strip()
        if stored:
            return stored
    except OSError:
        pass
    new_key = secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(new_key)
    key_file.chmod(0o600)
    logger.warning(
        "DASHBOARD_API_KEY not set in environment. Generated temporary key and wrote to %s (mode 0600). "
        "Set DASHBOARD_API_KEY in your .env file for production.",
        key_file,
    )
    return new_key


DASHBOARD_API_KEY = _get_or_create_api_key()

security_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Security(security_scheme),
):
    """Verify API key for protected endpoints."""
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Provide Bearer token in Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, and the presented token is attacker-controlled, so a str compare
    # turns an unauthenticated request into a 500 instead of a 403.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), DASHBOARD_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return credentials.credentials
