"""API key authentication for ODS Dashboard API."""

import logging
import os
import secrets
import tempfile
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)


def _load_or_create_api_key(configured_key: str | None, key_file: Path) -> str:
    """Return the configured key or a stable generated key from disk."""
    if configured_key:
        return configured_key

    try:
        persisted = key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        persisted = ""
    if persisted:
        logger.info("Loaded generated DASHBOARD_API_KEY from %s", key_file)
        return persisted

    generated = secrets.token_urlsafe(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{key_file.name}.",
        suffix=".tmp",
        dir=key_file.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(generated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key_file)
    finally:
        temporary.unlink(missing_ok=True)
    logger.warning(
        "DASHBOARD_API_KEY not set. Generated a persistent key at %s (mode 0600). "
        "Set DASHBOARD_API_KEY in your .env file for production.", key_file
    )
    return generated


DASHBOARD_API_KEY = _load_or_create_api_key(
    os.environ.get("DASHBOARD_API_KEY"),
    Path(os.environ.get("DASHBOARD_API_KEY_FILE", "/data/dashboard-api-key.txt")),
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
