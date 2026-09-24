"""API key authentication for ODS Dashboard API."""

import logging
import os
import secrets
import tempfile
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)


def _load_or_create_api_key(key_file: Path) -> tuple[str, bool]:
    """Return a stable fallback key and whether this call created it."""
    try:
        api_key = key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        api_key = ""
    else:
        if not api_key:
            raise RuntimeError(f"Dashboard API key file is empty: {key_file}")
        key_file.chmod(0o600)
        return api_key, False

    key_file.parent.mkdir(parents=True, exist_ok=True)
    api_key = secrets.token_urlsafe(32)
    fd, temporary_name = tempfile.mkstemp(
        dir=key_file.parent,
        prefix=f".{key_file.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(api_key)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key_file)
        key_file.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return api_key, True


DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY")
if not DASHBOARD_API_KEY:
    key_file = Path("/data/dashboard-api-key.txt")
    DASHBOARD_API_KEY, key_created = _load_or_create_api_key(key_file)
    if key_created:
        logger.warning(
            "DASHBOARD_API_KEY not set. Generated fallback key at %s (mode 0600). "
            "Set DASHBOARD_API_KEY in your .env file for production.", key_file
        )
    else:
        logger.info("DASHBOARD_API_KEY not set. Reusing fallback key from %s.", key_file)

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
