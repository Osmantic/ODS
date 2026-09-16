# Checking a redirect without following it

The dependency-free `scripts/healthcheck.py` follows HTTP redirects by default.
Use `--no-redirects` when deployment readiness depends on the endpoint you
addressed, rather than the destination of its `Location` header.

```bash
# A redirect to a login page must not satisfy this HTTP 200 probe.
python3 scripts/healthcheck.py http://127.0.0.1:3001/health --no-redirects --json

# Verify that an endpoint intentionally serves a redirect.
python3 scripts/healthcheck.py http://127.0.0.1:3001/old-path --no-redirects --expect-status 3xx --json
```

The result reports the first response's status. HEAD and GET are supported;
a body predicate still forces GET and examines that same response body.
`--no-redirects` is rejected for TCP targets. TLS certificate verification,
timeouts, retry policy, HEAD-to-GET fallback, and exit codes are unchanged:
0 healthy, 1 probe failed, 2 invalid usage.

This flag selects which response to inspect; it does not make a redirected
endpoint healthy automatically, resolve a proxy configuration, or constrain
the network access of other tools. Omit the flag to retain existing behavior.
