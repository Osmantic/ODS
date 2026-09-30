"""Owner-declared integrations that ODS does not run, and their reachability.

A custom integration is a system the owner relies on but ODS does not manage:
a hosted decision API, a data engine on the LAN, a bespoke service on this
host. The owner records a name and a health URL. ODS sends one plain GET to
that URL and keeps only the outcome: a status class, the HTTP code, the
latency and when it checked.

The check is deliberately narrow so it cannot become a way to read or act on
other systems:

* response bodies are never read, stored or returned;
* redirects are not followed;
* no credentials are stored or sent (URLs with a user name, password, query
  string or fragment are refused, because those are where tokens usually sit);
* link-local, multicast and unspecified addresses - including cloud metadata
  endpoints - are refused both as literals and after DNS resolution, and the
  connection uses exactly the addresses that passed that check.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver

SCHEMA_VERSION = 1
MAX_INTEGRATIONS = 25
MAX_FILE_BYTES = 64 * 1024
NAME_MAX = 60
NOTES_MAX = 280
URL_MAX = 512
CHECK_TIMEOUT_SECONDS = 5.0
STALE_AFTER_SECONDS = 60.0
USER_AGENT = "ODS-Integrations/1"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Specific metadata addresses outside the ranges refused wholesale below.
_BLOCKED_EXACT = frozenset({
    ipaddress.ip_address("fd00:ec2::254"),   # AWS IPv6 instance metadata
    ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud instance metadata
})


class IntegrationError(ValueError):
    """A request the owner can correct; the message is safe to show."""


class IntegrationConflict(IntegrationError):
    """The request collides with the saved list (duplicate or limit)."""


class IntegrationStoreUnreadable(RuntimeError):
    """The saved list exists but cannot be trusted; it is never overwritten."""


class BlockedAddressError(OSError):
    """The destination resolves to an address the check refuses to contact."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _embedded_ipv4(address: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    """The IPv4 destination an IPv6 form would actually reach, if any."""
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address.teredo is not None:
        return address.teredo[1]
    if address in _NAT64:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _address_blocked(address: ipaddress._BaseAddress) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(address)
        if embedded is not None and _address_blocked(embedded):
            return True
    return (
        address in _BLOCKED_EXACT
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv4Address) and address.is_reserved)
    )


def _literal_address(host: str) -> Optional[ipaddress._BaseAddress]:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


_NUMERIC_LABEL = re.compile(r"^(?:0x[0-9a-f]*|[0-9]+)$", re.IGNORECASE)


def _ambiguous_numeric_host(host: str) -> bool:
    """True for numeric hosts that are not a canonical IP literal.

    "2852039166", "0251.0376.0251.0376", "127.1" or "169.254.169.254." all
    reach an IPv4 address through getaddrinfo, and aiohttp treats every
    all-digit host as an address and skips the resolver, so they would escape
    the address check. Only the canonical dotted form is accepted.
    """
    if _literal_address(host) is not None:
        return False
    labels = host.split(".")
    if labels and labels[-1] == "":
        labels = labels[:-1]
    return bool(labels) and all(_NUMERIC_LABEL.fullmatch(label) for label in labels)


# --- Validation -------------------------------------------------------------

def validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise IntegrationError("Give the integration a name.")
    name = " ".join(value.split())
    if _CONTROL_RE.search(name):
        raise IntegrationError("The name contains control characters.")
    if not name:
        raise IntegrationError("Give the integration a name.")
    if len(name) > NAME_MAX:
        raise IntegrationError(f"Keep the name to {NAME_MAX} characters or fewer.")
    return name


def validate_notes(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise IntegrationError("Notes must be text.")
    notes = " ".join(value.split())
    if _CONTROL_RE.search(notes):
        raise IntegrationError("Notes contain control characters.")
    if len(notes) > NOTES_MAX:
        raise IntegrationError(f"Keep notes to {NOTES_MAX} characters or fewer.")
    return notes


def validate_health_url(value: Any) -> str:
    """Return the canonical health URL or raise IntegrationError."""
    if not isinstance(value, str) or not value.strip():
        raise IntegrationError("Enter the health URL ODS should check.")
    url = value.strip()
    if len(url) > URL_MAX:
        raise IntegrationError(f"Keep the health URL to {URL_MAX} characters or fewer.")
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise IntegrationError("The health URL contains spaces, backslashes or control characters.")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise IntegrationError("The health URL is not a valid URL.") from None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise IntegrationError("The health URL must start with http:// or https://.")
    if not parts.hostname:
        raise IntegrationError("The health URL needs a host name or IP address.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise IntegrationError(
            "Remove the user name or password from the URL. ODS does not store credentials for integrations."
        )
    if parts.query or parts.fragment:
        raise IntegrationError(
            "Remove the query string (?…) or fragment (#…). They often carry tokens, and ODS does not "
            "store credentials for integrations."
        )
    try:
        port = parts.port
    except ValueError:
        raise IntegrationError("The health URL has an invalid port.") from None
    if port == 0:
        raise IntegrationError("The health URL has an invalid port.")
    if _ambiguous_numeric_host(parts.hostname):
        raise IntegrationError("Write an IP address in the usual dotted form, for example 192.168.1.20.")
    literal = _literal_address(parts.hostname)
    if literal is not None and _address_blocked(literal):
        raise IntegrationError(
            "Link-local, multicast and unspecified addresses (such as cloud metadata endpoints) cannot be checked."
        )
    return urlunsplit((scheme, parts.netloc, parts.path or "/", "", ""))


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return slug or "integration"


# --- Reachability check -----------------------------------------------------

class _VettedResolver(AbstractResolver):
    """Resolve once, refuse blocked addresses, and connect only to vetted ones."""

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        loop = asyncio.get_running_loop()
        records = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_STREAM)
        results = []
        for resolved_family, _, protocol, _, address in records:
            if resolved_family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            literal = _literal_address(str(address[0]))
            if literal is None or _address_blocked(literal):
                raise BlockedAddressError("the host resolves to a link-local, multicast or metadata address")
            results.append({
                "hostname": host, "host": str(address[0]), "port": port,
                "family": resolved_family, "proto": protocol,
                "flags": socket.AI_NUMERICHOST | socket.AI_NUMERICSERV,
            })
        if not results:
            raise OSError(f"{host} did not resolve")
        return results

    async def close(self) -> None:
        return None


def _classify_http(code: int) -> tuple[str, str]:
    if 200 <= code < 300:
        return "healthy", f"Answered HTTP {code}"
    if 300 <= code < 400:
        return "degraded", f"Redirected (HTTP {code}); ODS does not follow redirects, so enter the final health URL"
    if code in {401, 403}:
        return "degraded", f"Reachable but refused (HTTP {code}); use an endpoint that answers without credentials"
    return "unhealthy", f"Answered HTTP {code}"


def _connector_detail(error: BaseException) -> str:
    cause = getattr(error, "os_error", None) or error.__cause__ or error
    for candidate in (error, cause):
        if isinstance(candidate, BlockedAddressError):
            return f"Refused: {candidate}"
    if isinstance(cause, socket.gaierror) or "resolve" in str(cause).lower():
        return "Host name could not be resolved"
    if isinstance(cause, ConnectionRefusedError):
        return "Connection refused"
    return "Could not connect"


async def probe_health_url(url: str, *, timeout: float = CHECK_TIMEOUT_SECONDS) -> dict[str, Any]:
    """GET *url* once and describe only the outcome (never the body)."""
    started = time.monotonic()
    checked_at = _iso(_now())
    code: Optional[int] = None
    try:
        # aiohttp skips the resolver for anything that looks like an address,
        # so vet those forms here too.
        host = urlsplit(url).hostname or ""
        if _ambiguous_numeric_host(host):
            raise BlockedAddressError("numeric host names must be written as a dotted IP address")
        literal = _literal_address(host)
        if literal is not None and _address_blocked(literal):
            raise BlockedAddressError("link-local, multicast or metadata address")
        connector = aiohttp.TCPConnector(resolver=_VettedResolver(), force_close=True, limit=1)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=timeout),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        ) as session:
            async with session.get(url, allow_redirects=False) as response:
                code = response.status
        status, detail = _classify_http(code)
    except asyncio.TimeoutError:
        status, detail = "down", f"No answer within {timeout:g} s"
    except aiohttp.ClientConnectorCertificateError:
        status, detail = "down", "TLS certificate was not accepted"
    except aiohttp.ClientSSLError:
        status, detail = "down", "TLS handshake failed"
    except aiohttp.ClientConnectorError as error:
        status, detail = "down", _connector_detail(error)
    except BlockedAddressError as error:
        status, detail = "down", _connector_detail(error)
    except (aiohttp.ClientError, OSError, ValueError):
        status, detail = "down", "Could not connect"
    return {
        "status": status,
        "detail": detail,
        "httpStatus": code,
        "latencyMs": round((time.monotonic() - started) * 1000),
        "checkedAt": checked_at,
    }


# --- Persistence ------------------------------------------------------------

def _validate_record(record: Any) -> dict[str, str]:
    if not isinstance(record, dict) or set(record) != {"id", "name", "url", "notes", "createdAt"}:
        raise ValueError("invalid integration record")
    if not isinstance(record["id"], str) or not _ID_RE.fullmatch(record["id"]):
        raise ValueError("invalid integration id")
    if not isinstance(record["createdAt"], str) or len(record["createdAt"]) > 40:
        raise ValueError("invalid integration timestamp")
    return {
        "id": record["id"],
        "name": validate_name(record["name"]),
        "url": validate_health_url(record["url"]),
        "notes": validate_notes(record["notes"]),
        "createdAt": record["createdAt"],
    }


def read_integrations(path: Path) -> list[dict[str, str]]:
    path = Path(path)
    if path.is_symlink():
        raise IntegrationStoreUnreadable("The integrations file is a symbolic link.")
    if not path.exists():
        return []
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("unexpected integrations file")
        document = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or set(document) != {"schemaVersion", "integrations"}
                or document["schemaVersion"] != SCHEMA_VERSION
                or not isinstance(document["integrations"], list)
                or len(document["integrations"]) > MAX_INTEGRATIONS):
            raise ValueError("unexpected integrations document")
        records = [_validate_record(record) for record in document["integrations"]]
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise IntegrationStoreUnreadable(
            "The saved integrations list could not be read; it was left untouched."
        ) from error
    if len({record["id"] for record in records}) != len(records):
        raise IntegrationStoreUnreadable("The saved integrations list has duplicate entries; it was left untouched.")
    return records


def write_integrations(path: Path, records: list[dict[str, str]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise IntegrationStoreUnreadable("The integrations location is a symbolic link.")
    descriptor, temporary = tempfile.mkstemp(prefix=".custom-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schemaVersion": SCHEMA_VERSION, "integrations": records}, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# --- Store ------------------------------------------------------------------

Probe = Callable[[str], Awaitable[dict[str, Any]]]


class CustomIntegrationStore:
    """Serialises edits and coalesces checks for one integrations file.

    Check results live in memory only; after a restart the first read checks
    everything again, so "last checked" is always a real observation.
    """

    def __init__(self, path: Path, probe: Optional[Probe] = None,
                 stale_after: float = STALE_AFTER_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        self.path = Path(path)
        self._probe = probe or probe_health_url
        self._stale_after = stale_after
        self._clock = clock
        self._edit_lock = asyncio.Lock()
        self._check_lock = asyncio.Lock()
        # id -> (url, monotonic time of the check, result)
        self._checks: dict[str, tuple[str, float, dict[str, Any]]] = {}

    def _last_check(self, record: dict[str, str]) -> Optional[tuple[float, dict[str, Any]]]:
        saved = self._checks.get(record["id"])
        if saved is None or saved[0] != record["url"]:
            return None
        return saved[1], saved[2]

    def _present(self, record: dict[str, str]) -> dict[str, Any]:
        last = self._last_check(record)
        return {**record, "check": dict(last[1]) if last else None}

    async def _run_checks(self, records: list[dict[str, str]], *, force: bool) -> None:
        async with self._check_lock:
            # Re-evaluate under the lock so concurrent readers share one probe.
            now = self._clock()
            due = []
            for record in records:
                last = self._last_check(record)
                if force or last is None or now - last[0] >= self._stale_after:
                    due.append(record)
            if not due:
                return
            results = await asyncio.gather(*(self._probe(record["url"]) for record in due),
                                           return_exceptions=True)
            finished = self._clock()
            for record, result in zip(due, results):
                if isinstance(result, BaseException):
                    result = {"status": "down", "detail": "Check failed", "httpStatus": None,
                              "latencyMs": None, "checkedAt": _iso(_now())}
                self._checks[record["id"]] = (record["url"], finished, result)

    async def list(self, *, refresh: str = "stale") -> list[dict[str, Any]]:
        records = await asyncio.to_thread(read_integrations, self.path)
        if refresh == "stale" and records:
            await self._run_checks(records, force=False)
        return [self._present(record) for record in records]

    async def add(self, name: Any, url: Any, notes: Any = None) -> dict[str, Any]:
        name = validate_name(name)
        url = validate_health_url(url)
        notes = validate_notes(notes)
        async with self._edit_lock:
            records = await asyncio.to_thread(read_integrations, self.path)
            if len(records) >= MAX_INTEGRATIONS:
                raise IntegrationConflict(f"ODS tracks up to {MAX_INTEGRATIONS} custom integrations. Remove one first.")
            for record in records:
                if record["url"] == url:
                    raise IntegrationConflict(f"“{record['name']}” already checks this URL.")
            taken = {record["id"] for record in records}
            base = _slug(name)
            identifier, suffix = base, 2
            while identifier in taken:
                identifier = f"{base}-{suffix}"
                suffix += 1
            record = {"id": identifier, "name": name, "url": url, "notes": notes, "createdAt": _iso(_now())}
            await asyncio.to_thread(write_integrations, self.path, [*records, record])
            # A previous integration with the same id must not lend its result.
            self._checks.pop(identifier, None)
        await self._run_checks([record], force=True)
        return self._present(record)

    async def remove(self, identifier: str) -> bool:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            return False
        async with self._edit_lock:
            records = await asyncio.to_thread(read_integrations, self.path)
            remaining = [record for record in records if record["id"] != identifier]
            if len(remaining) == len(records):
                return False
            await asyncio.to_thread(write_integrations, self.path, remaining)
            self._checks.pop(identifier, None)
        return True

    async def check(self, identifier: str) -> Optional[dict[str, Any]]:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            return None
        records = await asyncio.to_thread(read_integrations, self.path)
        record = next((item for item in records if item["id"] == identifier), None)
        if record is None:
            return None
        await self._run_checks([record], force=True)
        return self._present(record)
