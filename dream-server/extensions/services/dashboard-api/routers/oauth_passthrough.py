"""OAuth callback passthrough for agent-driven skill setup.

Hermes Agent ships with per-skill setup scripts (e.g.
``/opt/hermes/skills/productivity/google-workspace/scripts/setup.py``) that
are explicitly designed to be agent-driven — the agent runs ``--auth-url``
to get an OAuth consent link, sends it to the user, the user authorizes in
their browser, and the agent runs ``--auth-code <CODE>`` to finalise.

The problem with that flow on Dream Talk: the user has to manually copy
the OAuth code out of their browser's URL bar and paste it back into the
chat. That's the friction you feel on every setup. The "magic" UX is the
browser redirect coming back to a Dream Server endpoint that captures the
code and hands it to the agent automatically — that's this module.

How it slots in:

  1. Agent runs ``setup.py --auth-url`` (via its terminal_tool). The
     ``redirect_uri`` baked into the OAuth client points at this module's
     ``/api/oauth/callback`` route on the operator's Dream Server host.
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

from __future__ import annotations

import html
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])


def _callback_dir() -> Path:
    """Where dashboard-api writes the captured OAuth callback for the
    agent to consume. ``data/persona/`` is the operator-owned mount that
    Hermes can read (we don't put it in ``data/hermes/`` because that's
    uid 10000 and dashboard-api can't write there).
    """
    # In-container path: dashboard-api mounts ./data → /data, so
    # ./data/persona/ is /data/persona/ from here.
    base = Path(os.environ.get("DREAM_PERSONA_DIR", "/data/persona"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _safe_return_path(return_url: str) -> str | None:
    """Return a same-origin relative path, or None for unsafe links.

    OAuth callbacks are public redirect targets, so never reflect arbitrary
    absolute URLs or javascript: links into the success page. The agent can
    pass "/talk" when it wants a button back into Dream Talk.
    """
    candidate = (return_url or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    return candidate


def _success_page(skill: str, return_url: Optional[str] = None) -> str:
    """The HTML the user sees after authorising. Friendly, clear about
    what just happened, with a button back into Dream Talk if we know
    where to send them."""
    safe_skill = html.escape(skill or "service")
    back_link = ""
    safe_return_path = _safe_return_path(return_url or "")
    if safe_return_path:
        safe_return = html.escape(safe_return_path, quote=True)
        back_link = f'<p><a href="{safe_return}" class="btn">Back to Dream Talk</a></p>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dream Server — authorised</title>
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
  <p>Dream just got access to your {safe_skill} account. You can close this tab and return to the chat — your assistant has picked it up.</p>
  {back_link}
</body>
</html>"""


def _error_page(reason: str) -> str:
    safe = html.escape(reason)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorisation failed</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1.5rem;text-align:center}}</style>
</head><body><h1>Authorisation failed</h1><p>{safe}</p>
<p>Head back to Dream Talk and ask your assistant to try again.</p></body></html>"""


@router.get("/api/oauth/callback")
async def oauth_callback(
    code: str = Query("", description="Authorisation code returned by the OAuth provider."),
    state: str = Query("", description="Opaque state token the agent issued when generating the auth URL. Used to identify which skill the callback belongs to."),
    error: str = Query("", description="Set by the provider if the user denied or auth failed."),
    return_url: str = Query("", description="Optional deep link back into Dream Talk after success."),
):
    """OAuth redirect target.

    Writes the captured ``code`` + ``state`` to a file at
    ``data/persona/oauth_callback.json`` for the agent to consume on its
    next turn, then returns a friendly success page. No JSON response —
    the user lands here via a browser redirect, so HTML is the right
    affordance.

    The agent (per persona) polls for this file after sending the auth
    URL: when it appears, the agent runs the relevant skill's
    ``setup.py --auth-code`` to finalise, deletes the file, and
    confirms to the user.
    """
    if error:
        logger.warning("oauth callback received provider error: %s", error[:200])
        return HTMLResponse(_error_page(f"The provider sent back an error: {error}"), status_code=400)
    if not code:
        return HTMLResponse(_error_page("No authorisation code was returned. You may have denied the request, or the provider's redirect was malformed."), status_code=400)

    skill = state.strip() or "google-workspace"
    payload = {
        "code": code,
        "state": skill,
        # Unix epoch so the agent can detect stale callbacks (>15 min)
        # and decline rather than trying to exchange a definitely-
        # expired code at the provider.
        "captured_at": int(time.time()),
    }
    target = _callback_dir() / "oauth_callback.json"
    try:
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.exception("oauth callback failed to write %s: %s", target, exc)
        return HTMLResponse(
            _error_page("Dream Server caught the redirect but couldn't hand the code back to your assistant. The operator might need to check filesystem permissions on data/persona/."),
            status_code=500,
        )

    logger.info("oauth callback captured for skill=%s (code length %d)", skill, len(code))
    return HTMLResponse(_success_page(skill, return_url or None))


@router.get("/api/oauth/pending")
async def oauth_pending():
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
