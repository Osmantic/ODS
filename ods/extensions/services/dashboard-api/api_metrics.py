"""Low-cardinality Prometheus metrics for the dashboard API."""

import threading
import time
from collections import defaultdict


_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


def _label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class ApiMetrics:
    """Thread-safe process-local request metrics registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._in_flight = 0
        self._requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self._duration_count: dict[tuple[str, str], int] = defaultdict(int)
        self._duration_sum: dict[tuple[str, str], float] = defaultdict(float)
        self._duration_buckets: dict[tuple[str, str, float], int] = defaultdict(int)

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def request_finished(
        self,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        method = method.upper()
        route = route or "unmatched"
        duration_seconds = max(0.0, duration_seconds)
        key = (method, route)
        with self._lock:
            self._in_flight -= 1
            self._requests[(method, route, status_code)] += 1
            self._duration_count[key] += 1
            self._duration_sum[key] += duration_seconds
            for bucket in _DURATION_BUCKETS:
                if duration_seconds <= bucket:
                    self._duration_buckets[(method, route, bucket)] += 1

    def reset(self) -> None:
        """Clear observations; intended for isolated tests."""
        with self._lock:
            self._started_at = time.time()
            self._in_flight = 0
            self._requests.clear()
            self._duration_count.clear()
            self._duration_sum.clear()
            self._duration_buckets.clear()

    def render(self) -> str:
        with self._lock:
            started_at = self._started_at
            in_flight = self._in_flight
            requests = dict(self._requests)
            duration_count = dict(self._duration_count)
            duration_sum = dict(self._duration_sum)
            duration_buckets = dict(self._duration_buckets)

        lines = [
            "# HELP ods_dashboard_api_process_start_time_seconds Start time of this API process.",
            "# TYPE ods_dashboard_api_process_start_time_seconds gauge",
            f"ods_dashboard_api_process_start_time_seconds {started_at:.3f}",
            "# HELP ods_dashboard_api_http_requests_in_flight Requests currently being served.",
            "# TYPE ods_dashboard_api_http_requests_in_flight gauge",
            f"ods_dashboard_api_http_requests_in_flight {in_flight}",
            "# HELP ods_dashboard_api_http_requests_total Completed HTTP requests.",
            "# TYPE ods_dashboard_api_http_requests_total counter",
        ]
        for (method, route, status), count in sorted(requests.items()):
            labels = f'method="{_label_value(method)}",route="{_label_value(route)}",status="{status}"'
            lines.append(f"ods_dashboard_api_http_requests_total{{{labels}}} {count}")

        lines.extend([
            "# HELP ods_dashboard_api_http_request_duration_seconds HTTP request latency.",
            "# TYPE ods_dashboard_api_http_request_duration_seconds histogram",
        ])
        for method, route in sorted(duration_count):
            labels = f'method="{_label_value(method)}",route="{_label_value(route)}"'
            for bucket in _DURATION_BUCKETS:
                count = duration_buckets.get((method, route, bucket), 0)
                lines.append(
                    "ods_dashboard_api_http_request_duration_seconds_bucket"
                    f'{{{labels},le="{bucket:g}"}} {count}',
                )
            count = duration_count[(method, route)]
            lines.append(
                "ods_dashboard_api_http_request_duration_seconds_bucket"
                f'{{{labels},le="+Inf"}} {count}',
            )
            lines.append(
                f"ods_dashboard_api_http_request_duration_seconds_sum{{{labels}}} "
                f"{duration_sum[(method, route)]:.9g}",
            )
            lines.append(
                f"ods_dashboard_api_http_request_duration_seconds_count{{{labels}}} {count}",
            )
        return "\n".join(lines) + "\n"


class ApiMetricsMiddleware:
    """ASGI middleware that records one observation per completed request."""

    def __init__(self, app, registry: ApiMetrics) -> None:
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        self.registry.request_started()
        started = time.perf_counter()
        status_code = 500

        async def capture_status(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            route = scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            self.registry.request_finished(
                scope.get("method", "UNKNOWN"),
                route_template,
                status_code,
                time.perf_counter() - started,
            )


api_metrics = ApiMetrics()
