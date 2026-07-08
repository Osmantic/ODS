"""API key authentication for ODS Dashboard API."""

import logging
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger(__name__)

DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY")
if not DASHBOARD_API_KEY:
    DASHBOARD_API_KEY = secrets.token_urlsafe(32)
    key_file = Path("/data/dashboard-api-key.txt")
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(DASHBOARD_API_KEY)
    key_file.chmod(0o600)
    logger.warning(
        "DASHBOARD_API_KEY not set. Generated temporary key and wrote to %s (mode 0600). "
        "Set DASHBOARD_API_KEY in your .env file for production.", key_file
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
    if not secrets.compare_digest(credentials.credentials, DASHBOARD_API_KEY):
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return credentials.credentials
