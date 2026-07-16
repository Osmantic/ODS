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

  1. Agent runs ``setup.py --auth-url`` (via its terminal_tool). The
     ``redirect_uri`` baked into the OAuth client points at this module's
     ``/api/oauth/callback`` route on the operator's ODS host.
  2. Agent sends the auth URL to the user as a markdown link. The user
     taps it, authorises in Google/Spotify/etc., and the provider
     redirects to ``/api/oauth/callback?code=...&state=<skill-id>``.
  3. This handler writes the ``{code, state, ts}`` payload to
     ``data/persona/oauth_callback.json`` (operator-owned, both Hermes
     and dashboard-api can read it) and returns a friendly success page
     the user sees in their browser.
  4. The agent (per persona) checks for the callback file after sending
     the URL — when present, it consumes the code, runs the skill's
     ``setup.py --auth-code <CODE>`` to finalise, deletes the file, and
     confirms to the user.

Why a file rather than calling Hermes directly: dashboard-api can't
docker-exec into the hermes container without docker-in-docker
plumbing, and the hermes container is uid-10000-owned so dashboard-api
can't write into ``/opt/data`` either. ``data/persona/`` is the
operator-owned shared mount (same one the install-context SOUL.md
lives in) that both containers can read.

Security:
  * No authentication on the callback route (it's a redirect target —
    we can't enforce session cookies from a provider redirect). Protection
    comes from the ``state`` parameter the agent passes through, which is
    a randomly-generated nonce stored alongside the pending request.
    The agent should reject any callback whose state doesn't match the
    one it issued.
  * Codes have very short TTLs at the provider (~10 min) so a leaked
    callback file isn't long-exploitable.
  * Codes are single-use — re-exchange attempts fail at the provider.
"""

import html
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])


# In-memory database of pending OAuth flows: {state_nonce: {"skill": skill, "expires_at": expires_at}}
_PENDING_FLOWS: dict[str, dict] = {}
_FLOW_LOCK = threading.Lock()
FLOW_TTL_SECONDS = 900  # 15 minutes


class OAuthRegisterRequest(BaseModel):
    skill: str = Field(..., pattern=r"^[a-zA-Z0-9_-]+$", max_length=100)


class OAuthRegisterResponse(BaseModel):
    state: str
    expires_at: int


def _cleanup_expired_flows_unlocked() -> None:
    """Remove expired entries from the pending store. Call under _FLOW_LOCK."""
    now = int(time.time())
    expired = [k for k, v in _PENDING_FLOWS.items() if v["expires_at"] < now]
    for k in expired:
        del _PENDING_FLOWS[k]


def register_pending_flow(skill: str) -> tuple[str, int]:
    """Generate a secure state nonce and record the pending flow."""
    state = secrets.token_urlsafe(32)
    now = int(time.time())
    expires_at = now + FLOW_TTL_SECONDS
    with _FLOW_LOCK:
        _cleanup_expired_flows_unlocked()
        _PENDING_FLOWS[state] = {
            "skill": skill,
            "expires_at": expires_at
        }
    return state, expires_at


def consume_pending_flow(state: str) -> str | None:
    """Atomically validate and consume a pending flow, returning the trusted skill if valid."""
    now = int(time.time())
    with _FLOW_LOCK:
        _cleanup_expired_flows_unlocked()
        flow = _PENDING_FLOWS.get(state)
        if not flow:
            return None
        del _PENDING_FLOWS[state]
        if flow["expires_at"] < now:
            return None
        return flow["skill"]


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
    pass "/talk" when it wants a button back into ODS Talk.
    """
    candidate = (return_url or "").strip()
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return None
    return candidate


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


@router.post("/api/oauth/pending", response_model=OAuthRegisterResponse)
async def register_oauth_flow(
    payload: OAuthRegisterRequest,
    api_key: str = Depends(verify_api_key)
):
    """Register a pending OAuth flow for a specific skill and get a secure state nonce."""
    state, expires_at = register_pending_flow(payload.skill)
    return OAuthRegisterResponse(state=state, expires_at=expires_at)


@router.get("/api/oauth/callback")
async def oauth_callback(
    code: str = Query("", description="Authorisation code returned by the OAuth provider."),
    state: str = Query("", description="Opaque state token representing the pending OAuth flow."),
    error: str = Query("", description="Set by the provider if the user denied or auth failed."),
    return_url: str = Query("", description="Optional deep link back into ODS Talk after success."),
):
    """OAuth redirect target.

    Validates the state nonce against the pending flow store, consumes it,
    determines the target skill, writes code + state to the legacy file and
    a skill-specific file, then returns a success HTML page.
    """
    if not state:
        return HTMLResponse(_error_page("Missing state parameter."), status_code=400)

    # Validate and consume the state nonce atomically
    skill = consume_pending_flow(state)
    if not skill:
        logger.warning("OAuth callback received with invalid, expired, or reused state: %s", state[:10])
        return HTMLResponse(_error_page("Invalid, expired, or already consumed session state."), status_code=400)

    # Double check skill layout to prevent any downstream path traversal or file write issues
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill):
        logger.warning("OAuth callback rejected due to invalid skill identifier layout: %s", skill[:50])
        return HTMLResponse(_error_page("Invalid skill name structure."), status_code=400)

    if error:
        logger.warning("oauth callback received provider error for skill=%s: %s", skill, error[:200])
        return HTMLResponse(_error_page(f"The provider sent back an error: {error}"), status_code=400)

    if not code:
        return HTMLResponse(_error_page("No authorisation code was returned."), status_code=400)

    payload = {
        "code": code,
        "state": state,
        "captured_at": int(time.time()),
    }

    # Write atomically to both the legacy file and the skill-specific file
    targets = [
        _callback_dir() / "oauth_callback.json",
        _callback_dir() / f"oauth_callback_{skill}.json"
    ]

    for target in targets:
        try:
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                tmp.chmod(0o600)
            except OSError:
                logger.debug("oauth callback could not chmod temp file %s", tmp, exc_info=True)
            tmp.replace(target)
        except OSError as exc:
            logger.exception("oauth callback failed to write %s: %s", target, exc)
            return HTMLResponse(
                _error_page("ODS caught the redirect but couldn't hand the code back to your assistant."),
                status_code=500,
            )

    logger.info("oauth callback captured for skill=%s", skill)
    return HTMLResponse(_success_page(skill, return_url or None))


@router.get("/api/oauth/pending")
async def oauth_pending(
    skill: str = Query("google-workspace", description="The skill identifier to check for a pending callback."),
    api_key: str = Depends(verify_api_key)
):
    """Convenience endpoint the agent or operator can poll to find out
    whether an OAuth callback has arrived but not yet been consumed.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", skill):
        return {"pending": False, "error": "invalid skill format"}

    target = _callback_dir() / f"oauth_callback_{skill}.json"
    if not target.exists():
        # Fallback to legacy file name for compatibility if it matches default
        if skill == "google-workspace":
            legacy = _callback_dir() / "oauth_callback.json"
            if legacy.exists():
                target = legacy
            else:
                return {"pending": False}
        else:
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
