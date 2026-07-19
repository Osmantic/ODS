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

The installer auto-generates these in `.env`:

| Secret | Purpose |
|--------|---------|
| `WEBUI_SECRET` | Session signing for Open WebUI |
| `N8N_PASS` | Admin password for n8n |
| `LITELLM_KEY` | API key for LiteLLM gateway |

**Verify no defaults remain:**
```bash
grep -E "(PASSWORD|SECRET|KEY)" .env | grep -i changeme
```

### Manual Secret Rotation

```bash
# Generate new secrets
NEW_WEBUI=$(openssl rand -hex 32)
NEW_N8N=$(openssl rand -base64 16)
NEW_LITELLM="sk-ods-$(openssl rand -hex 16)"

# Update .env
sed -i "s/WEBUI_SECRET=.*/WEBUI_SECRET=$NEW_WEBUI/" .env
sed -i "s/N8N_PASS=.*/N8N_PASS=$NEW_N8N/" .env
sed -i "s/LITELLM_KEY=.*/LITELLM_KEY=$NEW_LITELLM/" .env

# Restart services
docker compose down && docker compose up -d
```

---

## Network Security

### Default: Localhost Only

All services bind to `127.0.0.1` — accessible only from the local machine.

### Quick LAN Access

For headless servers accessible from other machines on the same network:

```bash
./install.sh --lan
```

Or change `BIND_ADDRESS` to `0.0.0.0` in the Dashboard Settings tab, then restart:

```bash
ods restart
```

This binds all services to all network interfaces. Use firewall rules to
restrict access to your local subnet:

```bash
sudo ufw allow from 192.168.0.0/24 to any port 3000  # WebUI
sudo ufw allow from 192.168.0.0/24 to any port 3001  # Dashboard
sudo ufw allow from 192.168.0.0/24 to any port 8080  # LLM API
```

### Host Agent Network Binding

The host agent (`bin/ods-host-agent.py`) has its own bind address, separate from the Docker services above. It is controlled by `ODS_AGENT_BIND` in `.env`:

| Platform | Default | Behavior |
|----------|---------|----------|
| macOS / Windows | `127.0.0.1` | Docker Desktop routes container traffic via loopback — loopback is sufficient |
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
| Open WebUI | Built-in | Change admin password, disable signups |
| n8n | Basic auth | Use strong password, enable 2FA |
| llama-server | None | Keep localhost-only, use LiteLLM for remote |
| LiteLLM | API key | Set `LITELLM_KEY` in .env |

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

```bash
# Pull latest images
docker compose pull

# Recreate containers
docker compose up -d
```

Watch for security updates to: llama-server, Open WebUI, n8n, base images.

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
2. Email: security@osmantic.com with subject `Security report`
3. Include: description, reproduction steps, potential impact

`security@osmantic.com` is an inbound alias monitored through the shared
`contact@osmantic.com` inbox. It is not a separate mailbox or Send-As identity,
so replies may come from `contact@osmantic.com` or another configured Osmantic
sender.

We'll respond within 48 hours.

---

*Security is a shared responsibility. When in doubt, keep it local.*
