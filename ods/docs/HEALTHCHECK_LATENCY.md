# Healthcheck latency budgets

Use a latency budget when a listening service can still be too slow for callers:

```bash
python3 scripts/healthcheck.py http://127.0.0.1:8080/health --max-latency-ms 500 --json
python3 scripts/healthcheck.py tcp://127.0.0.1:6333 --max-latency-ms 200 --retries 0
```

The positive integer budget covers the complete probe, including retries and their waits. An otherwise successful probe exceeding the budget exits 1 and reports `ok: false`, measured `elapsed_ms`, and a `latency budget exceeded` detail. HTTP status is preserved. Existing connection/status/body failures retain their own diagnostic; a fast failure never becomes healthy.

This is an acceptance threshold, not cancellation: `--timeout` still controls request/connection waiting. Without the new flag, behavior is unchanged. It measures this probe's duration rather than application inference throughput or a percentile over multiple requests. The dependency-free script uses the same behavior on Linux, macOS and Windows with Python.
