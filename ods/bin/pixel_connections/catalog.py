"""Connectors ODS ships, as data: what each one can do and what it asks Google for.

A capability is something the owner turns on. ``available`` says whether this
ODS release can deliver it yet; a capability that is not available is shown as
coming later and is never requested from the provider.
"""
from __future__ import annotations

import copy
from typing import Any

CALENDAR_READ_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_READ_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"

GOOGLE: dict[str, Any] = {
    "id": "google",
    "name": "Google",
    "summary": "Calendar and Gmail",
    "kinds": ["source", "actuator"],
    "platforms": ["linux-systemd", "wsl-systemd"],
    "auth": {"type": "oauth2-pkce", "client": "owner-desktop-app", "redirects": ["loopback", "paste"]},
    "capabilities": [
        {
            "id": "calendar.read", "label": "Read your calendar", "effect": "read",
            "scopes": [CALENDAR_READ_SCOPE], "tools": ["pixel_calendar_list", "pixel_calendar_get"],
            "default": True, "available": True, "requires": [],
        },
        {
            "id": "calendar.write", "label": "Suggest calendar changes you approve", "effect": "external-write",
            "scopes": [CALENDAR_WRITE_SCOPE],
            "tools": ["pixel_calendar_propose_create", "pixel_calendar_propose_update", "pixel_calendar_propose_delete"],
            "default": False, "available": True, "requires": ["calendar.read"],
        },
        {
            "id": "calendar.direct", "label": "Add or move your own private events without asking",
            "effect": "private-write", "scopes": [CALENDAR_WRITE_SCOPE], "tools": [],
            "default": False, "available": True, "requires": ["calendar.write"],
        },
        {
            "id": "gmail.read", "label": "Read email summaries", "effect": "read",
            "scopes": [GMAIL_READ_SCOPE],
            "tools": ["pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read",
                      "pixel_gmail_thread"],
            "default": True, "available": True, "requires": [],
        },
        {
            "id": "gmail.drafts", "label": "Write Gmail drafts you send yourself", "effect": "draft",
            "scopes": [GMAIL_COMPOSE_SCOPE], "tools": [], "default": False, "available": False,
            "requires": ["gmail.read"],
        },
    ],
    # Decision 7: the primary calendar, and Inbox plus Sent metadata for 180 days.
    "data": {"calendarId": "primary", "calendarPastDays": 7, "calendarFutureDays": 90, "gmailWindowDays": 180},
}

CONNECTORS = (GOOGLE,)


def connector(connector_id: str) -> dict[str, Any]:
    for entry in CONNECTORS:
        if entry["id"] == connector_id:
            return entry
    raise KeyError(connector_id)


def public_catalog() -> list[dict[str, Any]]:
    """A copy for Settings; callers can never change the shipped catalog."""
    return copy.deepcopy(list(CONNECTORS))


def scopes_for(connector_id: str, capabilities: dict[str, bool]) -> list[str]:
    """Scopes to request for the capabilities the owner turned on, nothing more."""
    scopes: list[str] = []
    for capability in connector(connector_id)["capabilities"]:
        if capabilities.get(capability["id"]) and capability["available"]:
            scopes.extend(scope for scope in capability["scopes"] if scope not in scopes)
    return scopes
