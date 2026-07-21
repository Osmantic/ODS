#!/usr/bin/env python3
"""
APE — Agent Policy Engine
ODS extension: policy gateway for autonomous agent tool calls.

This is a lightweight Python reimplementation of the APE formal policy engine.
The full engine — including Rocq/Coq formal proofs of conscience predicates G1-G6,
trust algebra, and neurosymbolic runtime — is open-source under AGPL v3:
  https://github.com/latentcollapse/HLX_research_language

Provides:
  POST /verify        — evaluate an action against the active policy
  POST /approve       — grant a pending human-approval decision
  GET  /audit         — tail the audit log
  GET  /policy        — return the active policy (redacted)
  GET  /health        — liveness probe
  GET  /metrics       — decision counters

Intent classes:
  ReadFile            — read/cat/head/tail operations
  WriteFile           — write/append/create operations
  ExecuteCommand      — shell exec, python3, node, etc.
  NetworkFetch        — curl, wget, web_fetch
  SpawnAgent          — sub-agent creation
  Other               — anything else

Default policy (policy.yaml):
  - ExecuteCommand: allowlist of safe commands; deny everything else
  - WriteFile: deny writes outside /home/node/.openclaw/workspace
  - Rate limit: 60 requests/minute per session
  - Windowed limits: per-intent sliding-window caps (5m/1h/1d) that can
    hard-deny or escalate to human approval
  - All decisions logged to audit.jsonl (append-only)

Persistent governance state (issue #1269)
-----------------------------------------
Sliding-window counters, circuit-breaker trip status, the warmup deadline,
and pending human-approval grants are persisted to a single JSON file under
the /data/ape volume (sibling of audit.jsonl). The file survives container
restarts and is mutated under a process-wide lock plus a best-effort
advisory file lock so concurrent requests and sidecar processes do not
corrupt it. Window samples are pruned on every load/save so the on-disk and
in-memory footprint stays bounded.

The /verify response schema is unchanged for existing clients: ``allowed``,
``reason``, ``intent`` and ``decision_id`` are always present and keep their
old meaning. Two OPTIONAL fields are added — ``decision`` (one of
``allow`` / ``deny`` / ``require_approval``) and ``approval_token`` (only set
when ``decision == require_approval``). Clients that ignore unknown fields are
unaffected. STRICT_MODE still raises 403 for policy denials and 429 for hard
rate-limit/circuit-breaker denials; ``require_approval`` is an advisory
escalation and deliberately does NOT raise so the agent framework can route
the call to a human and retry via /approve.
"""

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:  # advisory cross-process locking; absent on some platforms (e.g. Windows)
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - platform dependent
    fcntl = None  # type: ignore

# ── Config ──────────────────────────────────────────────────────────────────

POLICY_FILE = Path(os.environ.get("APE_POLICY_FILE", "/config/policy.yaml"))
AUDIT_LOG   = Path(os.environ.get("APE_AUDIT_LOG",   "/data/ape/audit.jsonl"))
RATE_LIMIT  = int(os.environ.get("APE_RATE_LIMIT_RPM", "60"))
STRICT_MODE = os.environ.get("APE_STRICT_MODE", "false").lower() == "true"
_API_KEY = os.environ.get("APE_API_KEY", "")

# Persistent governance state lives next to the audit log on the /data/ape
# volume so it survives container restarts.
STATE_FILE = Path(os.environ.get(
    "APE_STATE_FILE",
    str(AUDIT_LOG.parent / "state.json"),
))

# Warmup grace period (seconds) after process start during which windowed
# limits and the circuit breaker are not enforced. 0 disables warmup.
WARMUP_SECONDS = int(os.environ.get("APE_WARMUP_SECONDS", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ape")

API_KEY = _API_KEY or secrets.token_hex(32)

if not _API_KEY:
    logger.warning(f"APE_API_KEY not set - auto-generated key: {API_KEY[:16]}... (set APE_API_KEY env var to use a fixed key)")

if not STRICT_MODE:
    logger.warning("WARNING: APE is running in advisory mode. Tool calls are logged but NOT blocked. Set APE_STRICT_MODE=true to enforce policies.")

# Named sliding-window tiers. Order matters only for readability; each window
# is evaluated independently.
WINDOW_TIERS: dict[str, int] = {
    "5min": 5 * 60,
    "hour": 60 * 60,
    "day": 24 * 60 * 60,
}

# ── Policy ───────────────────────────────────────────────────────────────────

DEFAULT_POLICY = {
    "version": 1,
    "intents": {
        "ExecuteCommand": {
            "mode": "allowlist",
            "allowed": ["ls", "cat", "grep", "find", "head", "tail", "wc",
                        "echo", "pwd", "env", "which"],
            "deny_patterns": [
                r"rm\s+-rf",      # recursive delete
                r">\s*/dev/sd",   # disk writes
                r"curl.*\|.*sh",  # curl pipe to shell
                r"wget.*\|.*sh",  # wget pipe to shell
                r"chmod\s+[0-7]*7[0-7]*\s+/",  # chmod 777 /...
            ],
        },
        "WriteFile": {
            "mode": "path_guard",
            "allowed_paths": [
                "/home/node/.openclaw/workspace",
                "/tmp",
            ],
        },
        "ReadFile":     {"mode": "allow"},
        "NetworkFetch": {"mode": "allow"},
        "SpawnAgent":   {"mode": "allow"},
        "Other":        {"mode": "allow"},
    },
    "rate_limit": {"requests_per_minute": RATE_LIMIT},
    # Per-intent sliding-window caps. Each entry maps a tier name (see
    # WINDOW_TIERS) to either an int (hard cap → deny) or a mapping
    # {limit: int, action: "deny"|"require_approval"}. Intents without an
    # entry fall back to "default". An empty/absent windowed_limits block
    # disables this layer entirely (legacy behaviour).
    "windowed_limits": {
        "enabled": True,
        "default": {
            "5min": {"limit": 120, "action": "deny"},
            "hour": {"limit": 1000, "action": "deny"},
            "day": {"limit": 5000, "action": "deny"},
        },
        "intents": {
            "ExecuteCommand": {
                "5min": {"limit": 40, "action": "require_approval"},
                "hour": {"limit": 200, "action": "deny"},
                "day": {"limit": 800, "action": "deny"},
            },
            "WriteFile": {
                "5min": {"limit": 60, "action": "require_approval"},
                "hour": {"limit": 400, "action": "deny"},
                "day": {"limit": 1500, "action": "deny"},
            },
            "NetworkFetch": {
                "5min": {"limit": 60, "action": "require_approval"},
                "hour": {"limit": 400, "action": "deny"},
                "day": {"limit": 1500, "action": "deny"},
            },
            "SpawnAgent": {
                "5min": {"limit": 10, "action": "require_approval"},
                "hour": {"limit": 60, "action": "deny"},
                "day": {"limit": 200, "action": "deny"},
            },
        },
    },
    # Circuit breaker: if the share of denied decisions over a rolling window
    # crosses the threshold (with a minimum sample size), the breaker trips
    # and every subsequent request is denied until the cooldown elapses. The
    # tripped state is persisted so a restart does not silently reset it.
    "circuit_breaker": {
        "enabled": True,
        "window_seconds": 300,
        "min_samples": 20,
        "deny_ratio": 0.5,
        "cooldown_seconds": 120,
    },
}

_policy: dict = DEFAULT_POLICY
_policy_mtime: float = 0.0


def load_policy() -> dict:
    global _policy, _policy_mtime
    if not POLICY_FILE.exists():
        return DEFAULT_POLICY
    try:
        mtime = POLICY_FILE.stat().st_mtime
        if mtime == _policy_mtime:
            return _policy
        with open(POLICY_FILE) as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            _policy = loaded
            _policy_mtime = mtime
            logger.info("Policy reloaded from %s", POLICY_FILE)
    except Exception as e:
        logger.warning("Failed to reload policy: %s", e)
    return _policy


# ── Persistent governance state ────────────────────────────────────────────
#
# In-memory shape (also the on-disk JSON shape):
#   {
#     "windows":  { "<scope>|<intent>": { "<tier>": [epoch_ts, ...] } },
#     "breaker":  { "decisions": [[epoch_ts, allowed_bool], ...],
#                   "tripped_until": epoch_or_0 },
#     "approvals": { "<token>": { ...verify-request snapshot... } },
#     "grants":   { "<grant_key>": { ...one-shot bypass record... } },
#     "warmup_until": epoch_or_0,
#   }
#
# A "grant" is a one-shot windowed-limit bypass created when a human accepts a
# require_approval escalation via POST /approve. It is keyed tightly to the
# approved {session, tool, intent, args-hash} so it can only authorise a retry
# of the *same* action. The next /verify whose classified action matches an
# unconsumed grant consumes it (strictly one-shot — deleted on use) and is
# allowed past the exhausted window exactly once; a subsequent retry with no
# new approval escalates again. Grants survive a restart like approvals.
#
# Every public mutator goes through _STATE_LOCK so concurrent FastAPI worker
# threads cannot interleave a read-modify-write. The save path also takes a
# best-effort advisory flock and writes atomically (temp file + os.replace)
# so a sidecar process or a crash mid-write cannot corrupt the file.

_STATE_LOCK = threading.RLock()
_PROCESS_START = time.time()

_state: dict[str, Any] = {
    "windows": {},
    "breaker": {"decisions": [], "tripped_until": 0.0},
    "approvals": {},
    "grants": {},
    "warmup_until": 0.0,
}

# Bound the per-(scope,intent,tier) sample list and the breaker sample list so
# a hostile or buggy caller cannot grow the state file without limit. The cap
# is generous relative to any sane configured limit; pruning by time is the
# primary mechanism, this is the backstop (memory-bounding policy #2786).
_MAX_SAMPLES_PER_WINDOW = 20000
_MAX_BREAKER_SAMPLES = 5000
_MAX_PENDING_APPROVALS = 1000
_MAX_PENDING_GRANTS = 1000


def _empty_state() -> dict[str, Any]:
    return {
        "windows": {},
        "breaker": {"decisions": [], "tripped_until": 0.0},
        "approvals": {},
        "grants": {},
        "warmup_until": 0.0,
    }


def _args_hash(args: dict) -> str:
    """Stable short hash of the call args so a grant is tied to the exact
    invocation that was approved, not just any call to the same tool."""
    try:
        canon = json.dumps(args or {}, sort_keys=True, separators=(",", ":"),
                           default=str)
    except Exception:  # pragma: no cover - non-serialisable args
        canon = repr(args)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _grant_key(session_id: Optional[str], tool_name: str, intent: str,
               args_hash: str) -> str:
    """Tight one-shot-grant key: scope + tool + intent + args fingerprint."""
    scope = session_id or "_global"
    return f"{scope}|{tool_name}|{intent}|{args_hash}"


def _coerce_state(raw: Any) -> dict[str, Any]:
    """Normalise an arbitrary parsed JSON blob into the expected shape."""
    state = _empty_state()
    if not isinstance(raw, dict):
        return state
    windows = raw.get("windows")
    if isinstance(windows, dict):
        for key, tiers in windows.items():
            if not isinstance(tiers, dict):
                continue
            clean: dict[str, list] = {}
            for tier, samples in tiers.items():
                if isinstance(samples, list):
                    clean[str(tier)] = [
                        float(s) for s in samples
                        if isinstance(s, (int, float))
                    ]
            if clean:
                state["windows"][str(key)] = clean
    breaker = raw.get("breaker")
    if isinstance(breaker, dict):
        decisions = breaker.get("decisions")
        if isinstance(decisions, list):
            state["breaker"]["decisions"] = [
                [float(d[0]), bool(d[1])]
                for d in decisions
                if isinstance(d, (list, tuple)) and len(d) == 2
                and isinstance(d[0], (int, float))
            ]
        tu = breaker.get("tripped_until")
        if isinstance(tu, (int, float)):
            state["breaker"]["tripped_until"] = float(tu)
    approvals = raw.get("approvals")
    if isinstance(approvals, dict):
        for tok, rec in list(approvals.items())[:_MAX_PENDING_APPROVALS]:
            if isinstance(rec, dict):
                state["approvals"][str(tok)] = rec
    grants = raw.get("grants")
    if isinstance(grants, dict):
        for gkey, rec in list(grants.items())[:_MAX_PENDING_GRANTS]:
            if isinstance(rec, dict):
                state["grants"][str(gkey)] = rec
    wu = raw.get("warmup_until")
    if isinstance(wu, (int, float)):
        state["warmup_until"] = float(wu)
    return state


def _prune_state(now: float) -> None:
    """Drop expired window samples / breaker samples. Caller holds the lock."""
    longest = max(WINDOW_TIERS.values()) if WINDOW_TIERS else 0
    cutoff_default = now - longest
    dead_keys: list[str] = []
    for key, tiers in _state["windows"].items():
        for tier, samples in list(tiers.items()):
            span = WINDOW_TIERS.get(tier, longest)
            cutoff = now - span
            kept = [s for s in samples if s >= cutoff]
            if len(kept) > _MAX_SAMPLES_PER_WINDOW:
                kept = kept[-_MAX_SAMPLES_PER_WINDOW:]
            if kept:
                tiers[tier] = kept
            else:
                del tiers[tier]
        if not tiers:
            dead_keys.append(key)
    for key in dead_keys:
        del _state["windows"][key]

    cb = _state["breaker"]
    cb_cut = min(cutoff_default, now - 24 * 60 * 60)
    cb["decisions"] = [
        d for d in cb["decisions"] if d[0] >= cb_cut
    ][-_MAX_BREAKER_SAMPLES:]
    if cb.get("tripped_until", 0.0) and cb["tripped_until"] < now:
        cb["tripped_until"] = 0.0

    if len(_state["approvals"]) > _MAX_PENDING_APPROVALS:
        # Drop the oldest pending approvals by issued timestamp.
        items = sorted(
            _state["approvals"].items(),
            key=lambda kv: kv[1].get("issued_at", 0.0),
        )
        for tok, _ in items[:-_MAX_PENDING_APPROVALS]:
            _state["approvals"].pop(tok, None)

    grants = _state.get("grants", {})
    if len(grants) > _MAX_PENDING_GRANTS:
        # Drop the oldest unconsumed grants by grant timestamp (backstop;
        # grants are normally consumed on the very next matching /verify).
        gitems = sorted(
            grants.items(),
            key=lambda kv: kv[1].get("granted_at", 0.0),
        )
        for gkey, _ in gitems[:-_MAX_PENDING_GRANTS]:
            grants.pop(gkey, None)


def load_state() -> None:
    """Load persisted state from disk into memory. Called once at startup."""
    global _state
    with _STATE_LOCK:
        if not STATE_FILE.exists():
            _state = _empty_state()
            return
        try:
            with open(STATE_FILE) as f:
                raw = json.load(f)
            _state = _coerce_state(raw)
            _prune_state(time.time())
            logger.info("Governance state loaded from %s", STATE_FILE)
        except Exception as e:  # corrupt / partial file → start clean, keep file
            logger.warning("Failed to load governance state (%s); starting fresh", e)
            _state = _empty_state()


def save_state() -> None:
    """Atomically persist the current state. Caller need not hold the lock."""
    with _STATE_LOCK:
        snapshot = json.dumps(_state, separators=(",", ":"))
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + f".tmp.{os.getpid()}")
        with open(tmp, "w") as f:
            if fcntl is not None:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                except Exception:  # pragma: no cover - best effort
                    pass
            f.write(snapshot)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.warning("Governance state save failed: %s", e)


# ── Windowed rate limiting ─────────────────────────────────────────────────

# Legacy minute-window limiter (kept verbatim for the requests_per_minute
# policy knob so existing deployments behave identically).
_session_request_times: dict[str, deque] = {}


def check_rate_limit(policy: dict, session_id: Optional[str]) -> bool:
    """Return True if the request is within the legacy per-minute limit."""
    limit = policy.get("rate_limit", {}).get("requests_per_minute", RATE_LIMIT)
    key = session_id or "_global"
    if key not in _session_request_times:
        _session_request_times[key] = deque()
    times = _session_request_times[key]
    now = time.monotonic()
    cutoff = now - 60.0
    while times and times[0] < cutoff:
        times.popleft()
    if len(times) >= limit:
        return False
    times.append(now)
    return True


def _tier_spec(raw: Any) -> Optional[tuple[int, str]]:
    """Normalise a configured tier value to (limit, action) or None."""
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return None
    if isinstance(raw, int):
        return (raw, "deny")
    if isinstance(raw, dict):
        limit = raw.get("limit")
        if isinstance(limit, bool) or not isinstance(limit, int):
            return None
        action = raw.get("action", "deny")
        if action not in ("deny", "require_approval"):
            action = "deny"
        return (limit, action)
    return None


def _intent_window_config(policy: dict, intent: str) -> dict[str, Any]:
    wl = policy.get("windowed_limits", {})
    if not isinstance(wl, dict) or not wl.get("enabled", True):
        return {}
    intents = wl.get("intents", {})
    if isinstance(intents, dict) and intent in intents and isinstance(intents[intent], dict):
        return intents[intent]
    default = wl.get("default", {})
    return default if isinstance(default, dict) else {}


def check_windowed_limits(
    policy: dict, session_id: Optional[str], intent: str, now: float,
) -> tuple[str, str]:
    """Evaluate sliding-window caps for (scope, intent).

    Returns (decision, reason) where decision is one of:
      "allow"            — within all configured tiers (sample recorded)
      "deny"             — a hard-cap tier exceeded (NO sample recorded)
      "require_approval" — an approval-tier exceeded (NO sample recorded)

    On a non-allow outcome the request is NOT counted, so a single offending
    call cannot poison the window for the whole period.
    """
    cfg = _intent_window_config(policy, intent)
    if not cfg:
        return ("allow", "no windowed limits configured")

    scope = session_id or "_global"
    key = f"{scope}|{intent}"

    with _STATE_LOCK:
        tiers = _state["windows"].setdefault(key, {})
        # First pass: evaluate every configured tier against pruned samples.
        worst: Optional[tuple[str, str]] = None
        for tier_name, span in WINDOW_TIERS.items():
            spec = _tier_spec(cfg.get(tier_name))
            if spec is None:
                continue
            limit, action = spec
            samples = tiers.get(tier_name, [])
            cutoff = now - span
            samples = [s for s in samples if s >= cutoff]
            tiers[tier_name] = samples
            if len(samples) >= limit:
                reason = (
                    f"{intent} exceeded {tier_name} window "
                    f"({len(samples)}/{limit})"
                )
                if action == "deny":
                    # Hard deny always wins over an approval escalation.
                    return ("deny", reason)
                if worst is None:
                    worst = ("require_approval", reason)
        if worst is not None:
            return worst

        # Within all tiers → record the sample under every configured tier.
        for tier_name in WINDOW_TIERS:
            if _tier_spec(cfg.get(tier_name)) is None:
                continue
            tiers.setdefault(tier_name, []).append(now)
        return ("allow", "within windowed limits")


def consume_grant(
    session_id: Optional[str], tool_name: str, intent: str, args: dict,
) -> Optional[dict[str, Any]]:
    """Atomically consume a one-shot approval grant for this exact action.

    Returns the consumed grant record (so the caller can audit it) or None if
    no matching unconsumed grant exists. The grant is deleted on consumption —
    it authorises exactly ONE retry past the exhausted window. A second retry
    finds no grant and re-escalates to require_approval.
    """
    gkey = _grant_key(session_id, tool_name, intent, _args_hash(args))
    with _STATE_LOCK:
        grants = _state.setdefault("grants", {})
        return grants.pop(gkey, None)


def record_window_sample(
    policy: dict, session_id: Optional[str], intent: str, now: float,
) -> None:
    """Record one window sample for (scope, intent) without re-evaluating the
    caps. Used after a one-shot grant is consumed so the approved retry is
    still counted (it is not a free call) and the audit trail stays accurate.
    """
    cfg = _intent_window_config(policy, intent)
    if not cfg:
        return
    scope = session_id or "_global"
    key = f"{scope}|{intent}"
    with _STATE_LOCK:
        tiers = _state["windows"].setdefault(key, {})
        for tier_name in WINDOW_TIERS:
            if _tier_spec(cfg.get(tier_name)) is None:
                continue
            tiers.setdefault(tier_name, []).append(now)


# ── Circuit breaker ─────────────────────────────────────────────────────────

def circuit_breaker_blocked(policy: dict, now: float) -> tuple[bool, str]:
    """Return (blocked, reason). Caller-independent; takes the state lock."""
    cb = policy.get("circuit_breaker", {})
    if not isinstance(cb, dict) or not cb.get("enabled", False):
        return (False, "")
    with _STATE_LOCK:
        tripped_until = _state["breaker"].get("tripped_until", 0.0)
        if tripped_until and now < tripped_until:
            return (True, f"circuit breaker open (cooldown until "
                          f"{datetime.fromtimestamp(tripped_until, timezone.utc).isoformat()})")
        if tripped_until and now >= tripped_until:
            _state["breaker"]["tripped_until"] = 0.0
    return (False, "")


def record_breaker_decision(policy: dict, allowed: bool, now: float) -> None:
    """Feed a decision into the breaker; trip it if the deny ratio is high."""
    cb = policy.get("circuit_breaker", {})
    if not isinstance(cb, dict) or not cb.get("enabled", False):
        return
    window = float(cb.get("window_seconds", 300))
    min_samples = int(cb.get("min_samples", 20))
    deny_ratio = float(cb.get("deny_ratio", 0.5))
    cooldown = float(cb.get("cooldown_seconds", 120))
    with _STATE_LOCK:
        decisions = _state["breaker"]["decisions"]
        decisions.append([now, bool(allowed)])
        cutoff = now - window
        decisions = [d for d in decisions if d[0] >= cutoff][-_MAX_BREAKER_SAMPLES:]
        _state["breaker"]["decisions"] = decisions
        if len(decisions) >= min_samples:
            denied = sum(1 for _, ok in decisions if not ok)
            if denied / len(decisions) >= deny_ratio:
                _state["breaker"]["tripped_until"] = now + cooldown
                logger.warning(
                    "Circuit breaker TRIPPED: %d/%d denied over %.0fs window",
                    denied, len(decisions), window,
                )


def in_warmup(now: float) -> bool:
    """True while inside the configured warmup grace period."""
    if WARMUP_SECONDS <= 0:
        return False
    with _STATE_LOCK:
        deadline = _state.get("warmup_until", 0.0)
        if not deadline:
            deadline = _PROCESS_START + WARMUP_SECONDS
            _state["warmup_until"] = deadline
    return now < deadline


# ── Intent classification ─────────────────────────────────────────────────────

_EXEC_VERBS = {"exec", "run", "execute", "shell", "bash", "sh", "cmd"}
_READ_VERBS  = {"read", "cat", "head", "tail", "get_file", "read_file", "view"}
_WRITE_VERBS = {"write", "create", "append", "write_file", "save", "put"}
_NET_VERBS   = {"fetch", "curl", "wget", "web_fetch", "http_get", "request"}
_SPAWN_VERBS = {"spawn", "agent", "sub_agent", "subagent", "delegate"}


def classify_intent(tool_name: str, args: dict) -> str:
    tokens = set(re.split(r"[^a-z0-9]", tool_name.lower()))
    if tokens & _EXEC_VERBS:
        return "ExecuteCommand"
    if tokens & _READ_VERBS:
        return "ReadFile"
    if tokens & _WRITE_VERBS:
        return "WriteFile"
    if tokens & _NET_VERBS:
        return "NetworkFetch"
    if tokens & _SPAWN_VERBS:
        return "SpawnAgent"
    # Infer from args
    if "command" in args or "cmd" in args:
        return "ExecuteCommand"
    if "path" in args or "file" in args:
        return "ReadFile" if args.get("mode", "r") == "r" else "WriteFile"
    if "url" in args:
        return "NetworkFetch"
    return "Other"


# ── Policy evaluation ─────────────────────────────────────────────────────────

def evaluate(intent: str, tool_name: str, args: dict, policy: dict) -> tuple[bool, str]:
    """Return (allowed, reason)."""
    intent_policy = policy.get("intents", {}).get(intent, {"mode": "allow"})
    mode = intent_policy.get("mode", "allow")

    if mode == "allow":
        return True, "allowed by policy"

    if mode == "deny":
        return False, f"{intent} is denied by policy"

    if mode == "allowlist":
        command = args.get("command", args.get("cmd", ""))
        if not command:
            return False, "empty command denied"
        # Check command base name
        base = command.strip().split()[0] if command.strip() else ""
        allowed = intent_policy.get("allowed", [])
        if base not in allowed:
            return False, f"command '{base}' not in allowlist"
        # Check deny patterns
        for pattern in intent_policy.get("deny_patterns", []):
            if re.search(pattern, command):
                return False, f"command matches deny pattern: {pattern}"
        return True, f"command '{base}' is in allowlist"

    if mode == "path_guard":
        path = str(args.get("path", args.get("file", args.get("filename", ""))))
        if not path:
            return True, "no path specified"
        real = os.path.realpath(path)
        allowed_paths = intent_policy.get("allowed_paths", [])
        if any(real == p or real.startswith(p.rstrip("/") + "/") for p in allowed_paths):
            return True, "path is within allowed zone"
        return False, f"write to '{real}' is outside allowed paths"

    return True, f"unknown mode '{mode}', defaulting to allow"


# ── Audit log ─────────────────────────────────────────────────────────────────

def write_audit(entry: dict) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Audit write failed: %s", e)


_decision_counts = {
    "allowed": 0,
    "denied": 0,
    "rate_limited": 0,
    # Additive counters (issue #1269). Existing keys above keep their meaning.
    "require_approval": 0,
    "windowed_denied": 0,
    "circuit_broken": 0,
    "approvals_granted": 0,
    "grants_consumed": 0,
}


# ── App ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    load_state()
    yield


app = FastAPI(
    title="APE — Agent Policy Engine",
    version="1.1.0",
    description="Policy gateway for ODS autonomous agents",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3001", "http://localhost:3000",
                   "http://127.0.0.1:3001", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    # Compared as UTF-8 bytes: compare_digest raises TypeError on non-ASCII
    # str, which would turn an unauthenticated request into a 500 not a 401.
    if API_KEY and not secrets.compare_digest(
        (x_api_key or "").encode("utf-8"), API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


class VerifyRequest(BaseModel):
    tool_name: str
    args: dict[str, Any] = {}
    session_id: Optional[str] = None
    agent_id: Optional[str] = None


class VerifyResponse(BaseModel):
    # Existing fields — unchanged contract for legacy clients.
    allowed: bool
    reason: str
    intent: str
    decision_id: str
    # Additive, OPTIONAL fields (issue #1269). decision == "require_approval"
    # is the third decision tier; approval_token is only set in that case.
    decision: str = "allow"
    approval_token: Optional[str] = None


class ApproveRequest(BaseModel):
    approval_token: str
    approver: Optional[str] = None


class ApproveResponse(BaseModel):
    granted: bool
    reason: str
    tool_name: Optional[str] = None
    intent: Optional[str] = None


@app.get("/health")
async def health():
    return {"status": "ok", "strict_mode": STRICT_MODE,
            "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/verify", response_model=VerifyResponse)
async def verify(req: VerifyRequest, request: Request, api_key: str = Depends(verify_api_key)):
    policy = load_policy()
    decision_id = f"{int(time.time() * 1000)}-{secrets.token_hex(8)}"
    now = time.time()
    client_host = request.client.host if request.client else None
    warming = in_warmup(now)

    # 1) Circuit breaker (hard) — skipped during warmup.
    if not warming:
        broken, cb_reason = circuit_breaker_blocked(policy, now)
        if broken:
            _decision_counts["circuit_broken"] += 1
            _decision_counts["denied"] += 1
            entry = {
                "id": decision_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": req.tool_name,
                "intent": "unknown",
                "allowed": False,
                "decision": "deny",
                "reason": cb_reason,
                "session": req.session_id,
                "agent": req.agent_id,
                "client": client_host,
            }
            write_audit(entry)
            if STRICT_MODE:
                raise HTTPException(status_code=429, detail=cb_reason)
            return VerifyResponse(allowed=False, reason=cb_reason,
                                  intent="unknown", decision_id=decision_id,
                                  decision="deny")

    # 2) Legacy per-minute rate limit — preserved verbatim.
    if not check_rate_limit(policy, req.session_id):
        _decision_counts["rate_limited"] += 1
        entry = {
            "id": decision_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": req.tool_name,
            "intent": "unknown",
            "allowed": False,
            "decision": "deny",
            "reason": "rate limit exceeded",
            "session": req.session_id,
            "agent": req.agent_id,
            "client": client_host,
        }
        write_audit(entry)
        if STRICT_MODE:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        return VerifyResponse(allowed=False, reason="rate limit exceeded",
                              intent="unknown", decision_id=decision_id,
                              decision="deny")

    intent = classify_intent(req.tool_name, req.args)

    # 3) Policy evaluation (allowlist / path guard / allow / deny).
    allowed, reason = evaluate(intent, req.tool_name, req.args, policy)

    decision = "allow" if allowed else "deny"

    # 4) Windowed multi-tier caps — only consulted for policy-allowed calls so
    #    an explicit policy deny is never softened to require_approval.
    grant_used: Optional[dict[str, Any]] = None
    if allowed and not warming:
        w_decision, w_reason = check_windowed_limits(
            policy, req.session_id, intent, now)
        if w_decision != "allow":
            # The window is exhausted. Before escalating again, check for a
            # one-shot bypass grant minted by a prior /approve for THIS exact
            # {session, tool, intent, args}. If present, consume it (strictly
            # one-shot) and allow this single retry past the window. A second
            # retry finds no grant and re-escalates — no broad cap lift.
            grant_used = consume_grant(
                req.session_id, req.tool_name, intent, req.args)
        if grant_used is not None:
            allowed = True
            decision = "allow"
            reason = (
                "one-shot approval grant consumed (approved by "
                f"{grant_used.get('approver') or 'unknown'}); "
                f"original escalation: {w_reason}"
            )
            # Count the approved retry against the window so the bypass is a
            # single extra sample, not a free call that resets nothing.
            record_window_sample(policy, req.session_id, intent, now)
            _decision_counts["grants_consumed"] += 1
        elif w_decision == "deny":
            allowed = False
            decision = "deny"
            reason = w_reason
            _decision_counts["windowed_denied"] += 1
        elif w_decision == "require_approval":
            allowed = False
            decision = "require_approval"
            reason = w_reason

    approval_token: Optional[str] = None
    if decision == "require_approval":
        approval_token = f"appr_{secrets.token_urlsafe(24)}"
        with _STATE_LOCK:
            _state["approvals"][approval_token] = {
                "tool_name": req.tool_name,
                "intent": intent,
                "args_keys": list(req.args.keys()),
                # Args fingerprint so the grant minted on /approve is tied to
                # this exact invocation, not any call to the same tool.
                "args_hash": _args_hash(req.args),
                "session": req.session_id,
                "agent": req.agent_id,
                "reason": reason,
                "issued_at": now,
                "decision_id": decision_id,
            }
        _decision_counts["require_approval"] += 1
    else:
        _decision_counts["allowed" if allowed else "denied"] += 1

    # Circuit breaker observes hard allow/deny outcomes. require_approval is an
    # escalation, not a failure, so it does not feed the breaker.
    if decision != "require_approval" and not warming:
        record_breaker_decision(policy, allowed, now)

    entry = {
        "id": decision_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": req.tool_name,
        "intent": intent,
        "allowed": allowed,
        "decision": decision,
        "reason": reason,
        "args_keys": list(req.args.keys()),
        "session": req.session_id,
        "agent": req.agent_id,
        "client": client_host,
    }
    if approval_token:
        entry["approval_token"] = approval_token
    if grant_used is not None:
        # Mark the approved allow so the audit trail shows it bypassed an
        # exhausted window via a consumed one-shot grant.
        entry["grant_consumed"] = True
        entry["approval_decision_id"] = grant_used.get("decision_id")
        entry["approver"] = grant_used.get("approver")
    write_audit(entry)
    save_state()
    logger.info("%s tool=%s intent=%s decision=%s allowed=%s reason=%s",
                decision_id, req.tool_name, intent, decision, allowed, reason)

    # STRICT_MODE: hard policy denials still raise 403. require_approval is an
    # advisory escalation and intentionally returns 200 so the agent framework
    # can route it to a human and retry via /approve.
    if decision == "deny" and not allowed and STRICT_MODE:
        raise HTTPException(status_code=403, detail=reason)

    return VerifyResponse(allowed=allowed, reason=reason,
                          intent=intent, decision_id=decision_id,
                          decision=decision, approval_token=approval_token)


@app.post("/approve", response_model=ApproveResponse)
async def approve(req: ApproveRequest, request: Request,
                  api_key: str = Depends(verify_api_key)):
    """Grant a pending human-approval decision issued by /verify.

    Consumes the one-shot approval token AND mints a one-shot windowed-limit
    bypass grant keyed tightly to the approved {session, tool, intent, args}.
    The caller retries the original tool call; the very next /verify that
    classifies to the same action consumes the grant and is allowed past the
    exhausted window exactly once (the grant does not permanently lift the
    cap). A second retry with no fresh approval finds no grant and
    re-escalates to require_approval.
    """
    with _STATE_LOCK:
        rec = _state["approvals"].pop(req.approval_token, None)
        if rec is None:
            return ApproveResponse(
                granted=False,
                reason="unknown or already-consumed approval token")
        # Persist a one-shot bypass tightly keyed to the approved action.
        gkey = _grant_key(
            rec.get("session"),
            rec.get("tool_name", ""),
            rec.get("intent", ""),
            rec.get("args_hash", _args_hash({})),
        )
        _state.setdefault("grants", {})[gkey] = {
            "tool_name": rec.get("tool_name"),
            "intent": rec.get("intent"),
            "session": rec.get("session"),
            "agent": rec.get("agent"),
            "args_hash": rec.get("args_hash"),
            "approver": req.approver,
            "decision_id": rec.get("decision_id"),
            "granted_at": time.time(),
        }
    _decision_counts["approvals_granted"] += 1
    entry = {
        "id": rec.get("decision_id"),
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": rec.get("tool_name"),
        "intent": rec.get("intent"),
        "allowed": True,
        "decision": "approved",
        "reason": f"human approval granted by {req.approver or 'unknown'}",
        "session": rec.get("session"),
        "agent": rec.get("agent"),
        "client": request.client.host if request.client else None,
    }
    write_audit(entry)
    save_state()
    logger.info("approval granted token=%s tool=%s approver=%s "
                "(one-shot grant minted)",
                req.approval_token[:12] + "...", rec.get("tool_name"),
                req.approver)
    return ApproveResponse(granted=True, reason="approval granted",
                           tool_name=rec.get("tool_name"),
                           intent=rec.get("intent"))


@app.get("/audit")
async def audit(last_n: int = 50, api_key: str = Depends(verify_api_key)):
    """Return the last N audit log entries."""
    if not AUDIT_LOG.exists():
        return {"entries": []}
    try:
        entries = []
        total_lines = 0
        with open(AUDIT_LOG, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size == 0:
                return {"entries": [], "total": 0}
            chunk_size = 8192
            position = file_size
            lines_found = 0
            while position > 0 and lines_found < last_n + 1:
                chunk_start = max(0, position - chunk_size)
                f.seek(chunk_start)
                chunk = f.read(position - chunk_start)
                lines_found += chunk.count(b'\n')
                position = chunk_start
            f.seek(position)
            for line in f:
                total_lines += 1
                if line.strip():
                    if len(entries) >= last_n:
                        entries.pop(0)
                    entries.append(json.loads(line))
        return {"entries": entries, "total": total_lines}
    except Exception as e:
        return {"entries": [], "error": str(e)}


@app.get("/policy")
async def policy(api_key: str = Depends(verify_api_key)):
    """Return the active policy (args not shown for security)."""
    p = load_policy()
    return {"version": p.get("version", 1),
            "intents": list(p.get("intents", {}).keys()),
            "rate_limit": p.get("rate_limit", {}),
            "windowed_limits": p.get("windowed_limits", {}),
            "circuit_breaker": p.get("circuit_breaker", {}),
            "strict_mode": STRICT_MODE}


@app.get("/metrics")
async def metrics(api_key: str = Depends(verify_api_key)):
    with _STATE_LOCK:
        pending = len(_state["approvals"])
        pending_grants = len(_state.get("grants", {}))
        breaker_open = bool(
            _state["breaker"].get("tripped_until", 0.0) > time.time()
        )
    return {"decisions": _decision_counts,
            "total": sum(_decision_counts.values()),
            "pending_approvals": pending,
            "pending_grants": pending_grants,
            "circuit_breaker_open": breaker_open}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("APE_PORT", "7890"))
    uvicorn.run(app, host="0.0.0.0", port=port)
