"""Magic-link auth - generate QR-friendly URLs for owner and guest access.

Provides the storage, lifecycle, and redemption plumbing for temporary guest
links and revoke-only owner cards. Redemption sets an audited dashboard-api
session cookie and redirects to the token's target surface.

Endpoints:
  POST   /api/auth/magic-link/generate   admin → create token, return URL + QR data
  GET    /auth/magic-link/{token}        public -> redeem, set cookie, 302 to target
  GET    /api/auth/magic-link/list       admin → pending + recently-redeemed
  DELETE /api/auth/magic-link/{token}    admin → revoke

Security posture:
  * Tokens are 32 url-safe bytes from secrets.token_urlsafe; only the SHA-256
    hash is persisted — plaintext lives in memory only during generation.
  * Guest redemption is single-use by default; reusable=True marks a token as
    shareable (e.g. a family invite poster) and tracks each redemption in the
    audit trail.
  * Guest tokens have a 60-minute default expiry; owner tokens are reusable
    until revoked and never returned by the list API in plaintext.
  * Rate-limit on redemption: 5 failed attempts per remote IP per minute.
  * Cookie issued is HttpOnly + SameSite=Lax + Secure when HTTPS. Default
    host-based links set Domain=<device>.local so redemption on auth.<device>.local
    carries through to chat.<device>.local.
  * No information leaks: invalid/expired/already-redeemed tokens all return
    the same 404 "Invalid or expired magic link" so a holder cannot fingerprint
    state.

Storage layout (data/auth/magic-links.json):
  {
    "tokens": [
      {
        "token_hash": "<sha256 hex>",
        "target_username": "alice",
        "scope": "chat",
        "reusable": false,
        "created_at": "2026-05-02T14:00:00Z",
        "expires_at": "2026-05-02T15:00:00Z",
        "created_by_ip": "127.0.0.1",
        "redemptions": [
          {"at": "2026-05-02T14:05:00Z", "ip": "192.168.1.42", "user_agent": "..."}
        ],
        "revoked_at": null
      }
    ]
  }
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator, model_validator

import session_signer
from config import EXTENSIONS_DIR, GPU_BACKEND, SERVICES, load_extension_manifests
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["magic-link"])

DATA_DIR = Path(os.environ.get("ODS_DATA_DIR", "/data"))
AUTH_DIR = DATA_DIR / "auth"
TOKEN_STORE_PATH = AUTH_DIR / "magic-links.json"
SESSION_COOKIE_NAME = "ods-session"
DEFAULT_EXPIRY_SECONDS = 3600  # 60 minutes
MIN_EXPIRY_SECONDS = 60
MAX_EXPIRY_SECONDS = 86400
MAX_TOKENS_RETAINED = 200  # cap audit log growth

# Used to build subdomain URLs (auth.<name>.local, chat.<name>.local).
# Falls back to "ods" so a half-configured install still produces a
# sane-looking URL.
_DEVICE_NAME_DEFAULT = "ods"
# Session cookie TTL — 12 hours of rolling chat access after redemption.
SESSION_TTL_SECONDS = 12 * 3600

# Rate-limit table — {ip: (failed_count, window_start_epoch)}
# Single-process FastAPI, no need for shared state across workers.
_RATE_LIMIT_BUCKETS: dict[str, tuple[int, float]] = {}
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_FAILURES = 5

# Store mutation lock — prevents lost writes when generate/redeem race.
_STORE_LOCK = threading.Lock()


# --- Pydantic schemas ---


class GenerateRequest(BaseModel):
    target_username: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Username Open WebUI should provision / sign in as on redemption.",
    )
    scope: str = Field(
        default="chat",
        pattern=r"^(chat|hermes)$",
        description="Redirect target after redemption. chat lands in Open WebUI; hermes lands in the Hermes Agent.",
    )
    expires_in: Optional[int] = Field(
        default=None,
        ge=MIN_EXPIRY_SECONDS,
        le=MAX_EXPIRY_SECONDS,
        description="Guest token validity in seconds. Owner tokens are revoke-only and must omit this.",
    )
    reusable: bool = Field(
        default=False,
        description="When True, the token can be redeemed multiple times until expiry. Useful for family/share-poster invites.",
    )
    token_type: str = Field(
        default="guest",
        pattern=r"^(guest|owner)$",
        description="guest tokens expire; owner tokens are reusable until manually revoked.",
    )
    url_mode: str = Field(
        default="auto",
        pattern=r"^(auto|lan|public)$",
        description="auto uses the normal URL policy; lan forces .local URLs; public uses ODS_PUBLIC_URL when configured.",
    )
    note: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Free-form note shown in the admin list (e.g. 'for mom').",
    )

    @field_validator("target_username")
    @classmethod
    def _strip_username(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="before")
    @classmethod
    def _normalize_lifecycle(cls, data):
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        token_type = normalized.get("token_type") or "guest"
        normalized["token_type"] = token_type

        if token_type == "owner":
            if normalized.get("expires_in") is not None:
                raise ValueError("owner tokens are revoke-only; omit expires_in")
            normalized["scope"] = normalized.get("scope") or "hermes"
            normalized["reusable"] = True
            if normalized.get("url_mode") in (None, "auto"):
                normalized["url_mode"] = "lan"
        else:
            normalized["scope"] = normalized.get("scope") or "chat"
            if normalized.get("expires_in") is None:
                normalized["expires_in"] = DEFAULT_EXPIRY_SECONDS
            normalized["url_mode"] = normalized.get("url_mode") or "auto"

        return normalized


class GenerateResponse(BaseModel):
    token: str  # plaintext — returned ONCE on generation, never persisted in cleartext
    url: str
    expires_at: Optional[str]
    target_username: str
    scope: str
    reusable: bool
    token_type: str
    url_mode: str


class TokenSummary(BaseModel):
    token_hash_prefix: str  # first 8 chars of hash — enough to identify, not enough to forge
    target_username: str
    scope: str
    reusable: bool
    token_type: str
    url_mode: str
    created_at: str
    expires_at: Optional[str]
    redemption_count: int
    last_redeemed_at: Optional[str]
    revoked_at: Optional[str]
    note: Optional[str]


# --- Token storage helpers (file-backed, locked) ---


def _magic_link_store_candidates() -> list[Path]:
    return [
        DATA_DIR / "auth" / "magic-links.json",
        DATA_DIR / "config" / "auth" / "magic-links.json",
    ]


def _writable_store_path() -> Path:
    """Return the normal token store, or a writable config-backed fallback."""
    last_error: OSError | None = None
    for path in _magic_link_store_candidates():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            probe = path.parent / ".write-test"
            probe.write_text("", encoding="utf-8")
            try:
                probe.unlink()
            except OSError:
                pass
            if path != TOKEN_STORE_PATH:
                logger.warning("magic-link store falling back to %s", path)
            return path
        except OSError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _ensure_store() -> dict:
    """Load the token store, creating an empty one if missing."""
    store_path = _writable_store_path()
    if not store_path.exists():
        return {"tokens": []}
    try:
        return json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupted store — start fresh rather than blocking generation.
        logger.exception("magic-link store unreadable at %s; starting fresh", store_path)
        return {"tokens": []}


def _write_store(store: dict) -> None:
    """Persist the store atomically (write-tmp + rename)."""
    store_path = _writable_store_path()
    tmp = store_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, indent=2), encoding="utf-8")
    tmp.replace(store_path)
    try:
        store_path.chmod(0o600)
    except OSError:
        # Best-effort; some filesystems (Docker volumes) don't honor chmod.
        pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_record(record: dict) -> dict:
    """Fill defaults for records created before owner onboarding existed."""
    token_type = record.get("token_type") or "guest"
    if token_type not in {"guest", "owner"}:
        token_type = "guest"
    record["token_type"] = token_type

    scope = record.get("scope") or "chat"
    if scope not in {"chat", "hermes"}:
        scope = "chat"
    record["scope"] = scope

    url_mode = record.get("url_mode") or "auto"
    if url_mode not in {"auto", "lan", "public"}:
        url_mode = "auto"
    if token_type == "owner" and url_mode == "auto":
        url_mode = "lan"
    record["url_mode"] = url_mode

    if token_type == "owner":
        record["reusable"] = True
        record["expires_at"] = None
    else:
        record["reusable"] = bool(record.get("reusable", False))

    if not isinstance(record.get("redemptions"), list):
        record["redemptions"] = []
    record.setdefault("note", None)
    record.setdefault("revoked_at", None)
    return record


def _is_expired(token_record: dict, now: Optional[datetime] = None) -> bool:
    token_record = _normalize_record(token_record)
    if token_record["token_type"] == "owner" or not token_record.get("expires_at"):
        return False
    now = now or datetime.now(timezone.utc)
    expires_at = datetime.fromisoformat(token_record["expires_at"])
    return now >= expires_at


def _prune(store: dict) -> dict:
    """Drop expired single-use tokens and cap retention."""
    now = datetime.now(timezone.utc)
    keep = []
    for record in store.get("tokens", []):
        record = _normalize_record(record)
        # Always keep revoked or recently-redeemed records for audit.
        if record.get("revoked_at"):
            keep.append(record)
            continue
        if not _is_expired(record, now):
            keep.append(record)
            continue
        # Expired: keep if redeemed (audit), drop if never used.
        if record.get("redemptions"):
            keep.append(record)
    # Cap retention — oldest-first eviction.
    keep.sort(key=lambda r: r["created_at"])
    if len(keep) > MAX_TOKENS_RETAINED:
        keep = keep[-MAX_TOKENS_RETAINED:]
    store["tokens"] = keep
    return store


def _find_by_hash(store: dict, token_hash: str) -> Optional[dict]:
    for record in store.get("tokens", []):
        if record["token_hash"] == token_hash:
            return record
    return None


# --- Rate limiting ---


def _check_rate_limit(ip: str) -> None:
    """Raise 429 if the IP has exceeded the failure window."""
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        if len(_RATE_LIMIT_BUCKETS) > 1000:
            stale = [k for k, v in _RATE_LIMIT_BUCKETS.items() if now - v[1] > _RATE_LIMIT_WINDOW_SECONDS]
            for k in stale:
                del _RATE_LIMIT_BUCKETS[k]
            if len(_RATE_LIMIT_BUCKETS) > 10000:
                _RATE_LIMIT_BUCKETS.clear()

        count, window_start = _RATE_LIMIT_BUCKETS.get(ip, (0, now))
        if now - window_start > _RATE_LIMIT_WINDOW_SECONDS:
            # Window expired; reset.
            _RATE_LIMIT_BUCKETS.pop(ip, None)
            return
        if count >= _RATE_LIMIT_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail="Too many failed redemption attempts. Try again later.",
            )


def _record_failure(ip: str) -> None:
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        count, window_start = _RATE_LIMIT_BUCKETS.get(ip, (0, now))
        if now - window_start > _RATE_LIMIT_WINDOW_SECONDS:
            _RATE_LIMIT_BUCKETS[ip] = (1, now)
        else:
            _RATE_LIMIT_BUCKETS[ip] = (count + 1, window_start)


# --- QR code generation ---


def _qr_data_url(text: str) -> Optional[str]:
    """Return a data: URL for a QR code of `text`, or None if the qrcode lib isn't installed.

    The admin-side dashboard uses this to display the magic link as a scannable
    QR. The qrcode library is small and pure-Python (with optional PIL for
    higher-quality output). Make it optional so an install without the library
    still returns a usable URL.
    """
    try:
        import qrcode  # noqa: PLC0415
        from io import BytesIO
    except ImportError:
        return None

    img = qrcode.make(text, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    payload = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{payload}"


# --- Endpoint URL builders ---


def _device_name() -> str:
    """The ODS_DEVICE_NAME segment, with a sane fallback.

    The mDNS announcer and the ods-proxy use the same name to build
    per-service hostnames (chat.<name>.local, auth.<name>.local, etc.).
    Falls back to 'ods' so a misconfigured install still produces a
    URL that looks reasonable.
    """
    raw = (os.environ.get("ODS_DEVICE_NAME") or _DEVICE_NAME_DEFAULT).strip()
    return raw or _DEVICE_NAME_DEFAULT


def _public_base() -> str:
    """Override base for the magic-link URL (operator escape hatch).

    Set ODS_PUBLIC_URL to an absolute URL (e.g., a Tailscale tunnel
    or custom domain) and the magic-link / redirect URLs build off
    that instead of the per-subdomain default. Empty → use
    `_auth_url` / `_chat_url` built from ODS_DEVICE_NAME.
    """
    return (os.environ.get("ODS_PUBLIC_URL") or "").rstrip("/")


def _use_public_url(url_mode: str = "auto") -> bool:
    return url_mode != "lan" and bool(_public_base())


def _auth_url(url_mode: str = "auto") -> str:
    """Origin where magic-link redemption (this service) is reached.

    Default: http://auth.<ODS_DEVICE_NAME>.local — the ods-proxy
    on :80 with Host: auth.<name>.local routes to dashboard-api.
    ODS_PUBLIC_URL overrides for tunneled / non-mDNS deployments.
    """
    if _use_public_url(url_mode):
        return _public_base()
    return f"http://auth.{_device_name()}.local"


def _chat_url(url_mode: str = "auto") -> str:
    """Where a successful redemption redirects to (Open WebUI via proxy).

    Default: http://chat.<ODS_DEVICE_NAME>.local. Cookie set by
    redemption uses Domain=<ODS_DEVICE_NAME>.local so it carries
    across to the chat subdomain — that's how single-redemption SSO
    works without identity claims in the cookie.
    """
    if _use_public_url(url_mode):
        # When ODS_PUBLIC_URL is overridden, assume /chat is the chat
        # path on that origin (mirrors WEBUI_URL=…/chat for tunnel users).
        return f"{_public_base()}/chat"
    return f"http://chat.{_device_name()}.local"


def _hermes_url(url_mode: str = "auto") -> str:
    """Where a successful Hermes redemption redirects."""
    if _use_public_url(url_mode):
        return f"{_public_base()}/hermes"
    return f"http://hermes.{_device_name()}.local"


def _talk_url(url_mode: str = "auto") -> str:
    """Where a successful owner-card redemption redirects.

    ODS Talk is served by the dashboard container but presented as its own
    phone-first host so owner cards don't land consumers in the advanced
    Hermes interface.
    """
    if _use_public_url(url_mode):
        return f"{_public_base()}/talk"
    return f"http://talk.{_device_name()}.local/talk"


def _redirect_url(record: dict) -> str:
    record = _normalize_record(record)
    if record["scope"] == "hermes":
        if record["token_type"] == "owner":
            return _talk_url(record["url_mode"])
        return _hermes_url(record["url_mode"])
    return _chat_url(record["url_mode"])


def _magic_link_url(token: str, url_mode: str = "auto") -> str:
    """Public URL the user scans/clicks to redeem.

    Default: http://auth.<ODS_DEVICE_NAME>.local/magic-link/<token>.
    The cookie set by the redemption handler uses
    Domain=<ODS_DEVICE_NAME>.local so it's also sent to
    chat.<name>.local, hermes.<name>.local, etc. (subdomain SSO).
    """
    base = _auth_url(url_mode).rstrip("/")
    # When using the override (ODS_PUBLIC_URL), preserve the /auth/
    # prefix that the ods-proxy historically routed to dashboard-api.
    # When using the default subdomain, no /auth/ prefix is needed
    # since auth.<name>.local already targets dashboard-api at root.
    if _use_public_url(url_mode):
        return f"{base}/auth/magic-link/{token}"
    return f"{base}/magic-link/{token}"


def _cookie_domain(url_mode: str = "auto") -> Optional[str]:
    """Cookie Domain attribute. Empty/None = host-only cookie.

    ODS_COOKIE_DOMAIN can override the parent domain used for subdomain SSO.
    In the default ods-proxy layout, derive <ODS_DEVICE_NAME>.local so a
    redemption on auth.<name>.local authenticates requests to chat.<name>.local.
    If ODS_PUBLIC_URL is set, keep the cookie host-only because the operator
    is explicitly using a single custom origin/path layout.
    """
    raw = (os.environ.get("ODS_COOKIE_DOMAIN") or "").strip()
    if raw:
        return raw
    if _use_public_url(url_mode):
        return None
    return f"{_device_name()}.local"


def _ods_proxy_service() -> Optional[dict]:
    """Return ods-proxy service config, refreshing once for post-enable state.

    dashboard-api loads extension manifests at process startup, but `ods
    enable ods-proxy` flips compose.yaml.disabled to compose.yaml while this
    process is still running. Owner-card readiness must see that transition
    without requiring an operator to restart dashboard-api manually.
    """
    service = SERVICES.get("ods-proxy")
    if service:
        return service

    refreshed_services, _, errors = load_extension_manifests(EXTENSIONS_DIR, GPU_BACKEND)
    if errors:
        logger.debug("Manifest refresh while checking ods-proxy reported %d errors", len(errors))
    service = refreshed_services.get("ods-proxy")
    if service:
        SERVICES["ods-proxy"] = service
    return service


def _ods_proxy_lan_ready() -> tuple[bool, str]:
    """Return whether owner-card LAN URLs can actually be served."""
    service = _ods_proxy_service()
    if not service:
        return (
            False,
            "ODS Talk owner cards require ods-proxy. Enable LAN web access before generating an owner card.",
        )

    host = service.get("host") or "ods-proxy"
    port = int(service.get("port") or 80)
    health = str(service.get("health") or "/health")
    if not health.startswith("/"):
        health = f"/{health}"
    url = f"http://{host}:{port}{health}"
    try:
        with urllib.request.urlopen(url, timeout=1.0) as resp:
            status = getattr(resp, "status", 0)
            if 200 <= status < 400:
                return True, ""
            return False, f"ods-proxy health returned HTTP {status}"
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return False, f"ods-proxy is enabled but not reachable: {exc}"


def _owner_card_requires_lan_proxy(payload: GenerateRequest) -> bool:
    return payload.token_type == "owner" and not _use_public_url(payload.url_mode)


# --- Endpoints ---


@router.get("/api/auth/magic-link/owner-card/status", dependencies=[Depends(verify_api_key)])
def owner_card_status() -> dict:
    """Report whether LAN owner-card URLs can be generated safely."""
    ready, reason = _ods_proxy_lan_ready()
    return {
        "ready": ready,
        "requires": "ods-proxy",
        "reason": "" if ready else reason,
    }


@router.post("/api/auth/magic-link/generate", dependencies=[Depends(verify_api_key)])
def generate_magic_link(payload: GenerateRequest, request: Request) -> GenerateResponse:
    """Create a new magic link. Admin-only (verify_api_key)."""
    if payload.url_mode == "public" and not _public_base():
        raise HTTPException(
            status_code=400,
            detail="url_mode=public requires ODS_PUBLIC_URL to be configured",
        )
    if _owner_card_requires_lan_proxy(payload):
        ready, reason = _ods_proxy_lan_ready()
        if not ready:
            raise HTTPException(status_code=409, detail=reason)
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    created_at = datetime.now(timezone.utc)
    expires_at = (
        None
        if payload.token_type == "owner"
        else created_at + timedelta(seconds=payload.expires_in or DEFAULT_EXPIRY_SECONDS)
    )
    record = {
        "token_hash": token_hash,
        "target_username": payload.target_username,
        "scope": payload.scope,
        "reusable": payload.reusable,
        "token_type": payload.token_type,
        "url_mode": payload.url_mode,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "created_by_ip": _client_ip(request),
        "redemptions": [],
        "revoked_at": None,
        "note": payload.note,
    }
    with _STORE_LOCK:
        store = _prune(_ensure_store())
        store["tokens"].append(record)
        _write_store(store)

    url = _magic_link_url(token, payload.url_mode)
    logger.info(
        "magic-link generated for target=%s type=%s scope=%s reusable=%s expires_at=%s",
        payload.target_username, payload.token_type, payload.scope, payload.reusable, record["expires_at"],
    )

    return GenerateResponse(
        token=token,
        url=url,
        expires_at=expires_at.isoformat() if expires_at else None,
        target_username=payload.target_username,
        scope=payload.scope,
        reusable=payload.reusable,
        token_type=payload.token_type,
        url_mode=payload.url_mode,
    )


@router.get("/api/auth/magic-link/qr", dependencies=[Depends(verify_api_key)])
def magic_link_qr(url: str) -> dict:
    """Return a QR-code data URL for a previously-generated magic link.

    Separated from generate() so the admin UI can render the QR on demand
    (e.g., after re-displaying a previously-generated invite from the list).
    The URL is treated as opaque payload — this endpoint does NOT validate
    that it points at a real magic link; the admin already has the link.
    """
    data_url = _qr_data_url(url)
    if not data_url:
        raise HTTPException(
            status_code=503,
            detail="qrcode library not installed. Install with: pip install qrcode[pil]",
        )
    return {"data_url": data_url}


# Two routes for the same handler. The default URL builder uses
# /magic-link/<token> (the proxy mounts dashboard-api at root on
# auth.<device>.local, so /magic-link/... is the natural path). The
# /auth/magic-link/<token> path is kept for back-compat with the
# ODS_PUBLIC_URL override case where dashboard-api is reached via a
# proxy that strips a /auth prefix, and for any in-flight QR codes
# generated before the URL shape change.
@router.get("/magic-link/{token}")
@router.get("/auth/magic-link/{token}")
def redeem_magic_link(token: str, request: Request, response: Response) -> RedirectResponse:
    """Public redemption endpoint.

    Validates the token, marks it redeemed, sets an audited session cookie,
    and 302s to the chat URL. Wrong tokens get a constant 404 — no oracle for
    fingerprinting whether a token exists vs is expired vs is already used.
    """
    ip = _client_ip(request)
    _check_rate_limit(ip)

    # Pre-flight: if ODS_SESSION_SECRET isn't configured, refuse the
    # redemption BEFORE marking the token used. Otherwise a misconfigured
    # install burns a single-use invite on every attempt — the user can't
    # retry, can't recover, and the admin has to mint a new link. The
    # 503 is honest about it being a server misconfig, not a user error.
    if not session_signer.is_configured():
        logger.error(
            "magic-link redemption refused: ODS_SESSION_SECRET is not "
            "configured. Set it in .env (32+ random bytes) and restart "
            "dashboard-api."
        )
        raise HTTPException(
            status_code=503,
            detail="Session signing is not configured on this server. Ask the operator.",
        )

    # Constant-shape failure response. We construct the success path inside
    # the lock and only commit if every check passes.
    token_hash = _hash_token(token)
    redirect_to = _chat_url()

    with _STORE_LOCK:
        store = _prune(_ensure_store())
        record = _find_by_hash(store, token_hash)

        if record is None:
            _record_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired magic link")
        record = _normalize_record(record)
        if record.get("revoked_at"):
            _record_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired magic link")
        if _is_expired(record):
            _record_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired magic link")
        if record["redemptions"] and not record.get("reusable", False):
            _record_failure(ip)
            raise HTTPException(status_code=404, detail="Invalid or expired magic link")
        redirect_to = _redirect_url(record)

        # Success — record the redemption and persist.
        user_agent = request.headers.get("user-agent", "")[:200]
        record["redemptions"].append({
            "at": _now_iso(),
            "ip": ip,
            "user_agent": user_agent,
        })
        _write_store(store)

    # Build the response. The session cookie is HMAC-signed via
    # session_signer.issue() — guarded by is_configured() above so we
    # don't reach this line without a usable secret.
    session_token = session_signer.issue(ttl_seconds=SESSION_TTL_SECONDS)
    secure_cookie = request.url.scheme == "https"
    cookie_domain = _cookie_domain(record.get("url_mode", "auto"))

    logger.info(
        "magic-link redeemed target=%s type=%s scope=%s ip=%s redirecting=%s",
        record["target_username"], record["token_type"], record["scope"], ip, redirect_to,
    )

    redirect = RedirectResponse(url=redirect_to, status_code=302)
    # set_cookie's `domain` parameter is the Cookie's Domain attribute.
    # Pass `None` (omit) for a host-only cookie; pass the bare hostname
    # to let the browser send it to all subdomains. FastAPI / Starlette
    # treat "" as no-domain too, but `None` is the canonical signal.
    cookie_kwargs: dict = dict(
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    if cookie_domain:
        cookie_kwargs["domain"] = cookie_domain

    redirect.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        **cookie_kwargs,
    )
    # Hint to the chat UI which user this redemption was for; Open WebUI
    # ignores unknown cookies but a future integration can read this.
    target_user_kwargs: dict = dict(
        max_age=DEFAULT_EXPIRY_SECONDS,
        httponly=False,  # readable by the chat UI's JS for pre-fill
        samesite="lax",
        secure=secure_cookie,
        path="/",
    )
    if cookie_domain:
        target_user_kwargs["domain"] = cookie_domain
    redirect.set_cookie(
        key="ods-target-user",
        value=record["target_username"],
        **target_user_kwargs,
    )
    return redirect


@router.get("/api/auth/magic-link/list", dependencies=[Depends(verify_api_key)])
def list_magic_links() -> dict:
    """Admin view of active + recently-redeemed tokens."""
    with _STORE_LOCK:
        store = _prune(_ensure_store())
        _write_store(store)  # persist pruning
        out: list[TokenSummary] = []
        for r in store.get("tokens", []):
            r = _normalize_record(r)
            out.append(TokenSummary(
                token_hash_prefix=r["token_hash"][:8],
                target_username=r["target_username"],
                scope=r["scope"],
                reusable=r.get("reusable", False),
                token_type=r["token_type"],
                url_mode=r["url_mode"],
                created_at=r["created_at"],
                expires_at=r.get("expires_at"),
                redemption_count=len(r.get("redemptions", [])),
                last_redeemed_at=(r["redemptions"][-1]["at"] if r.get("redemptions") else None),
                revoked_at=r.get("revoked_at"),
                note=r.get("note"),
            ))
    return {"tokens": [s.model_dump() for s in out]}


@router.delete("/api/auth/magic-link/{token_hash_prefix}", dependencies=[Depends(verify_api_key)])
def revoke_magic_link(token_hash_prefix: str) -> dict:
    """Revoke by token hash prefix (the value shown in the admin list).

    Idempotent: revoking an already-revoked / expired / nonexistent token
    returns the same 404 so admins don't fingerprint state. A successful
    revocation flips revoked_at to now, leaves the audit trail intact.
    """
    if len(token_hash_prefix) < 4 or len(token_hash_prefix) > 64:
        raise HTTPException(status_code=400, detail="Invalid token hash prefix")
    with _STORE_LOCK:
        store = _ensure_store()
        for record in store.get("tokens", []):
            if record["token_hash"].startswith(token_hash_prefix) and not record.get("revoked_at"):
                record["revoked_at"] = _now_iso()
                _write_store(store)
                logger.info("magic-link revoked target=%s", record["target_username"])
                return {"revoked": True}
    raise HTTPException(status_code=404, detail="No active magic link with that prefix")


# --- Helpers ---


def _client_ip(request: Request) -> str:
    """Best-effort remote IP. Honors X-Forwarded-For if behind a trusted proxy.

    The dashboard-api is bound to 127.0.0.1 by default, so the realistic
    callers are: same-host (loopback), Docker bridge gateway, or a reverse
    proxy. If you put a real reverse proxy in front, set ODS_TRUST_FORWARDED=1
    so we honor X-Forwarded-For; otherwise we use the direct connection IP
    (the proxy's IP, which is fine for rate-limit grouping).
    """
    if os.environ.get("ODS_TRUST_FORWARDED") == "1":
        forwarded = request.headers.get("x-forwarded-for", "").split(",")
        if forwarded and forwarded[0].strip():
            return forwarded[0].strip()
    client = request.client
    return client.host if client else "unknown"
