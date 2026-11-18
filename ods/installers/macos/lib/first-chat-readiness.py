#!/usr/bin/env python3
"""Bounded first-chat route readiness probe for the macOS installer.

Runs inside the dashboard-api container via ``docker exec -i ... python3 -``
so it can read the container's own ``DASHBOARD_API_KEY`` / ``ODS_AGENT_KEY``
without exposing them on argv or in logs. It polls the authenticated host
agent ``/v1/model/status`` to schedule the switchboard's real route proof,
then requires the sanitized ``/api/models/state`` to show a verified active
route whose runtime identity matches the current ``.env`` model, and finally
requires the model-router ``/health`` and ``/v1/models`` to agree on the
same active route and sequence.

Fail-closed: any missing proof, identity mismatch, auth/HTTP failure, or
timeout returns nonzero. Never prints keys, response bodies, or raw env.

Testable without Docker: ``run_probe`` accepts injected ``http_get`` and
``sleep`` callables and a ``clock`` so unit tests can drive delayed proof,
terminal errors, wrong identities, and timeouts deterministically.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_TIMEOUT = 180.0
DEFAULT_INTERVAL = 3.0
MAX_BODY_BYTES = 65536


class ProbeError(RuntimeError):
    """Terminal readiness failure; message is safe to log."""


class RetryProbe(ProbeError):
    """A transient or incomplete observation, safe to retry within the budget."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _HttpResult:
    __slots__ = ("status", "body")

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body


def _http_get(url: str, headers: dict[str, str], timeout: float) -> _HttpResult:
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise RetryProbe("response too large")
            return _HttpResult(response.status, body)
    except urllib.error.HTTPError as exc:
        with exc:
            return _HttpResult(exc.code, b"")
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        raise RetryProbe("HTTP transport failure") from None


def _json_body(result: _HttpResult) -> dict:
    try:
        data = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RetryProbe("response was not valid JSON") from None
    if not isinstance(data, dict):
        raise RetryProbe("response root was not an object")
    return data


def _valid_model_name(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 256 and "\x00" not in value


def _valid_context(value: object) -> bool:
    return type(value) is int and 1 <= value <= 10_000_000


def _valid_seq(value: object) -> bool:
    return type(value) is int and value >= 0


def _identity_tokens(value: object) -> set[str]:
    """Exact, known runtime aliases for one model identity value.

    Mirrors ``_runtime_model_identity_tokens`` in bin/ods-host-agent.py so the
    installer probe and the host agent agree on what counts as the same
    model. Never substring: only the full normalized path and its basename
    (with and without a trailing ``.gguf``) are accepted.
    """
    if not isinstance(value, str):
        return set()
    raw = value.strip()
    if not raw:
        return set()
    variants = {raw}
    lowered = raw.casefold()
    for prefix in ("extra.", "user."):
        if lowered.startswith(prefix):
            variants.add(raw[len(prefix):])
    tokens: set[str] = set()
    for variant in variants:
        normalized = variant.strip().replace("\\", "/").rstrip("/")
        if not normalized:
            continue
        basename = normalized.rsplit("/", 1)[-1]
        for candidate in (normalized, basename):
            folded = candidate.casefold()
            tokens.add(folded)
            if folded.endswith(".gguf"):
                tokens.add(folded[:-5])
    return tokens


def _identity_matches(runtime: object, expected: dict) -> bool:
    actual = _identity_tokens(runtime)
    if not actual:
        return False
    wanted: set[str] = set()
    for key in ("llmModel", "ggufFile", "catalogId"):
        wanted.update(_identity_tokens(expected.get(key)))
    return bool(wanted and actual.intersection(wanted))


def _route_verified(active: object, expected: dict) -> bool:
    if not isinstance(active, dict):
        return False
    if active.get("reconstructed") is True:
        return False
    verified_at = active.get("verifiedAt")
    if not isinstance(verified_at, str) or not verified_at:
        return False
    proof = active.get("proof")
    if not isinstance(proof, dict) or proof.get("completion") is not True:
        return False
    if proof.get("identity") != active.get("runtimeModelId"):
        return False
    if not _valid_context(active.get("contextLength")):
        return False
    if not _valid_seq(active.get("routeSeq")):
        return False
    return _identity_matches(active.get("runtimeModelId"), expected)


def _router_health_ready(health: dict, state: dict) -> bool:
    """Require a healthy router whose route matches the verified state."""
    if health.get("status") != "ok":
        return False
    endpoint_count = health.get("endpointCount")
    if type(endpoint_count) is not int or endpoint_count <= 0:
        return False
    if health.get("hasRoute") is not True:
        return False
    if health.get("modelSwapGateActive") is not False:
        return False
    if not _valid_seq(health.get("seq")) or not _valid_seq(health.get("routeSeq")):
        return False
    if health.get("seq") != state.get("seq"):
        return False
    if health.get("routeSeq") != state.get("routeSeq"):
        return False
    return True


def _router_models_ready(models: dict, state: dict) -> bool:
    """Require /v1/models ods metadata to name the same active route."""
    ods = models.get("ods")
    if not isinstance(ods, dict):
        return False
    active = state.get("active") or {}
    if ods.get("routedModel") != active.get("runtimeModelId"):
        return False
    if ods.get("catalogId") != active.get("catalogId"):
        return False
    if not _valid_seq(ods.get("routeSeq")):
        return False
    if ods.get("routeSeq") != state.get("routeSeq"):
        return False
    if not _valid_context(ods.get("contextLength")):
        return False
    if ods.get("contextLength") != active.get("contextLength"):
        return False
    backend = active.get("backend")
    return isinstance(backend, dict) and ods.get("backend") == backend.get("kind")


def _read_expected_identity(env: dict) -> dict:
    context = None
    for key in ("CTX_SIZE", "MAX_CONTEXT"):
        raw = str(env.get(key) or "")
        if raw.isdigit() and 1 <= int(raw) <= 10_000_000:
            context = int(raw)
            break
    llm_model = env.get("LLM_MODEL")
    gguf_file = env.get("GGUF_FILE")
    return {
        "llmModel": llm_model if _valid_model_name(llm_model) else None,
        "ggufFile": gguf_file if _valid_model_name(gguf_file) else None,
        "catalogId": None,
        "contextSize": context,
    }


def _validate_budget(timeout: float, interval: float) -> None:
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ProbeError("timeout must be a finite positive number")
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        raise ProbeError("interval must be a finite positive number")


def _remaining(deadline: float, clock) -> float:
    return deadline - clock()


def _call(http_get, url: str, headers: dict, deadline: float, clock) -> _HttpResult:
    remaining = deadline - clock()
    if remaining <= 0:
        raise RetryProbe("deadline exceeded before request")
    return http_get(url, headers, min(5.0, remaining))


def run_probe(*, agent_url, dashboard_url, router_url, agent_key, dashboard_key,
              expected, timeout=DEFAULT_TIMEOUT, interval=DEFAULT_INTERVAL,
              http_get=None, sleep=None, clock=None):
    """Accept only a stable verified route observed within the total budget.

    `expected` may be a callback: the installed path re-reads the live model
    identity before and after every observation, including bootstrap promotion.
    """
    http_get = http_get or _http_get
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    _validate_budget(timeout, interval)
    if not agent_key or not dashboard_key:
        raise ProbeError("missing authentication key")
    read_expected = expected if callable(expected) else lambda: dict(expected)
    deadline = clock() + timeout
    agent_headers = {"Authorization": f"Bearer {agent_key}"}
    dashboard_headers = {"Authorization": f"Bearer {dashboard_key}"}

    def get(url, headers, label):
        result = _call(http_get, url, headers, deadline, clock)
        if result.status in (401, 403):
            raise ProbeError(f"{label} rejected authentication")
        if result.status != 200:
            raise RetryProbe(f"{label} HTTP {result.status}")
        return _json_body(result)

    def status():
        data = get(agent_url + "/v1/model/status", agent_headers, "host agent")
        # Idle lifecycleActive is omitted by the host agent; transactionPending
        # is always projected and fails closed when its journal is unreadable.
        if (data.get("lifecycleActive", False) is not False
                or data.get("modelTransactionPending") is not False
                or not isinstance(data.get("status"), str)):
            raise RetryProbe("model lifecycle is not settled")

    def state(identity):
        data = get(dashboard_url + "/api/models/state", dashboard_headers, "dashboard API")
        active = data.get("active")
        if (data.get("valid") is not True or not _route_verified(active, identity)
                or not _valid_seq(data.get("seq")) or not _valid_seq(data.get("routeSeq"))
                or active["routeSeq"] != data["routeSeq"] or data["routeSeq"] > data["seq"]):
            raise RetryProbe("active route not verified for current model")
        return data

    last_reason = "no attempt"
    while clock() < deadline:
        try:
            identity = read_expected()
            if not identity.get("llmModel") and not identity.get("ggufFile"):
                raise ProbeError("no configured model identity in .env")
            status()  # A supported poll schedules the real background proof.
            first = state(identity)
            health = get(router_url + "/health", {}, "router")
            models = get(router_url + "/v1/models", {}, "router")
            if not _router_health_ready(health, first) or not _router_models_ready(models, first):
                raise RetryProbe("router does not match verified route")
            status()
            second = state(identity)
            health_after = get(router_url + "/health", {}, "router")
            if (any(first.get(k) != second.get(k) for k in ("seq", "routeSeq", "active"))
                    or read_expected() != identity
                    or not _router_health_ready(health_after, second)
                    or not isinstance(health.get("instanceId"), str) or not health["instanceId"]
                    or health_after.get("instanceId") != health["instanceId"]):
                raise RetryProbe("model route changed during observation")
            if clock() >= deadline:
                raise RetryProbe("deadline reached during observation")
            return
        except RetryProbe as exc:
            last_reason = str(exc)
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(interval, remaining))
    raise ProbeError(f"first-chat route readiness timed out ({last_reason})")


def _load_env(path: str) -> dict:
    # This dependency-free parser is already shipped in dashboard-api /app.
    from env_values import parse_env_value
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except (OSError, UnicodeError):
        raise ProbeError("live model configuration is unreadable") from None
    values = {}
    for line in lines:
        key, separator, raw = line.partition("=")
        if separator and key in ("LLM_MODEL", "GGUF_FILE", "CTX_SIZE", "MAX_CONTEXT"):
            values[key] = parse_env_value(raw)
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--dashboard-url", required=True)
    parser.add_argument("--router-url", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    args = parser.parse_args(argv)

    agent_key = os.environ.get("ODS_AGENT_KEY") or os.environ.get("DASHBOARD_API_KEY") or ""
    dashboard_key = os.environ.get("DASHBOARD_API_KEY") or ""

    try:
        run_probe(
            agent_url=args.agent_url.rstrip("/"),
            dashboard_url=args.dashboard_url.rstrip("/"),
            router_url=args.router_url.rstrip("/"),
            agent_key=agent_key,
            dashboard_key=dashboard_key,
            expected=lambda: _read_expected_identity(_load_env(args.env_file)),
            timeout=args.timeout,
            interval=args.interval,
        )
    except ProbeError as exc:
        print(f"first-chat readiness failed: {exc}", file=sys.stderr)
        return 1
    print("first-chat route verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
