"""Google sign-in for a connected app, built by the custodian in the access coordinator.

Each owner brings their own Google project, as Pixel's Source Broker expects: a
"Desktop app" OAuth client whose JSON file they download from Google Cloud. The
custodian builds the consent address with PKCE (S256) and a single-use state, and
writes the PKCE verifier only into the broker's private sign-in handoff, so the
code Google returns is useless to anyone else. Google's endpoints are fixed.
Nothing here touches the network or the file system.
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta
import hashlib
import hmac
import re
import secrets
from typing import Any
import urllib.parse

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
# Google names the verified account only in an ID token, which these scopes request.
IDENTITY_SCOPES = ("openid", "email")
FLOW_SECONDS = 15 * 60
CLIENT_ID = re.compile(r"^[0-9]{4,32}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com$")
CLIENT_SECRET = re.compile(r"^[\x21-\x7e]{8,256}$")
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
FLOW_ID = re.compile(r"^[a-f0-9]{32}$")
STATE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CODE = re.compile(r"^[\x21-\x7e]{1,2048}$")
EMAIL = re.compile(r"^[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}$")
CLIENT_KEYS = {"client_id", "project_id", "auth_uri", "token_uri", "auth_provider_x509_cert_url",
               "client_secret", "redirect_uris"}
CALLBACK_KEYS = {"code", "state", "scope", "authuser", "hd", "prompt", "iss",
                 "error", "error_description", "error_uri"}


class OAuthError(ValueError):
    """A sign-in input the owner can fix; ``code`` names the fix, never Google's text."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_client(value: Any) -> dict[str, str]:
    """The Desktop-app client from Google's downloaded JSON, or OAuthError naming what is wrong."""
    if not isinstance(value, dict) or len(value) != 1:
        raise OAuthError("client-invalid")
    if "web" in value:
        raise OAuthError("client-not-desktop")
    installed = value.get("installed")
    if not isinstance(installed, dict) or not set(installed) <= CLIENT_KEYS:
        raise OAuthError("client-invalid")
    client_id, secret, project = (installed.get(key) for key in ("client_id", "client_secret", "project_id"))
    if (not isinstance(client_id, str) or not CLIENT_ID.fullmatch(client_id)
            or not isinstance(secret, str) or not CLIENT_SECRET.fullmatch(secret)
            or not isinstance(project, str) or not PROJECT_ID.fullmatch(project)):
        raise OAuthError("client-invalid")
    if installed.get("auth_uri") != GOOGLE_AUTH_URI or installed.get("token_uri") != GOOGLE_TOKEN_URI:
        raise OAuthError("client-invalid")
    redirects = installed.get("redirect_uris", ["http://localhost"])
    if not isinstance(redirects, list) or "http://localhost" not in redirects:
        raise OAuthError("client-not-desktop")
    return {"clientId": client_id, "clientSecret": secret, "projectId": project}


def client(value: Any) -> dict[str, str]:
    """Validate the already parsed client that crosses the coordinator's socket."""
    if not isinstance(value, dict) or set(value) != {"clientId", "clientSecret", "projectId"}:
        raise OAuthError("client-invalid")
    return parse_client({"installed": {"client_id": value["clientId"], "client_secret": value["clientSecret"],
                                       "project_id": value["projectId"], "auth_uri": GOOGLE_AUTH_URI,
                                       "token_uri": GOOGLE_TOKEN_URI}})


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def new_flow(*, google_client: dict[str, str], scopes: list[str], redirect_port: int, now: datetime,
             login_hint: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A fresh consent flow: the broker's private handoff and what the owner's browser may see.

    The handoff holds the PKCE verifier and client secret and goes only to the broker.
    The public part has no secret: the consent address carries only the S256 challenge.
    """
    if type(redirect_port) is not int or not 1024 <= redirect_port <= 65535:
        raise OAuthError("invalid-redirect")
    if login_hint is not None and (not isinstance(login_hint, str) or not EMAIL.fullmatch(login_hint)):
        raise OAuthError("invalid-account-hint")
    if not scopes or len(set(scopes)) != len(scopes):
        raise OAuthError("invalid-scopes")
    flow_id = secrets.token_hex(16)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    redirect_uri = f"http://127.0.0.1:{redirect_port}"
    expires = now + timedelta(seconds=FLOW_SECONDS)
    handoff = {
        "schemaVersion": 1, "kind": "pixel-source-oauth-flow", "flowId": flow_id, "state": state,
        "codeVerifier": verifier, "redirectUri": redirect_uri,
        "client": {"installed": {"client_id": google_client["clientId"], "client_secret": google_client["clientSecret"],
                                 "auth_uri": GOOGLE_AUTH_URI, "token_uri": GOOGLE_TOKEN_URI}},
        "requestedScopes": list(scopes), "account": login_hint, "createdAt": _iso(now), "expiresAt": _iso(expires),
    }
    query = {
        "client_id": google_client["clientId"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(scopes), "state": state, "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if login_hint:
        query["login_hint"] = login_hint
    public = {"flowId": flow_id, "authUrl": f"{GOOGLE_AUTH_URI}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}",
              "redirectUri": redirect_uri, "expiresAt": _iso(expires), "stateHash": state_hash(state)}
    return handoff, public


def callback_fields(query: str) -> dict[str, str]:
    """``state`` with ``code`` or ``error`` from Google's redirect query, checked for shape only.

    The host agent, which receives the redirect, has no state to compare; root does.
    """
    if not isinstance(query, str) or len(query) > 8192:
        raise OAuthError("invalid-callback")
    try:
        pairs = urllib.parse.parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=12)
    except ValueError:
        raise OAuthError("invalid-callback") from None
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values or key not in CALLBACK_KEYS:
            raise OAuthError("invalid-callback")
        values[key] = value
    state = values.get("state", "")
    if not STATE.fullmatch(state):
        raise OAuthError("invalid-callback")
    if "error" in values:
        return {"state": state, "error": "access-denied" if values["error"] == "access_denied" else "consent-failed"}
    code = values.get("code", "")
    if not CODE.fullmatch(code):
        raise OAuthError("invalid-callback")
    return {"state": state, "code": code}


def parse_callback(query: str, *, expected_state_hash: str) -> dict[str, str]:
    """The callback's fields, only when its state is this flow's."""
    fields = callback_fields(query)
    if not hmac.compare_digest(state_hash(fields["state"]), expected_state_hash):
        raise OAuthError("state-mismatch")
    return fields


LOOPBACK_ADDRESS = re.compile(r"^http://(?:127\.0\.0\.1|localhost):[0-9]{4,5}/?\?(.*)$", re.S)


def pasted_query(text: str) -> str:
    """The query of a sign-in address the owner copied from another device's address bar."""
    if not isinstance(text, str) or len(text) > 8192:
        raise OAuthError("address-not-from-sign-in")
    match = LOOPBACK_ADDRESS.fullmatch(text.strip())
    if match is None:
        raise OAuthError("address-not-from-sign-in")
    return match.group(1)
