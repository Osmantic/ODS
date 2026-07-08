#!/usr/bin/env python3
"""ODS — universal healthcheck.

Why this exists
--------------
A lot of containers use curl/wget for HEALTHCHECK instructions, but minimal images
(distro-less, scratch-ish, python slim) frequently do not include them.

This script provides a single, dependency-free healthcheck implementation that:
  - Works with *either* HTTP(S) endpoints or raw TCP sockets
  - Supports GET fallback when HEAD is blocked
  - Allows matching on status code ranges and/or response body regex
  - Emits structured output for debugging in CI

Usage
-----
  healthcheck.py http://localhost:8080/health
  healthcheck.py tcp://localhost:5432
  healthcheck.py localhost:5432

Options
-------
  --timeout SECONDS              Overall timeout for the request/connection
  --retries N                    Retry count (with small backoff)
  --method {HEAD,GET}            HTTP method (default: HEAD, with GET fallback)
  --expect-status 200,204,3xx    Allowed HTTP status codes/ranges
  --expect-body-regex REGEX      Regex to match in response body (GET only)
  --user-agent UA                Custom user-agent
  --json                         Emit machine-readable JSON result

Exit codes
----------
  0  Healthy
  1  Unhealthy (check failed)
  2  Usage / invalid input

Notes
-----
- For HTTP checks we prefer HEAD to avoid moving large bodies, but many
  frameworks disable HEAD or route it differently. We automatically fall back
  to GET when HEAD fails with method-related errors.
- For TCP checks we just attempt to connect. This validates listening and basic
  accept() path.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set, Tuple


# -----------------------------
# Data model
# -----------------------------


@dataclass(frozen=True)
class Result:
    ok: bool
    target: str
    kind: str  # http|tcp
    detail: str
    status: Optional[int] = None
    elapsed_ms: Optional[int] = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "ok": self.ok,
                "target": self.target,
                "kind": self.kind,
                "detail": self.detail,
                "status": self.status,
                "elapsed_ms": self.elapsed_ms,
            },
            separators=(",", ":"),
        )


# -----------------------------
# Parsing helpers
# -----------------------------


def _parse_target(raw: str) -> Tuple[str, str]:
    """Return (kind, normalized_target)."""
    if raw.startswith("http://") or raw.startswith("https://"):
        return ("http", raw)

    if raw.startswith("tcp://"):
        return ("tcp", raw[len("tcp://") :])

    # host:port shorthand
    if ":" in raw and not raw.startswith("["):
        return ("tcp", raw)

    raise ValueError("target must be http(s) URL, tcp://host:port, or host:port")


def _parse_host_port(raw: str) -> Tuple[str, int]:
    host, port_s = raw.rsplit(":", 1)
    host = host.strip()
    if not host:
        raise ValueError("host is empty")
    try:
        port = int(port_s)
    except ValueError as exc:
        raise ValueError("port must be an integer") from exc
    if not (1 <= port <= 65535):
        raise ValueError("port out of range (1-65535)")
    return (host, port)


def _parse_expected_status(expr: str) -> Set[int]:
    """Parse '200,204,3xx,401-403' => allowed status codes set."""
    allowed: Set[int] = set()
    for part in (p.strip() for p in expr.split(",") if p.strip()):
        if part.endswith("xx") and len(part) == 3 and part[0].isdigit():
            base = int(part[0]) * 100
            allowed.update(range(base, base + 100))
            continue
        if "-" in part:
            lo_s, hi_s = (x.strip() for x in part.split("-", 1))
            lo = int(lo_s)
            hi = int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            allowed.update(range(lo, hi + 1))
            continue
        allowed.add(int(part))

    if not allowed:
        raise ValueError("--expect-status produced empty set")
    return allowed


# -----------------------------
# Check implementations
# -----------------------------


def check_tcp(host: str, port: int, timeout: float) -> Tuple[bool, str]:
    """Check TCP port is open."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (True, "tcp connect ok")
    except socket.timeout:
        return (False, "tcp connect timeout")
    except ConnectionRefusedError:
        return (False, "tcp connection refused")
    except OSError as exc:
        return (False, f"tcp error: {exc}")


def _http_request(
    url: str,
    *,
    method: str,
    timeout: float,
    user_agent: str,
) -> urllib.response.addinfourl:
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", user_agent)
    return urllib.request.urlopen(req, timeout=timeout)  # nosec B310


def check_http(
    url: str,
    *,
    method: str,
    timeout: float,
    allowed_status: Optional[Set[int]],
    body_regex: Optional[re.Pattern[str]],
    user_agent: str,
) -> Tuple[bool, str, Optional[int]]:
    """Check HTTP endpoint matches expected status and optional body regex."""

    # If a body regex is provided, we must use GET.
    if body_regex is not None:
        method = "GET"

    try_methods: List[str]
    if method.upper() == "HEAD":
        # Prefer HEAD, fallback to GET if HEAD isn't supported.
        try_methods = ["HEAD", "GET"]
    else:
        try_methods = [method.upper()]

    last_err: Optional[str] = None

    for m in try_methods:
        try:
            with _http_request(url, method=m, timeout=timeout, user_agent=user_agent) as resp:
                status = getattr(resp, "status", None)

                # Status validation
                if status is None:
                    return (False, f"http {m}: missing status", None)

                if allowed_status is not None and status not in allowed_status:
                    return (False, f"http {m}: unexpected status {status}", status)

                if body_regex is not None:
                    # Limit body read to avoid memory blowups in bad configs.
                    body = resp.read(1024 * 1024)  # 1 MiB cap
                    try:
                        text = body.decode("utf-8", errors="replace")
                    except Exception:
                        text = str(body)
                    if not body_regex.search(text):
                        return (False, f"http {m}: body regex did not match", status)

                return (True, f"http {m}: ok", status)

        except urllib.error.HTTPError as exc:
            # HTTPError is a valid response with status code; treat via status checks.
            status = getattr(exc, "code", None)
            if allowed_status is not None and status in allowed_status:
                return (True, f"http {m}: ok (error status allowed)", int(status) if status is not None else None)
            last_err = f"http {m}: HTTPError {status}"

            # If HEAD is rejected, allow retry with GET.
            if m == "HEAD" and status in (400, 404, 405, 501):
                continue

            return (False, last_err, int(status) if status is not None else None)

        except urllib.error.URLError as exc:
            last_err = f"http {m}: URLError {exc.reason}"
            continue
        except socket.timeout:
            last_err = f"http {m}: timeout"
            continue

    return (False, last_err or "http: request failed", None)


# -----------------------------
# Retry wrapper
# -----------------------------


def with_retries(fn, *, retries: int, base_sleep: float = 0.15):
    last = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(base_sleep * (1.6 ** (attempt - 1)))
        last = fn()
        ok = last[0]
        if ok:
            return last
    return last


# -----------------------------
# CLI
# -----------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="healthcheck.py", add_help=True)
    p.add_argument("target", help="http(s) URL, tcp://host:port, or host:port")
    p.add_argument("--timeout", type=float, default=5.0, help="Timeout seconds (default: 5)")
    p.add_argument("--retries", type=int, default=1, help="Retry count (default: 1)")
    p.add_argument("--method", default="HEAD", choices=["HEAD", "GET"], help="HTTP method")
    p.add_argument(
        "--expect-status",
        default=None,
        help="Allowed HTTP statuses, e.g. '200,204,3xx,401-403' (default: 200)",
    )
    p.add_argument(
        "--expect-body-regex",
        default=None,
        help="Regex to match in response body (forces GET).",
    )
    p.add_argument(
        "--user-agent",
        default="ODS-Healthcheck/1.0",
        help="User-Agent header",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON result")
    return p.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1

    try:
        kind, norm = _parse_target(args.target)
    except ValueError as exc:
        res = Result(ok=False, target=args.target, kind="unknown", detail=str(exc))
        if args.json:
            print(res.to_json())
        else:
            print(f"[FAIL] {res.detail}")
        return 2

    if args.timeout <= 0:
        res = Result(ok=False, target=args.target, kind=kind, detail="--timeout must be > 0")
        if args.json:
            print(res.to_json())
        else:
            print("[FAIL] --timeout must be > 0")
        return 2

    if args.retries < 0 or args.retries > 50:
        res = Result(ok=False, target=args.target, kind=kind, detail="--retries out of range (0-50)")
        if args.json:
            print(res.to_json())
        else:
            print("[FAIL] --retries out of range")
        return 2

    allowed_status: Optional[Set[int]]
    if kind == "http":
        if args.expect_status is None:
            allowed_status = {200}
        else:
            try:
                allowed_status = _parse_expected_status(args.expect_status)
            except Exception as exc:
                res = Result(ok=False, target=args.target, kind=kind, detail=f"invalid --expect-status: {exc}")
                if args.json:
                    print(res.to_json())
                else:
                    print(f"[FAIL] {res.detail}")
                return 2
    else:
        allowed_status = None

    body_re: Optional[re.Pattern[str]] = None
    if args.expect_body_regex:
        try:
            body_re = re.compile(args.expect_body_regex)
        except re.error as exc:
            res = Result(ok=False, target=args.target, kind=kind, detail=f"invalid regex: {exc}")
            if args.json:
                print(res.to_json())
            else:
                print(f"[FAIL] {res.detail}")
            return 2

    start = time.perf_counter()

    if kind == "tcp":
        try:
            host, port = _parse_host_port(norm)
        except ValueError as exc:
            res = Result(ok=False, target=args.target, kind=kind, detail=str(exc))
            if args.json:
                print(res.to_json())
            else:
                print(f"[FAIL] {res.detail}")
            return 2

        ok, detail = with_retries(lambda: check_tcp(host, port, args.timeout), retries=args.retries)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = Result(ok=bool(ok), target=args.target, kind=kind, detail=str(detail), elapsed_ms=elapsed_ms)

    else:
        ok, detail, status = with_retries(
            lambda: check_http(
                norm,
                method=args.method,
                timeout=args.timeout,
                allowed_status=allowed_status,
                body_regex=body_re,
                user_agent=args.user_agent,
            ),
            retries=args.retries,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        res = Result(
            ok=bool(ok),
            target=args.target,
            kind=kind,
            detail=str(detail),
            status=int(status) if status is not None else None,
            elapsed_ms=elapsed_ms,
        )

    if args.json:
        print(res.to_json())
    else:
        if res.ok:
            status_part = f" status={res.status}" if res.status is not None else ""
            print(f"[PASS] {res.kind} {res.target}{status_part} ({res.elapsed_ms}ms)")
        else:
            status_part = f" status={res.status}" if res.status is not None else ""
            print(f"[FAIL] {res.kind} {res.target}{status_part} ({res.elapsed_ms}ms): {res.detail}")

    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
