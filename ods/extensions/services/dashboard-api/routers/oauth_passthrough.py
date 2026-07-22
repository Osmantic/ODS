"""OAuth callback passthrough for agent-driven skill setup.

Hermes Agent ships with per-skill setup scripts (e.g.
``/opt/hermes/skills/productivity/google-workspace/scripts/setup.py``) that
are explicitly designed to be agent-driven — the agent runs ``--auth-url``
to get an OAuth consent link, sends it to the user, the user authorizes in
their browser, and the agent runs ``--auth-code <CODE>`` to finalise.

The problem with that flow on ODS Talk: the user has to manually copy
the OAuth code out of their browser's URL bar and paste it back into the
chat. That's the friction you feel on every setup. The "magic" UX is the
browser redirect coming back to a ODS endpoint that captures the
code and hands it to the agent automatically — that's this module.

How it slots in:

  1. Agent calls ``POST /api/oauth/init`` with the skill it wants to
     authorise. dashboard-api generates a high-entropy nonce, persists
     ``{nonce, skill_id, return_url, created_at, ttl}`` under
     ``data/persona/oauth-nonces/<nonce>.json``, and returns the nonce
     as ``state``.
  2. Agent runs ``setup.py --auth-url --state <NONCE>``. The
     ``redirect_uri`` baked into the OAuth client points at this module's
     ``/api/oauth/callback`` route on the operator's ODS host.
  3. Agent sends the auth URL to the user as a markdown link. The user
     taps it, authorises in Google/Spotify/etc., and the provider
     redirects to ``/api/oauth/callback?code=...&state=<nonce>``.
  4. This handler looks up the nonce, refuses the callback if state is
     missing/unknown/expired/replayed, and only on a valid nonce writes
     the ``{code, state, ts}`` payload to ``data/persona/oauth_callback.json``
     (operator-owned; Hermes reads it) and returns a friendly success
     page. The nonce is deleted on write, making it single-use.
  5. The agent (per persona) checks for the callback file after sending
     the URL — when present, it consumes the code, runs the skill's
     ``setup.py --auth-code <CODE>`` to finalise, deletes the file, and
     confirms to the user.

Why a file rather than calling Hermes directly: dashboard-api can't
docker-exec into the hermes container without docker-in-docker
plumbing, and the hermes container is uid-10000-owned so dashboard-api
can't write into ``/opt/data`` either. ``data/persona/`` is the
operator-owned shared mount (same one the install-context SOUL.md
lives in) that both containers can read.

Security model:
  * The callback route stays unauthenticated (it's a redirect target —
    we can't enforce session cookies from a provider redirect). Protection
    comes from the ``state`` parameter, which MUST match a server-issued
    nonce created via ``/api/oauth/init``. Callbacks with missing,
    malformed, unknown, expired, or already-consumed states are rejected
    outright and never touch ``oauth_callback.json``.
  * Nonces are single-use: consumed (deleted) BEFORE the callback payload
    is written, so a race between two callbacks with the same state
    resolves to one winner even under concurrent load.
  * Nonces bind the ``return_url`` at init time. The callback endpoint
    never accepts a ``return_url`` query param — that eliminates the
    open-redirect surface a provider redirect could otherwise expose.
  * ``skill_id`` is resolved server-side from the nonce, not from the
    incoming callback. Attackers can no longer influence which skill
    the agent later exchanges the code against.
  * Codes have very short TTLs at the provider (~10 min); nonces default
    to 15 min. Expired nonces are opportunistically pruned on each init
    so we don't accumulate garbage.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

# base64url alphabet (RFC 4648 §5). token_urlsafe(32) yields 43 chars; we
# accept 22..128 to allow for future entropy tweaks without a breaking
# change. The strict charset is the primary defense against path traversal
# via the state parameter.
_STATE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,128}$")

# Skill ids are rendered into the success page and echoed back into the
# callback payload the agent consumes; keep the charset boring.
_SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

_DEFAULT_NONCE_TTL_SECONDS = 900  # 15 min; OAuth provider codes usually expire in ~10 min


def _callback_dir() -> Path:
    """Where dashboard-api writes the captured OAuth callback for the
    agent to consume. ``data/persona/`` is the operator-owned mount that
    Hermes can read (we don't put it in ``data/hermes/`` because that's
    uid 10000 and dashboard-api can't write there).
    """
    # In-container path: dashboard-api mounts ./data → /data, so
    # ./data/persona/ is /data/persona/ from here.
    base = Path(os.environ.get("ODS_PERSONA_DIR", "/data/persona"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _nonce_dir() -> Path:
    """Per-nonce metadata files. Kept next to (not inside) the callback
    file so a listing operation on the persona dir doesn't get noisy."""
    base = _callback_dir() / "oauth-nonces"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _install_dir() -> Path:
    return Path(os.environ.get("ODS_INSTALL_DIR", "/ods"))


def _data_dir() -> Path:
    return Path(os.environ.get("ODS_DATA_DIR", "/data"))


def _providers_file() -> Path:
    override = os.environ.get("ODS_OAUTH_PROVIDERS_FILE", "").strip()
    if override:
        return Path(override)
    return _install_dir() / "extensions" / "services" / "hermes" / "oauth-providers.json"


def _credential_roots() -> list[Path]:
    override = os.environ.get("ODS_OAUTH_CREDENTIAL_DIRS", "").strip()
    if override:
        return [Path(item) for item in override.split(os.pathsep) if item.strip()]
    data_dir = _data_dir()
    return [
        data_dir / "hermes",
        data_dir / "hermes" / "credentials",
        data_dir / "persona" / "oauth",
    ]


def _load_provider_registry() -> dict:
    path = _providers_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": "ods.oauth-providers.v1", "providers": []}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("oauth provider registry unavailable at %s: %s", path, exc)
        return {"schema_version": "ods.oauth-providers.v1", "providers": [], "error": str(exc)}
    if not isinstance(payload, dict):
        return {"schema_version": "ods.oauth-providers.v1", "providers": [], "error": "registry root must be an object"}
    providers = payload.get("providers")
    if not isinstance(providers, list):
        payload["providers"] = []
        payload["error"] = "providers must be a list"
    return payload


def _credential_status(provider: dict) -> tuple[bool, list[str]]:
    found: list[str] = []
    credential_files = provider.get("credential_files") or []
    if not isinstance(credential_files, list):
        return False, found
    for filename in credential_files:
        if not isinstance(filename, str) or not filename or Path(filename).is_absolute():
            continue
        for root in _credential_roots():
            candidate = root / filename
            if candidate.is_file():
                found.append(f"{root.name}/{filename}")
                break
    return bool(found), found


def _safe_return_path(return_url: str) -> str | None:
    """Return a same-origin relative path, or None for unsafe links.

    OAuth callbacks are public redirect targets, so never reflect arbitrary
    absolute URLs or javascript: links into the success page. The agent can
    pass "/talk" at init time when it wants a button back into ODS Talk.
    """
    candidate = (return_url or "").strip()
    if not candidate.startswith("/"):
        return None
    # Reject protocol-relative URLs. Browsers fold backslashes to forward
    # slashes in the authority, so "/\evil.com" and "/\\evil.com" resolve to
    # "//evil.com" — a same-looking prefix that is really an off-origin
    # redirect. Treat the character after the leading slash as the guard, and
    # reject backslashes anywhere since a same-origin path never needs one.
    if candidate[1:2] in ("/", "\\") or "\\" in candidate:
        return None
    return candidate


def _nonce_path(state: str) -> Optional[Path]:
    """Resolve ``state`` to a nonce file path, or ``None`` if the state
    is malformed or would escape the nonce directory.

    The regex is the primary defense against path traversal (``..``, ``/``
    and ``.`` are all forbidden by the charset); the ``relative_to`` check
    is defense in depth against symlink shenanigans in the persona dir.
    """
    if not state or not _STATE_PATTERN.fullmatch(state):
        return None
    nonce_dir = _nonce_dir()
    candidate = nonce_dir / f"{state}.json"
    try:
        candidate.resolve(strict=False).relative_to(nonce_dir.resolve(strict=False))
    except (OSError, ValueError):
        return None
    return candidate


def _consume_nonce(state: str) -> None:
    """Best-effort delete of a nonce file. Never raises. Used on error
    paths (provider error, missing code) so a denied or malformed flow
    can't leave a nonce lying around for replay."""
    path = _nonce_path(state)
    if not path:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.debug("could not unlink nonce %s", path, exc_info=True)


def _prune_expired_nonces(nonce_dir: Path) -> None:
    """Best-effort cleanup of expired nonces. Called opportunistically on
    init so a long-running deployment doesn't accumulate stale files.
    Never raises."""
    now = int(time.time())
    try:
        entries = list(nonce_dir.glob("*.json"))
    except OSError:
        return
    for path in entries:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Unreadable/corrupt — clean it up too.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        created = int(payload.get("created_at", 0) or 0)
        ttl = int(payload.get("ttl_seconds", 0) or 0)
        if not created or not ttl or (now - created) > ttl:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _atomic_write_0600(target: Path, data: str) -> None:
    """Write ``data`` to ``target`` atomically with mode 0600 on POSIX.
    The chmod is best-effort on filesystems that don't honour it
    (Windows dev boxes, some overlayfs setups). Uses ``with_name`` rather
    than ``with_suffix`` because Path.with_suffix rejects suffixes that
    contain embedded dots on some Python versions."""
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        logger.debug("could not chmod %s to 0600", tmp, exc_info=True)
    tmp.replace(target)


def _success_page(skill: str, return_url: Optional[str] = None) -> str:
    """The HTML the user sees after authorising. Friendly, clear about
    what just happened, with a button back into ODS Talk if we know
    where to send them."""
    safe_skill = html.escape(skill or "service")
    back_link = ""
    safe_return_path = _safe_return_path(return_url or "")
    if safe_return_path:
        safe_return = html.escape(safe_return_path, quote=True)
        back_link = f'<p><a href="{safe_return}" class="btn">Back to ODS Talk</a></p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ODS — authorised</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1.5rem; text-align: center; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 0.5rem; }}
  p {{ color: #555; }}
  .btn {{ display: inline-block; padding: 0.7rem 1.2rem; background: #18181b; color: #fff; text-decoration: none; border-radius: 0.5rem; margin-top: 1.5rem; }}
  .check {{ font-size: 2.5rem; }}
</style>
</head>
<body>
  <div class="check">✓</div>
  <h1>Authorised</h1>
  <p>ODS just got access to your {safe_skill} account. You can close this tab and return to the chat — your assistant has picked it up.</p>
  {back_link}
</body>
</html>"""


def _error_page(reason: str) -> str:
    safe = html.escape(reason)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorisation failed</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1.5rem;text-align:center}}</style>
</head><body><h1>Authorisation failed</h1><p>{safe}</p>
<p>Head back to ODS Talk and ask your assistant to try again.</p></body></html>"""


class OAuthInitRequest(BaseModel):
    """Body for ``POST /api/oauth/init``.

    The agent calls this before generating an OAuth auth URL. The returned
    ``state`` becomes the nonce baked into the auth URL, and the callback
    handler will only accept a callback whose ``state`` matches.
    """

    skill_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Skill the agent is authorising (e.g. 'google-workspace'). Echoed back to the agent on callback so it knows which skill to finalise.",
    )
    return_url: str = Field(
        "",
        max_length=512,
        description="Optional same-origin path (e.g. '/talk') rendered as a 'Back to ODS Talk' button on the success page. Bound at init time; ignored on the callback.",
    )
    ttl_seconds: int = Field(
        _DEFAULT_NONCE_TTL_SECONDS,
        ge=60,
        le=1800,
        description="How long the nonce is valid, in seconds. Default 900 (15 min) matches typical provider code TTLs.",
    )


@router.post("/api/oauth/init")
async def oauth_init(
    request: OAuthInitRequest,
    api_key: str = Depends(verify_api_key),
):
    """Issue a single-use OAuth state nonce.

    Only the agent (which holds the dashboard API key) can call this.
    The nonce is the ONLY value the callback handler will accept as
    ``state`` — everything else is rejected outright.
    """
    skill_id = request.skill_id.strip()
    if not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise HTTPException(
            status_code=422,
            detail="skill_id must match [A-Za-z0-9._-]{1,64}",
        )

    return_url = request.return_url.strip()
    bound_return_url = ""
    if return_url:
        safe = _safe_return_path(return_url)
        if not safe:
            raise HTTPException(
                status_code=422,
                detail="return_url must be a same-origin path starting with '/' (not '//').",
            )
        bound_return_url = safe

    nonce_dir = _nonce_dir()
    _prune_expired_nonces(nonce_dir)

    now = int(time.time())
    state = secrets.token_urlsafe(32)
    payload = {
        "nonce": state,
        "skill_id": skill_id,
        "return_url": bound_return_url,
        "created_at": now,
        "ttl_seconds": request.ttl_seconds,
    }
    target = nonce_dir / f"{state}.json"
    _atomic_write_0600(target, json.dumps(payload, indent=2))

    logger.info("oauth init issued nonce for skill=%s ttl=%ds", skill_id, request.ttl_seconds)
    return {
        "state": state,
        "skill_id": skill_id,
        "expires_at": now + request.ttl_seconds,
    }


@router.get("/api/oauth/callback")
async def oauth_callback(
    code: str = Query("", description="Authorisation code returned by the OAuth provider."),
    state: str = Query("", description="Nonce previously issued by /api/oauth/init. Callbacks whose state doesn't match a live nonce are rejected."),
    error: str = Query("", description="Set by the provider if the user denied or auth failed."),
):
    """OAuth redirect target.

    Validates ``state`` against a server-issued nonce, and only on a
    successful match writes the captured ``code`` to
    ``data/persona/oauth_callback.json`` for the agent to consume. Nonces
    are single-use and consumed BEFORE the callback file is written, so
    a race between two callbacks with the same state resolves to one
    winner.
    """
    if error:
        # Consume the nonce even on error so a denied flow can't be
        # replayed. State may be missing on error redirects; _consume_nonce
        # is a no-op in that case.
        _consume_nonce(state)
        logger.warning("oauth callback received provider error: %s", error[:200])
        return HTMLResponse(_error_page(f"The provider sent back an error: {error}"), status_code=400)

    if not code:
        _consume_nonce(state)
        return HTMLResponse(
            _error_page("No authorisation code was returned. You may have denied the request, or the provider's redirect was malformed."),
            status_code=400,
        )

    # State validation is the security boundary. If the incoming state
    # doesn't map to a live nonce, refuse — do NOT write anything the
    # agent might later consume.
    nonce_path = _nonce_path(state)
    if not nonce_path or not nonce_path.exists():
        logger.warning("oauth callback with unknown/invalid state (len=%d)", len(state))
        return HTMLResponse(
            _error_page(
                "This authorisation link is not recognised. It may have already been used, "
                "or it wasn't issued by ODS. Head back to the chat and start the setup again."
            ),
            status_code=400,
        )

    try:
        nonce_payload = json.loads(nonce_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("oauth callback saw unreadable nonce %s: %s", nonce_path, exc)
        try:
            nonce_path.unlink(missing_ok=True)
        except OSError:
            pass
        return HTMLResponse(
            _error_page("ODS caught the redirect but the pending authorisation state was unreadable. Restart the flow from the chat."),
            status_code=400,
        )

    now = int(time.time())
    created = int(nonce_payload.get("created_at", 0) or 0)
    ttl = int(nonce_payload.get("ttl_seconds", 0) or 0)
    if not created or not ttl or (now - created) > ttl:
        try:
            nonce_path.unlink(missing_ok=True)
        except OSError:
            pass
        return HTMLResponse(
            _error_page("This authorisation link has expired. Head back to the chat and start the setup again."),
            status_code=400,
        )

    skill_id_raw = str(nonce_payload.get("skill_id") or "").strip()
    skill_id = skill_id_raw if _SKILL_ID_PATTERN.fullmatch(skill_id_raw) else "service"
    bound_return_url = str(nonce_payload.get("return_url") or "").strip() or None

    # Consume the nonce BEFORE writing the callback file. If two callbacks
    # race with the same state, only one unlink succeeds; the second one
    # sees the file missing and bails at the nonce_path.exists() check on
    # the next request. On this request, a failed unlink means the file
    # vanished between our exists()/read and unlink() — treat that as
    # someone else winning the race and refuse.
    try:
        nonce_path.unlink()
    except FileNotFoundError:
        logger.warning("oauth callback lost nonce race for state (len=%d)", len(state))
        return HTMLResponse(
            _error_page("Another authorisation for the same link is already in flight. Head back to the chat and try again."),
            status_code=409,
        )
    except OSError as exc:
        logger.exception("failed to consume oauth nonce %s: %s", nonce_path, exc)
        return HTMLResponse(
            _error_page("ODS caught the redirect but couldn't consume the pending state. The operator may need to check filesystem permissions on data/persona/oauth-nonces/."),
            status_code=500,
        )

    payload = {
        "code": code,
        "state": skill_id,
        # Unix epoch so the agent can detect stale callbacks (>15 min)
        # and decline rather than trying to exchange a definitely-
        # expired code at the provider.
        "captured_at": now,
    }
    target = _callback_dir() / "oauth_callback.json"
    try:
        _atomic_write_0600(target, json.dumps(payload, indent=2))
    except OSError as exc:
        logger.exception("oauth callback failed to write %s: %s", target, exc)
        return HTMLResponse(
            _error_page("ODS caught the redirect but couldn't hand the code back to your assistant. The operator might need to check filesystem permissions on data/persona/."),
            status_code=500,
        )

    logger.info("oauth callback captured for skill=%s (code length %d)", skill_id, len(code))
    return HTMLResponse(_success_page(skill_id, bound_return_url))


@router.get("/api/oauth/pending")
async def oauth_pending(api_key: str = Depends(verify_api_key)):
    """Convenience endpoint the agent or operator can poll to find out
    whether an OAuth callback has arrived but not yet been consumed. The
    agent normally reads the file directly via its filesystem tools, but
    this endpoint is useful for debugging from a browser or curl.
    """
    target = _callback_dir() / "oauth_callback.json"
    if not target.exists():
        return {"pending": False}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"pending": False, "error": f"could not read callback file: {exc}"}
    age = max(0, int(time.time()) - int(payload.get("captured_at", 0)))
    return {
        "pending": True,
        "state": payload.get("state"),
        "captured_at": payload.get("captured_at"),
        "age_seconds": age,
        "stale": age > 900,  # codes typically expire ~10 min at the provider
    }


@router.get("/api/oauth/providers")
async def oauth_providers(api_key: str = Depends(verify_api_key)):
    """Report OAuth provider bootstrap readiness without exposing secrets."""
    registry = _load_provider_registry()
    providers = []
    for raw_provider in registry.get("providers", []):
        if not isinstance(raw_provider, dict):
            continue
        configured, found_files = _credential_status(raw_provider)
        providers.append(
            {
                "id": raw_provider.get("id"),
                "name": raw_provider.get("name"),
                "skill_id": raw_provider.get("skill_id"),
                "flow": raw_provider.get("flow"),
                "configured": configured,
                "credential_files": raw_provider.get("credential_files", []),
                "found_credentials": found_files,
                "redirect_uris": raw_provider.get("redirect_uris", []),
                "requires_provider_verification": bool(raw_provider.get("requires_provider_verification", False)),
                "notes": raw_provider.get("notes", ""),
            }
        )
    return {
        "schema_version": registry.get("schema_version", "ods.oauth-providers.v1"),
        "registry_available": "error" not in registry,
        "error": registry.get("error"),
        "credential_roots": [path.name for path in _credential_roots()],
        "providers": providers,
    }
