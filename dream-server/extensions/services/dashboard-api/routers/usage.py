"""Usage and cost reporting backed by Token Spy telemetry."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from security import verify_api_key

router = APIRouter(prefix="/api/usage", tags=["usage"])

TOKEN_SPY_URL = os.environ.get("TOKEN_SPY_URL", "http://token-spy:8080")
TOKEN_SPY_API_KEY = os.environ.get("TOKEN_SPY_API_KEY", "")
TOKEN_SPY_KEY_FILE = Path(os.environ.get("TOKEN_SPY_KEY_FILE", "/data/token-spy/token-spy-api-key.txt"))
LLAMA_CPP_PROMETHEUS_METRICS = {
    "input_tokens": "llamacpp:prompt_tokens_total",
    "output_tokens": "llamacpp:tokens_predicted_total",
    "requests": "llamacpp:requests_total",
}
_LOCAL_RUNTIME_REQUEST_STATE: dict[str, dict[str, Any]] = {}


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _date_range(start_day: date, end_day: date) -> list[str]:
    days = []
    current = start_day
    while current <= end_day:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _empty_report(start: str, end: str, status: str = "unavailable", detail: str | None = None) -> dict[str, Any]:
    start_day = _parse_date(start)
    end_day = _parse_date(end)
    return {
        "period": {"start": start, "end": end},
        "source": {
            "name": "token-spy",
            "status": status,
            "detail": detail,
        },
        "summary": {
            "spend_usd": 0,
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "tracked_providers": 0,
            "billing_providers": 0,
            "local_providers": 0,
            "untracked_providers": 0,
            "paid_cost_usd": 0,
            "local_cost_usd": 0,
        },
        "daily": [
            {
                "date": day,
                "spend_usd": 0,
                "requests": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
            for day in _date_range(start_day, end_day)
        ],
        "models": [],
        "services": [],
        "sources": [],
    }


def _token_spy_api_key() -> str:
    if TOKEN_SPY_API_KEY:
        return TOKEN_SPY_API_KEY
    try:
        return TOKEN_SPY_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _configured_local_runtime_metrics_urls() -> list[str]:
    explicit = os.environ.get("LOCAL_USAGE_METRICS_URLS") or os.environ.get("LLAMA_METRICS_URL")
    if explicit:
        return [item.strip() for item in explicit.split(",") if item.strip()]

    base = os.environ.get("LLM_API_URL") or os.environ.get("OLLAMA_URL") or "http://llama-server:8080"
    parsed = urllib.parse.urlparse(base.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path in {"/v1", "/api/v1"}:
        path = ""
    target = parsed._replace(path=f"{path}/metrics", params="", query="", fragment="")
    return [urllib.parse.urlunparse(target)]


def _runtime_model_name() -> str:
    return os.environ.get("GGUF_FILE") or os.environ.get("LLM_MODEL") or "llama-server"


async def _fetch_token_spy_report(start: str, end: str) -> dict[str, Any]:
    if not TOKEN_SPY_URL:
        return _empty_report(start, end, detail="TOKEN_SPY_URL is not configured")

    headers = {}
    api_key = _token_spy_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        return await asyncio.to_thread(
            _request_token_spy_report,
            start,
            end,
            headers,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _empty_report(
            start,
            end,
            detail=f"Token Spy returned HTTP {exc.code}: {detail[:160]}",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return _empty_report(start, end, detail=f"Token Spy unavailable: {exc}")


def _request_token_spy_report(start: str, end: str, headers: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode({"start": start, "end": end})
    url = f"{TOKEN_SPY_URL.rstrip('/')}/api/report?{query}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    payload["source"] = {
        "name": "token-spy",
        "status": "ok",
        "detail": None,
    }
    return payload


async def _fetch_local_runtime_counters() -> list[dict[str, Any]]:
    counters = []
    for url in _configured_local_runtime_metrics_urls():
        try:
            metrics_text = await asyncio.to_thread(_request_text, url)
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        parsed = _extract_llama_cpp_prometheus_counters(metrics_text, url)
        if parsed:
            counters.append(parsed)
    return counters


def _request_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8", errors="replace")


def _metric_value(metrics_text: str, metric_name: str) -> float:
    match = re.search(rf"^{re.escape(metric_name)}\s+([0-9.eE+-]+)\s*$", metrics_text, flags=re.MULTILINE)
    if not match:
        return 0
    try:
        return float(match.group(1))
    except ValueError:
        return 0


def _has_metric(metrics_text: str, metric_name: str) -> bool:
    return re.search(rf"^{re.escape(metric_name)}\s+", metrics_text, flags=re.MULTILINE) is not None


def _extract_llama_cpp_prometheus_counters(metrics_text: str, url: str) -> dict[str, Any] | None:
    input_tokens = int(_metric_value(metrics_text, LLAMA_CPP_PROMETHEUS_METRICS["input_tokens"]))
    output_tokens = int(_metric_value(metrics_text, LLAMA_CPP_PROMETHEUS_METRICS["output_tokens"]))
    if input_tokens <= 0 and output_tokens <= 0:
        return None

    parsed = urllib.parse.urlparse(url)
    service = parsed.hostname or "local-runtime"
    if service in {"127.0.0.1", "localhost", "host.docker.internal"}:
        service = "local-runtime"
    request_metric_available = _has_metric(metrics_text, LLAMA_CPP_PROMETHEUS_METRICS["requests"])
    request_count = int(_metric_value(metrics_text, LLAMA_CPP_PROMETHEUS_METRICS["requests"])) if request_metric_available else 0
    request_count_source = "prometheus_counter" if request_metric_available else "unavailable"
    request_count_note = None
    if not request_metric_available:
        observed = _observe_runtime_request_delta(
            key=f"llama.cpp:{service}:{_runtime_model_name()}",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        request_count = observed["requests"]
        request_count_source = observed["source"]
        request_count_note = observed["note"]

    return {
        "runtime": "llama.cpp",
        "adapter": "prometheus",
        "service": service,
        "model": _runtime_model_name(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "requests": request_count,
        "request_count_available": request_metric_available or request_count > 0,
        "request_count_source": request_count_source,
        "request_count_note": request_count_note,
    }


def _observe_runtime_request_delta(key: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    total_tokens = input_tokens + output_tokens
    state = _LOCAL_RUNTIME_REQUEST_STATE.get(key)
    if state is None:
        _LOCAL_RUNTIME_REQUEST_STATE[key] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "requests": 0,
        }
        return {
            "requests": 0,
            "source": "unavailable",
            "note": "llama.cpp did not expose a request counter; baseline initialized from current token counters",
        }

    previous_total = int(state.get("total_tokens") or 0)
    previous_input = int(state.get("input_tokens") or 0)
    previous_output = int(state.get("output_tokens") or 0)
    observed_requests = int(state.get("requests") or 0)
    if total_tokens < previous_total or input_tokens < previous_input or output_tokens < previous_output:
        observed_requests = 0
    elif total_tokens > previous_total:
        observed_requests += 1

    _LOCAL_RUNTIME_REQUEST_STATE[key] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "requests": observed_requests,
    }
    if observed_requests <= 0:
        return {
            "requests": 0,
            "source": "unavailable",
            "note": "llama.cpp did not expose a request counter; no completed request delta observed yet",
        }
    return {
        "requests": observed_requests,
        "source": "observed_counter_delta",
        "note": "llama.cpp did not expose requests_total; count reflects observed token-counter increases while Dashboard API was running",
    }


def _merge_local_runtime_counters(
    report: dict[str, Any],
    start_day: date,
    end_day: date,
    runtime_counters: list[dict[str, Any]],
) -> dict[str, Any]:
    del start_day, end_day
    if not runtime_counters:
        return report

    merged = copy.deepcopy(report)
    source = merged.setdefault("source", {"name": "token-spy", "status": "unknown", "detail": None})
    source["local_runtime"] = {
        "status": "observed",
        "detail": "Cumulative llama.cpp counters observed but not merged into date-bounded totals because Prometheus counters do not include event timestamps",
        "included_in_totals": False,
        "adapters": sorted({str(counter.get("runtime") or "unknown") for counter in runtime_counters}),
        "counters": [
            {
                "runtime": counter.get("runtime") or "unknown",
                "service": counter.get("service") or "local-runtime",
                "model": counter.get("model") or _runtime_model_name(),
                "input_tokens": int(counter.get("input_tokens") or 0),
                "output_tokens": int(counter.get("output_tokens") or 0),
                "requests": int(counter.get("requests") or 0),
                "request_count_source": counter.get("request_count_source") or "unavailable",
            }
            for counter in runtime_counters
        ],
        "request_count_available": any(counter.get("request_count_available") for counter in runtime_counters),
        "request_count_sources": sorted(
            {
                str(counter.get("request_count_source") or "unavailable")
                for counter in runtime_counters
            }
        ),
        "request_count_note": next(
            (
                str(counter.get("request_count_note"))
                for counter in runtime_counters
                if counter.get("request_count_note")
            ),
            None,
        ),
    }
    if source.get("status") != "ok":
        source["status"] = "partial"
        source["detail"] = "Token Spy unavailable; cumulative local runtime counters observed but not merged into the selected date range"
    return merged


@router.get("/report")
async def usage_report(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    api_key: str = Depends(verify_api_key),
):
    """Return real usage/cost metrics for the requested inclusive date range."""
    del api_key
    try:
        start_day = _parse_date(start)
        end_day = _parse_date(end)
    except ValueError:
        return _empty_report(start, end, status="invalid_range", detail="Dates must use YYYY-MM-DD")
    if end_day < start_day:
        return _empty_report(start, end, status="invalid_range", detail="end must be on or after start")

    report = await _fetch_token_spy_report(start, end)
    runtime_counters = await _fetch_local_runtime_counters()
    return _merge_local_runtime_counters(report, start_day, end_day, runtime_counters)
