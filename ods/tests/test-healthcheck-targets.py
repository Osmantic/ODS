#!/usr/bin/env python3
"""Target-parsing contracts for scripts/healthcheck.py.

The helper documents three accepted target forms:

    healthcheck.py http://localhost:8080/health
    healthcheck.py tcp://localhost:5432
    healthcheck.py localhost:5432

An IPv6 address is written in brackets when it carries a port, so `[::1]:5432`
and `tcp://[::1]:5432` are the IPv6 spellings of the last two. Both have to
reach socket.create_connection() with the bare address — the brackets are
syntax, not part of the host.

Run: python3 tests/test-healthcheck-targets.py
"""

import importlib.util
import pathlib
import sys

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "healthcheck.py"

_spec = importlib.util.spec_from_file_location("ods_healthcheck", SCRIPT)
healthcheck = importlib.util.module_from_spec(_spec)
# Register before executing: @dataclass resolves annotations through
# sys.modules[cls.__module__], which raises if the module is not there yet.
sys.modules["ods_healthcheck"] = healthcheck
_spec.loader.exec_module(healthcheck)

PASSED = 0
FAILED = 0


def check(label, actual, expected):
    global PASSED, FAILED
    if actual == expected:
        print(f"[PASS] {label}")
        PASSED += 1
    else:
        print(f"[FAIL] {label} (expected {expected!r}, got {actual!r})", file=sys.stderr)
        FAILED += 1


def check_raises(label, fn, *args):
    global PASSED, FAILED
    try:
        result = fn(*args)
    except ValueError:
        print(f"[PASS] {label}")
        PASSED += 1
    else:
        print(f"[FAIL] {label} (expected ValueError, got {result!r})", file=sys.stderr)
        FAILED += 1


# ── _parse_target ────────────────────────────────────────────────────────────

check("http URL is an http target",
      healthcheck._parse_target("http://localhost:8080/health"),
      ("http", "http://localhost:8080/health"))

check("https URL is an http target",
      healthcheck._parse_target("https://localhost:8443/health"),
      ("http", "https://localhost:8443/health"))

check("tcp:// scheme is stripped",
      healthcheck._parse_target("tcp://localhost:5432"),
      ("tcp", "localhost:5432"))

check("host:port shorthand is a tcp target",
      healthcheck._parse_target("localhost:5432"),
      ("tcp", "localhost:5432"))

check("bracketed IPv6 shorthand is a tcp target",
      healthcheck._parse_target("[::1]:5432"),
      ("tcp", "[::1]:5432"))

check("tcp:// with a bracketed IPv6 address keeps the brackets for the splitter",
      healthcheck._parse_target("tcp://[fe80::1]:5432"),
      ("tcp", "[fe80::1]:5432"))

check_raises("a bare hostname with no port is rejected",
             healthcheck._parse_target, "localhost")


# ── _parse_host_port ─────────────────────────────────────────────────────────

check("IPv4 host:port splits",
      healthcheck._parse_host_port("127.0.0.1:5432"),
      ("127.0.0.1", 5432))

check("hostname:port splits",
      healthcheck._parse_host_port("localhost:5432"),
      ("localhost", 5432))

check("IPv6 loopback loses its brackets",
      healthcheck._parse_host_port("[::1]:5432"),
      ("::1", 5432))

check("full IPv6 address loses its brackets",
      healthcheck._parse_host_port("[2001:db8::8a2e:370:7334]:8080"),
      ("2001:db8::8a2e:370:7334", 8080))

check_raises("an unterminated IPv6 literal is rejected",
             healthcheck._parse_host_port, "[::1:5432")

check_raises("a bracketed IPv6 address without a port is rejected",
             healthcheck._parse_host_port, "[::1]")

check_raises("an unbracketed IPv6 address is rejected rather than mis-split",
             healthcheck._parse_host_port, "::1")

check_raises("a non-integer port is rejected",
             healthcheck._parse_host_port, "localhost:http")

check_raises("port 0 is rejected",
             healthcheck._parse_host_port, "localhost:0")

check_raises("port 65536 is rejected",
             healthcheck._parse_host_port, "localhost:65536")

check_raises("an empty host is rejected",
             healthcheck._parse_host_port, ":5432")


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
