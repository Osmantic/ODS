# TLS endpoint diagnostics

Run from `ods/` with Python 3.9 or newer. Check a reverse proxy before a
certificate renewal deadline without sending an HTTP request or credentials:

```bash
python3 scripts/check-tls-endpoint.py chat.example.org --min-valid-days 14
python3 scripts/check-tls-endpoint.py 127.0.0.1 --port 8443 \
  --server-name chat.internal --ca-file /path/to/private-ca.pem
```

The connection host chooses the network destination. `--server-name` chooses
both TLS SNI and the hostname verified against the certificate; it defaults to
the connection host. Normal trust-chain and hostname verification remain enabled.
A private CA must be explicitly trusted with `--ca-file`; there is no insecure
bypass. The tool does not modify ODS, proxy configuration, trust stores or certificates.

One JSON receipt is written to stdout, with `schema_version: 1`, `ok`, `reason`,
the endpoint, UTC check time, negotiated TLS version and verified leaf certificate
expiry, remaining days and SHA-256 fingerprint. Failed handshakes leave the
certificate null rather than reporting unverified certificate data.

| Exit | Meaning |
| --- | --- |
| 0 | TLS verification passed and the leaf has at least the requested remaining days |
| 1 | Connection/TLS verification failed, timed out, or the verified leaf expires too soon |
| 2 | Invalid CLI arguments (stderr) or unusable CA configuration (JSON stdout) |

The default lifetime threshold is seven days; zero checks current validity only.
`--timeout` defaults to five seconds for each connection/handshake operation.
DNS resolution and attempts across multiple resolved addresses can take longer;
this is not a hard total deadline. No retry loop or scheduled monitor is installed.

This checks the endpoint presented to this machine and depends on its clock and
trust store. It does not prove HTTP application health, every load-balancer backend,
revocation status, or the remaining lifetime of intermediate certificates. The
handshake validates the chain now; the advance-expiry threshold covers the leaf.
Run from each relevant network when split DNS or different ingress paths matter.
