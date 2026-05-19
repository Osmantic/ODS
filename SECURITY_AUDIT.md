# DreamServer Security Audit
**Analyst:** latentcollapse
**Date:** 2026-03-08
**Scope:** `Light-Heart-Labs/DreamServer` public repository, local clone only — no live infrastructure touched
**Tools:** gitleaks 8.x, bandit 1.9.4, semgrep (auto config), shellcheck, manual review
**Method:** Passive static analysis. Apache 2.0 license permits full code audit.

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| High | 3 |
| Medium | 5 |
| Low | 2 |

The Critical finding should be addressed immediately — format-valid credentials traced to the founder's home path have been sitting in a public repository since the voice agent framework was committed. All other findings are fixable in a normal development cycle.

---

## CRITICAL

### C1 — Likely real LiveKit credentials committed to public repo

**Historical file:** legacy voice-agent framework token server, removed from the maintained product tree
**Detected by:** gitleaks (generic-api-key)

```python
API_KEY    = 'APIRUdt****'
API_SECRET = 'bTpgs0****'
```

The file header reads `Deploy to: /home/michael/hvac-token-server.py`. The org contact on GitHub is `michael@lightheartlabs.com`. The credentials are format-valid:

- `API_KEY` is 15 characters — matches LiveKit API key format
- `API_SECRET` is 43 characters — matches LiveKit API secret format

These are not placeholder values. They appear to be real LiveKit credentials belonging to the founder, committed as part of a voice agent reference implementation.

**Impact:** Anyone with repo access can generate valid LiveKit JWT tokens, join or publish to any LiveKit room associated with this account, and potentially eavesdrop on or inject into live voice sessions.

**Remediation:**
1. Rotate the LiveKit API key and secret immediately at console.livekit.io
2. Remove the historical file from git history with `git filter-repo` if the project chooses to rewrite public history
3. Replace with environment variable references: `API_KEY = os.environ['LIVEKIT_API_KEY']`

---

## HIGH

### H1 — SearXNG static shared secret key

**File:** `dream-server/config/searxng/settings.yml:3`
**Detected by:** gitleaks (generic-api-key)

```yaml
secret_key: "9d0e105e00289d066f0532614b135e5df22eeb2b6e0228bd4c0a4426ae3f39f0"
```

Every DreamServer installation ships with this identical static secret. SearXNG uses `secret_key` for HMAC-signing session cookies and CSRF tokens. A user who has read this repo can forge valid session tokens on any DreamServer install that hasn't manually changed this value.

**Impact:** Session forgery and CSRF bypass on all default installations.

**Remediation:** Generate a unique secret during install (the installer already generates other secrets via `openssl rand`) and inject it into `settings.yml` at install time rather than shipping a static value in the repo.

---

### H2 — `eval` on external script output creates command injection surface

**Files:**
- `dream-server/installers/lib/detection.sh:32` — `eval "$env_out"` (capability profile)
- `dream-server/installers/lib/detection.sh:75` — `eval "$env_out"` (backend contract)
- `dream-server/installers/macos.sh:81` — `eval "$PREFLIGHT_ENV"`
- `dream-server/installers/phases/04-requirements.sh:38` — `eval "$PREFLIGHT_ENV"`

The installer uses `eval "$env_out"` to source variables from `build-capability-profile.sh` → `classify-hardware.sh`. This pipeline ingests hardware metadata including GPU name (from nvidia-smi output). If a GPU name contains shell metacharacters — either from a malicious driver, a spoofed nvidia-smi binary earlier in `$PATH`, or a future code change that passes user input — the `eval` will execute arbitrary commands with the privileges of the user running the installer.

**Impact:** Privilege escalation / arbitrary code execution during install on any system where the hardware detection pipeline can be influenced.

**Remediation:** Replace `eval` with a parser that only accepts `KEY=value` lines matching a strict allowlist of expected variable names. Example:
```bash
while IFS='=' read -r key value; do
    case "$key" in
        CAP_PLATFORM_ID|CAP_GPU_VENDOR|CAP_RECOMMENDED_TIER|...)
            printf -v "$key" '%s' "$value" ;;
    esac
done <<< "$env_out"
```

---

### H3 — OpenClaw `dangerouslyDisableDeviceAuth` + `0.0.0.0` binding (upstream)

**Files:**
- `dream-server/config/openclaw/openclaw.json:47-55`
- `dream-server/config/openclaw/openclaw-strix-halo.json:47-55`
- `dream-server/extensions/services/openclaw/compose.yaml:16,23`

Three dangerous flags are active simultaneously in the default config:

```json
"gateway": {
    "host": "0.0.0.0",
    "controlUi": {
        "allowInsecureAuth": true,
        "dangerouslyDisableDeviceAuth": true,
        "dangerouslyAllowHostHeaderOriginFallback": true
    }
}
```

Combined with the Docker port mapping (`${OPENCLAW_PORT:-7860}:18789` — also binds to `0.0.0.0` by default) and the entrypoint flag `--bind lan`, this results in completely unauthenticated access to an agent framework with `exec`, `read`, `write`, and sub-agent spawning tools exposed to all network interfaces.

Note: `--bind lan` in the entrypoint **overrides** the `host` field in the JSON config — both must be fixed together.

**Impact:** Anyone on the local network (or internet, if port-forwarded) can run arbitrary commands on the host machine without any authentication.

**Remediation (partially addressed in PR #67):**
- `"host": "0.0.0.0"` → `"host": "127.0.0.1"` in both JSON configs ✓
- `--bind lan` → `--bind localhost` in entrypoint ✓
- Port binding → `127.0.0.1:${OPENCLAW_PORT:-7860}:18789` ✓
- Follow-up needed: remove or gate `dangerouslyDisableDeviceAuth: true`

---

## MEDIUM

### M1 — SQL injection pattern in token-spy

**File:** `dream-server/extensions/services/token-spy/db.py:77`
**Detected by:** bandit (B608), semgrep (sqlalchemy-execute-raw-query)

```python
conn.execute(f"ALTER TABLE usage ADD COLUMN {col} {typedef}")
```

`col` and `typedef` are currently sourced from a hardcoded list, so this is not directly exploitable today. However the pattern is dangerous — any future refactor that passes externally-derived column names or type definitions here lands directly in a raw SQL string without parameterization. SQLite's `ALTER TABLE` does not support parameterized column names, but the value should at minimum be validated against a strict allowlist before interpolation.

**Remediation:** Add an explicit allowlist check: `assert col in ALLOWED_COLS` before the execute, and `assert typedef in ALLOWED_TYPES`.

---

### M2 — dashboard and token-spy containers running as root

**Files:** `extensions/services/dashboard/Dockerfile`, `extensions/services/token-spy/Dockerfile`
**Detected by:** semgrep (missing-user-entrypoint, missing-user)

Neither Dockerfile contains a `USER` directive. Processes inside both containers run as root. The dashboard container is internet-facing (port 3001).

**Impact:** If either container is compromised via a vulnerability in nginx, the React app, or the token-spy proxy, the attacker has root inside the container with full access to mounted volumes.

**Remediation:**
```dockerfile
RUN addgroup -S dream && adduser -S dream -G dream
USER dream
```

---

### M3 — Nginx H2C smuggling conditions

**File:** `extensions/services/dashboard/nginx.conf:22`
**Detected by:** semgrep (possible-nginx-h2c-smuggling)

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection 'upgrade';
```

This combination creates known HTTP/2 cleartext (H2C) smuggling conditions. An attacker on the local network could potentially craft requests that bypass nginx's auth header injection and reach the dashboard-api backend directly, circumventing the `Authorization: Bearer` token.

**Remediation:** Unless WebSocket upgrades are explicitly needed on the `/api/` path, change `Connection` to a static value: `proxy_set_header Connection "close";`

---

### M4 — Voice agent WebSocket connection unencrypted

**File:** `extensions/services/dashboard/src/hooks/useVoiceAgent.js:12`
**Detected by:** semgrep (detect-insecure-websocket)

```javascript
const LIVEKIT_URL = import.meta.env.VITE_LIVEKIT_URL || `ws://${getHost()}:7880`
```

The fallback LiveKit URL uses `ws://` (unencrypted WebSocket). All voice audio is transmitted in cleartext when `VITE_LIVEKIT_URL` is not explicitly set.

**Remediation:** Default to `wss://` and document that users need a valid TLS cert or self-signed cert accepted by the browser for local use.

---

### M5 — `local` keyword used outside function scope *(addressed in PR #72)*

**File:** `dream-server/installers/phases/11-services.sh:32-33`
**Detected by:** shellcheck (SC2168, severity: error)

```bash
local some_var=...   # line 32 — outside any function
local other_var=...  # line 33 — outside any function
```

`local` is only valid inside functions. On bash this silently becomes a global variable; on strict POSIX shells (`dash`, `sh`) this is a syntax error and the installer will abort at this phase.

**Remediation:** Remove `local` keyword or wrap the code block in a function.

---

## LOW

### L1 — Missing Subresource Integrity (SRI) on CDN-loaded scripts

**Files:** `extensions/services/dashboard/public/agents.html:7-9`, `extensions/services/dashboard/templates/index.html:7-9`
**Detected by:** semgrep (missing-integrity)

External scripts loaded from CDNs without `integrity` attributes. If the CDN is compromised or the resource URL is hijacked, malicious JavaScript executes in the dashboard context with full access to API tokens and service credentials.

**Remediation:** Add `integrity="sha384-..."` and `crossorigin="anonymous"` to all CDN `<script>` and `<link>` tags.

---

### L2 — Missing security headers on dreamserver.ai

**Method:** Passive HTTP header inspection
**Tool:** Python urllib

The landing page at `dreamserver.ai` is missing all standard security headers:

| Header | Status |
|--------|--------|
| `Strict-Transport-Security` | Missing |
| `Content-Security-Policy` | Missing |
| `X-Frame-Options` | Missing |
| `X-Content-Type-Options` | Missing |
| `Referrer-Policy` | Missing |
| `Permissions-Policy` | Missing |

Low severity for a static marketing page, but notable for a product whose core value proposition is security and privacy.

**Remediation:** Add headers via Hostinger's CDN configuration or a `_headers` file (Netlify/Vercel-style). Five-minute fix.

---

## Disclosure Notes

- No live infrastructure was accessed. All findings are from static analysis of the public repository.
- The LiveKit credential finding (C1) should be treated as a responsible disclosure. Recommend rotating before this report is shared publicly.
- All other findings are standard code quality / security hygiene items appropriate for a public bug report or PR.

---

*Report generated using ArchMCP red-team container (BlackArch Linux) + gitleaks, bandit, semgrep, shellcheck.*
