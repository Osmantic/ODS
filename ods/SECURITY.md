# ODS Security Guide

Security best practices for running ODS.

---

## ⚠️ Before You Start

1. **Run `./install.sh`** — generates secure random secrets automatically
2. **Never use default passwords** — if you see "changeme", change it
3. **Keep the default localhost binding** — opt into LAN exposure only when you understand the firewall and authentication tradeoffs

---

## Secrets Management

### Generated Secrets

The installer generates every credential it writes to `.env` with a
cryptographically secure generator (`openssl rand`, falling back to
`/dev/urandom`). It writes `.env` with mode `600`, and reruns keep existing
values. The core credentials are:

| Secret | Purpose |
|--------|---------|
| `DASHBOARD_API_KEY` | Admin key for dashboard-api. nginx adds it server-side; browsers never receive it |
| `ODS_AGENT_KEY` | Bearer key for the host agent API |
| `ODS_SESSION_SECRET` | Signs ODS session cookies (for example the Hermes gate) |
| `WEBUI_SECRET` | Session signing for Open WebUI |
| `LITELLM_KEY` | LiteLLM gateway key |
| `LLAMA_SERVER_API_KEY` | Key of the Windows AMD `llama-server.exe` (Windows setup generates it) |
| `QDRANT_API_KEY`, `SHIELD_API_KEY`, `TOKEN_SPY_API_KEY` | Service API keys |
| `HERMES_DASHBOARD_SESSION_TOKEN`, `OPENCODE_SERVER_PASSWORD` | Agent and coding-tool credentials |
| `N8N_PASS`, `SEARXNG_SECRET`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `DIFY_SECRET_KEY` | Optional-service credentials |

Each of these is marked `"secret": true` in `.env.schema.json`. Library
extensions generate their own credentials when you install them.

**Verify no placeholder values remain:**
```bash
grep -E "(PASSWORD|SECRET|KEY)=" .env | grep -i changeme
```

Dashboard settings saves keep a copy of the previous `.env` under
`data/config-backups/` (owner-only, newest 20 kept). Those copies contain the
same secrets as `.env`, so treat `data/` as sensitive (see
[What's Stored](#whats-stored)). After rotating a secret, delete the older
copies that still hold the old value.

### Manual Secret Rotation

Rotate a value by writing a new hex secret into `.env`, then restart the stack
so every service that reads it picks it up:

```bash
cd ~/ods
rotate() {  # usage: rotate VAR [prefix]
  local value="${2:-}$(openssl rand -hex 32)"
  sed -i.bak "s|^$1=.*|$1=$value|" .env && rm -f .env.bak
}
rotate WEBUI_SECRET
rotate LITELLM_KEY sk-ods-
ods restart
```

Hex values avoid characters that would break the `sed` expression. Rotating
`WEBUI_SECRET` or `ODS_SESSION_SECRET` signs everyone out, and rotating
`DASHBOARD_API_KEY` also invalidates dashboard sessions. n8n's owner account
comes from `N8N_USER` and `N8N_PASS` wherever no owner had been created yet,
and n8n resets it to them at every start, so rotate `N8N_PASS` and restart
n8n. An owner created earlier on n8n's first-run screen keeps its own
password, which is changed inside n8n.

---

## Network Security

### Default: Localhost Only

All services bind to `127.0.0.1` — accessible only from the local machine.

### Quick LAN Access

For headless servers accessible from other machines on the same network:

```bash
./install.sh --lan
```

`--lan` sets `BIND_ADDRESS=0.0.0.0` and turns on Open WebUI sign-in
(`WEBUI_AUTH=true`). If you change `BIND_ADDRESS` in the Dashboard Settings
tab instead, saving a non-loopback address turns sign-in on as well, and
`ods restart` applies it. If you edit `BIND_ADDRESS` in `.env` directly,
`ods start`, `ods restart` and `ods update` (`.\ods.ps1` on native Windows)
turn sign-in on before they recreate Open WebUI.

This publishes the Dashboard (sign-in required from the network) and Open WebUI
on the selected interface. Backend APIs, native inference and extension ports
remain bound to `127.0.0.1`; `--lan` does not authorize publishing them.

**Before exposing an install that started localhost-only:** Open WebUI runs
without sign-in on localhost-only installs. In that mode it creates a built-in
administrator, `admin@localhost`, with the password `admin`, and that account
keeps working after sign-in is turned on. Sign in as `admin@localhost` and
change its password (or create your own administrator and delete it) before
other devices can reach port 3000. ODS refuses to start Open WebUI for other
devices, through `BIND_ADDRESS` or the ODS proxy, while that account still has
the password `admin`. In that case Open WebUI stays stopped, and its log
(`docker logs ods-webui`) explains these steps.

Docker publishes container ports through its own firewall rules, so host
firewalls such as `ufw` do not reliably restrict them. Restrict exposure with
`BIND_ADDRESS` (bind to one interface rather than `0.0.0.0`), your router, or a
private tunnel such as Tailscale. In LAN mode the Dashboard's network port is
`3011`; port `3001` stays on this machine's loopback.

For other services, use an explicitly configured authenticated reverse proxy
or private tunnel. The optional ODS proxy enforces the Dashboard owner session
before forwarding LAN requests to Hermes.

**Upgrade note:** older user-extension recipes with interpolated host bindings
(such as `${BIND_ADDRESS:-127.0.0.1}`) no longer pass validation. A safe default
can be overridden by the environment. Reinstall a curated recipe from the
updated library, or review and republish a custom recipe with literal
`127.0.0.1` host bindings. ODS does not silently rewrite approved recipe bytes.
Source-built recipes additionally require the sandbox described below.

### Source extension sandbox

GitHub source recipes run as a numeric non-root user with all capabilities
dropped, `no-new-privileges`, a read-only root filesystem, bounded memory/CPU/
processes, and their own internal Docker network. The generated default gives
them 2 GiB, two CPUs, 256 PIDs and a writable 64 MiB `/tmp`.

This runtime does not have internet or ODS backend access. Install build-time
dependencies in the Dockerfile. Applications that need persistence must declare
reviewed extension-owned storage with permissions for their runtime UID. An
application that requires external APIs or privileged ODS access needs a
separately reviewed integration; removing the sandbox is not an automatic
fallback. Docker still shares the host kernel, so this is defense in depth,
not a guarantee that hostile native code is safe.

### Dashboard Sign-in

The dashboard manages the whole install, so it only opens without a password
for a browser on the ODS machine itself (`http://localhost:3001`). Any other
route needs a one-time sign-in per browser, remembered for 30 days:

- LAN mode (`--lan` or `BIND_ADDRESS=0.0.0.0`) — port 3001 stays on this
  machine's loopback and opens without sign-in. Network port 3011 requires it.
- ODS proxy (`dashboard.<device>.local`), a reverse proxy, or Tailscale Serve
  in front of `localhost`.

Choose a memorable dashboard password on the ODS computer. Local-only users
can choose **Not now** and continue; network access remains protected. The
password is stored as a salted PBKDF2 hash, never plaintext, in the persistent
ODS data directory.

Forgotten password: open **Your profile** and choose **Change dashboard password**
on the ODS computer, or run `ods dashboard-login` there and open its one-time
link. The link expires after 10 minutes and opens password setup without the
old password. Saving a replacement revokes other dashboard sessions and unused
links. No email service or separately saved recovery key is required. Rotating
`DASHBOARD_API_KEY` also signs every browser out. Magic-link guest invites for chat
never grant dashboard, ODS Talk or Hermes access.

### Trust Boundary

ODS treats the machine it runs on, and the containers on its `ods-network`
Docker network, as one trust zone:

- **Local browsers.** A browser on the ODS machine reaches the dashboard admin
  API at `http://localhost:3001` without signing in. Pages from other sites are
  blocked: cross-site state-changing requests are rejected by `Origin` and
  `Sec-Fetch-Site` checks, and requests whose `Host` is not a loopback name
  need a dashboard session, which also defeats DNS rebinding.
- **Containers on `ods-network`.** Bundled services and curated library
  extensions join `ods-network`; GitHub source recipes run in their own
  sandbox network instead. A container on `ods-network` can make the same
  request a local browser can, so it can use the dashboard admin API. Install
  library extensions only from sources you trust, because installing one gives
  it this level of access.
- **The remote-provider boundary.** The remote-provider egress (which holds a
  remote LLM provider's API key) and its SSH tunnel are not on `ods-network`.
  They share an internal network with LiteLLM and dashboard-api only, and the
  egress serves only callers that present the LiteLLM gateway key
  (`LITELLM_KEY`). Extensions cannot join that network. A container that holds
  `LITELLM_KEY` can still use the remote provider through LiteLLM, as it can
  any other model.
- **The host agent.** The host agent performs host-side actions for
  dashboard-api and requires `ODS_AGENT_KEY`. It runs as the installing user
  with Docker group access, so anything that controls dashboard-api can manage
  containers on the host.
- **Agents.** Pixel's default sandbox runs commands in a container with no
  network and a read-only root. **Full Access** mode runs commands directly on
  the host as the installing user, with no per-command approval. That user
  normally has Docker access, which makes Full Access equivalent to root on the
  host; enable it only where that is acceptable. The optional OpenCode web UI
  listens on `127.0.0.1:3003` without a password; enable it only on a
  single-user machine.

### Host Agent Network Binding

The host agent (`bin/ods-host-agent.py`) has its own bind address, separate from the Docker services above. It is controlled by `ODS_AGENT_BIND` in `.env`:

| Platform | Default | Behavior |
|----------|---------|----------|
| macOS | `127.0.0.1` | Docker Desktop routes container traffic via loopback — loopback is sufficient |
| Windows (native installer) | `0.0.0.0` | The installer writes it on a new install and keeps a value already set in `.env`, so the dashboard-api container can reach the agent through Docker Desktop's host gateway (`host.docker.internal`). Every `/v1/*` request still needs the bearer key (`ODS_AGENT_KEY`). |
| Linux | auto-detected | Detects the `ods-network` gateway IP (e.g. `172.18.0.1`) so containers can reach the agent; LAN devices cannot. Falls back to the default Docker bridge gateway (e.g. `172.17.0.1`) for partial/older installs, then `127.0.0.1` if detection fails. |

To override the default, set `ODS_AGENT_BIND` in `.env`:

```bash
# Restrict to loopback only (e.g. no-Docker Linux or extra hardening)
ODS_AGENT_BIND=127.0.0.1

# Bind to a Docker network gateway only (explicit Linux default)
ODS_AGENT_BIND=172.17.0.1

# Bind to all interfaces — exposes the host agent API on LAN (not recommended)
ODS_AGENT_BIND=0.0.0.0
```

> **Note:** If you bind to `0.0.0.0`, ensure `ODS_AGENT_KEY` is set in `.env` — it protects the extension management endpoints with Bearer token authentication.

### Exposing to Internet (Not Recommended)

If you must expose publicly, use a reverse proxy with TLS:

**Caddy (simple):**
```bash
# /etc/caddy/Caddyfile
yourdomain.com {
    reverse_proxy localhost:3000
}
```

**nginx (with rate limiting):**
```nginx
limit_req_zone $binary_remote_addr zone=ai:10m rate=10r/m;

server {
    listen 443 ssl;
    server_name ai.yourdomain.com;
    
    ssl_certificate /etc/letsencrypt/live/ai.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.yourdomain.com/privkey.pem;
    
    auth_basic "AI Server";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    location / {
        limit_req zone=ai burst=5;
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

**Consider VPN** (Tailscale, WireGuard) instead of public exposure.

---

## Container Security

### Resource Limits

Prevent runaway containers:

```yaml
services:
  llama-server:
    deploy:
      resources:
        limits:
          memory: 32G
        reservations:
          memory: 16G
```

### Principle of Least Privilege

The docker-compose files use:
- Non-root users where possible
- Read-only volumes where appropriate
- GPU access only for services that need it

---

## Data Security

### What's Stored

| Directory | Contents | Sensitive? |
|-----------|----------|------------|
| `data/open-webui/` | Chat history, user accounts | **Yes** |
| `data/n8n/` | Workflows, credentials | **Yes** |
| `data/qdrant/` | Vector embeddings | Maybe |
| `data/whisper/` | Model cache | No |
| `models/` | Downloaded model weights | No |

### Encryption at Rest

Docker volumes aren't encrypted by default. For sensitive deployments:
- Use LUKS encrypted filesystem
- Or encrypted Docker volumes

### Backup Security

Backups contain sensitive data — encrypt them:

```bash
# Create encrypted backup
tar -cz data/ | gpg -c > ods-backup-$(date +%Y%m%d).tar.gz.gpg

# Restore
gpg -d ods-backup-YYYYMMDD.tar.gz.gpg | tar -xz
```

### Model Download Integrity

The installer verifies GGUF model downloads using SHA256 checksums to prevent:
- Corrupted downloads from network issues
- Truncated files from interrupted transfers
- Potential supply chain attacks

**How it works:**
1. Before installation: checks existing model files against known checksums
2. After download: verifies freshly downloaded models
3. On mismatch: removes corrupt file and prompts for re-download

**Verification happens automatically** during installation. If a model fails verification:
```bash
# The installer will show:
# ✗ Downloaded file is corrupt (SHA256 mismatch)
#   Expected: 9f1a24700a339b09c06009b729b5c809e0b64c213b8af5b711b3dbdfd0c5ba48
#   Got:      [actual hash]
# Corrupt file removed. Re-run installer to download again.

# Simply re-run the installer:
./install.sh
```

**Manual verification:**
```bash
# Check a model file manually
sha256sum data/models/Qwen3.5-9B-Q4_K_M.gguf

# Compare against expected hash in installers/lib/tier-map.sh
grep -A 2 "Qwen3.5-9B" installers/lib/tier-map.sh | grep GGUF_SHA256
```

**Note:** Some models (like qwen3-coder-next) don't have checksums yet. The installer will skip verification for these but still download them successfully.

### Network Timeout Hardening

All network operations (downloads, health checks, API calls) include timeout protection to prevent indefinite hangs.

**Important semantic note:**
- `curl --max-time` is a **total wall-clock timeout** for the entire request.
- `wget --timeout/--read-timeout` are **per-connection / idle (no-progress) timeouts**.

Because of this difference, **large model downloads must not use a low `curl --max-time`**, or they will abort on slow-but-progressing links.

**Timeout policy:**
- **Health checks / small API calls**: use `curl --connect-timeout` + `--max-time` (short total timeout)
- **Script downloads / small metadata**: use `curl --connect-timeout` + `--max-time` (bounded total time)
- **Large model downloads**: fail fast on unreachable servers, and fail on *stalled* transfers, but do **not** impose a low total wall-clock cap

**Why this matters:**
- Prevents installer hangs on slow/unresponsive networks
- Keeps slow-but-progressing multi-GB downloads running
- Provides predictable failure modes instead of indefinite blocking

**Examples:**
```bash
# Health check (total timeout is OK)
curl -fsS --connect-timeout 3 --max-time 10 http://localhost:8080/health

# Script download (bounded total timeout is OK)
curl -fsSL --connect-timeout 10 --max-time 300 https://get.docker.com -o script.sh

# Large download (stall detection; no low total max-time)
# - speed-limit/time = "consider it stalled if below 10KiB/s for 30s"
curl -C - -L --progress-bar --connect-timeout 10 \
  --speed-time 30 --speed-limit 10240 \
  -o model.gguf.part https://example.com/model.gguf
```

On Linux, `wget -c` with `--timeout`/`--read-timeout` provides similar stall protection semantics for large downloads.

All timeout values are tuned for typical network conditions while allowing for slower connections.

---

## API Security

### Recommended Architecture

```
Client → LiteLLM (with API key) → llama-server (localhost only)
```

llama-server has no authentication by default. Use LiteLLM as your authenticated gateway for remote access.

### Service-Specific

| Service | Auth | Notes |
|---------|------|-------|
| Dashboard | Session off-machine; none for local browsers | See [Dashboard Sign-in](#dashboard-sign-in) and [Trust Boundary](#trust-boundary) |
| Open WebUI | Off on localhost-only installs; on with `--lan` | Change the `admin@localhost` password before exposing (see [Quick LAN Access](#quick-lan-access)); signup is closed, so administrators add accounts in Admin Panel > Users |
| n8n | Owner account | Set from `N8N_USER`/`N8N_PASS` where no owner existed; an owner created earlier in n8n keeps its own password |
| llama-server | None | Keep localhost-only, use LiteLLM for remote |
| LiteLLM | API key | Set `LITELLM_KEY` in .env |
| OpenCode web (optional) | None | Listens on `127.0.0.1:3003`; single-user machines only |

---

## Monitoring

```bash
# Watch for errors
docker compose logs -f llama-server | grep -i error

# Monitor resource usage
watch -n 5 'nvidia-smi; docker stats --no-stream'
```

Set up alerts for:
- High GPU/CPU usage (possible abuse)
- Failed auth attempts
- Unusual network traffic

---

## Updates

### Updating an existing installation

`ods update` refreshes the container images pinned by the ODS version you
installed and recreates the containers. It does not install newer ODS code, and
installs made with the one-line bootstrap have no Git checkout for
`ods-update.sh` to pull. To pick up code changes, including security fixes, run
the newer installer over your existing installation. It updates `~/ods` in
place and keeps `.env` (your secrets) and `data/`:

```bash
ods backup                                    # snapshot first
git clone --depth 1 https://github.com/Osmantic/ODS.git ~/ods-update
cd ~/ods-update/ods && ./install.sh           # updates ~/ods in place
cd ~ && rm -rf ~/ods-update
```

Do not use `--force` or uninstall/reinstall to update. Those paths remove
`data/` (chat history, workflows, accounts). In-place upgrades between versions
are not yet release-qualified; see [Source updates](docs/SOURCE-UPDATES.md).
Watch the [security advisories](https://github.com/Osmantic/ODS/security/advisories)
and [CHANGELOG](CHANGELOG.md) for fixes that need this step.

---

## Pre-Deployment Checklist

- [ ] Ran installer (secrets generated)
- [ ] No default passwords remain
- [ ] Firewall configured
- [ ] TLS enabled (if network-accessible)
- [ ] Rate limiting configured
- [ ] Backups scheduled
- [ ] Credentials documented securely

---

## Reporting Security Issues

Found a vulnerability?

1. **Do NOT open a public issue**
2. Report it privately through GitHub:
   [Security → Report a vulnerability](https://github.com/Osmantic/ODS/security/advisories/new),
   or email security@osmantic.com with subject `Security report`
3. Include: description, reproduction steps, potential impact

`security@osmantic.com` is an inbound alias monitored through the shared
`contact@osmantic.com` inbox. It is not a separate mailbox or Send-As identity,
so replies may come from `contact@osmantic.com` or another configured Osmantic
sender.

We'll respond within 48 hours.

---

*Security is a shared responsibility. When in doubt, keep it local.*
