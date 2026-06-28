# Serving Multiple Users from a Single ODS Install

This is a recipe, not a feature description. ODS is shaped for single-machine, single-operator use out of the box: services bind to localhost, llama-server is launched with one concurrency slot, and most service-level auth is gateway-level (a shared password or token), not per-user RBAC. Standing up a multi-user install — concurrent users from different devices over LAN or remote access — is possible today, but it takes about half a dozen deliberate choices the installer doesn't make for you.

This guide walks them. It is the operator's how-to companion to [`SECURITY.md`](../SECURITY.md), which is the security contract — read both.

---

## Scope

"Multi-user" here means several concurrent users hitting the stack from different devices, the way someone would deploy ODS for a small team or a workshop, not multiple shells on the same machine. If you have a single user on a single laptop, none of this is needed.

---

## The honest baseline

Without changes, a fresh install:

- Binds every service to `127.0.0.1` — no other machine on your LAN can reach it.
- Launches `llama-server` with `--parallel 1` — one concurrent inference request at a time. A second user types and waits.
- Generates a `DASHBOARD_API_KEY` automatically and writes it inside the container if you don't set one — auth happens, but you may not know the value.
- Has no reverse proxy and no TLS bundled — the installer doesn't ship a Caddy or nginx service.
- Has no firewall help — `ufw`/`firewalld` is on you.

The hardware guide claims 5–40 concurrent users by tier. Those numbers assume you've done the steps below. They are not what you get from a default install.

---

## The six steps

### 1. Set `LLAMA_PARALLEL` (highest leverage)

This is the single most impactful knob. The default of 1 silently single-streams every additional user.

```bash
# in .env
LLAMA_PARALLEL=8
```

`docker-compose.base.yml` passes this directly to llama-server as `--parallel`. Each parallel slot pre-allocates KV cache, so total KV ≈ `LLAMA_PARALLEL × CTX_SIZE`. You're trading VRAM for concurrency — pick a value that fits your card after the model weights load. Rough starting points:

| Tier | Suggested `LLAMA_PARALLEL` |
|---|---|
| Entry (12 GB) | 2–3 |
| Prosumer (16 GB) | 5–8 |
| Pro (24 GB) | 10–15 |
| Enterprise (2× 24 GB+) | 20+ |

Verify: `docker compose logs llama-server | grep parallel` after restart.

### 2. Bind services to the network

```bash
./install.sh --lan
```

Or set `BIND_ADDRESS=0.0.0.0` in `.env` and run `ods restart`. `install-core.sh` maps `--lan` to that value, and the base plus extension compose port bindings use `${BIND_ADDRESS:-127.0.0.1}` — no per-service edits needed.

### 3. Add firewall rules

The installer does not configure your firewall. From `SECURITY.md`:

```bash
sudo ufw allow from 192.168.0.0/24 to any port 3000  # WebUI
sudo ufw allow from 192.168.0.0/24 to any port 3001  # Dashboard
sudo ufw allow from 192.168.0.0/24 to any port 8080  # LLM API
```

Open only the ports you actually want users to reach. Keep the dashboard, dashboard-api, and any developer-facing tools (n8n, opencode) closed at the firewall unless you intend them to be reachable.

### 4. Set `DASHBOARD_API_KEY` explicitly

```bash
# in .env, before first start
DASHBOARD_API_KEY=$(openssl rand -hex 32)
```

If unset, `extensions/services/dashboard-api/security.py` auto-generates a key and writes it to a file inside the container. Auth still happens, but you don't have the value handy and may not realize the dashboard is reachable from anywhere your LAN bind is. Setting it yourself is the safe default.

### 5. Add a reverse proxy + TLS — or use a VPN

For LAN-only deployments, a VPN like Tailscale or WireGuard is usually the lower-effort path: you skip the public-DNS, certificate, and rate-limiting story entirely and get end-to-end encryption between authenticated devices. `SECURITY.md` recommends this explicitly.

For deployments that genuinely need to be public, `SECURITY.md` includes Caddy and nginx examples. Caddy is the smaller commitment because it handles Let's Encrypt automatically. If you go this route, keep a rate-limit zone in front of the LLM API endpoint — the chat services are happy to consume all available compute.

### 6. Decide what to expose

Recommended exposure profile for a small-team deployment:

| Service | Expose to LAN? | Notes |
|---|---|---|
| `open-webui` (3000) | Yes | The user-facing chat UI; has per-account auth |
| `dashboard` (3001) | No | Operator surface; keep VPN-only or admin-only |
| `dashboard-api` (3002) | No | Same — controls system state, no per-user RBAC |
| `llama-server` (8080) | If users need raw API | OpenAI-compatible; protected by `LITELLM_KEY` if routed via litellm |
| `litellm` (4000) | If users need API | Master-key auth — same key for everyone |
| `n8n` (5678) | No | Workflows are global; one user can edit another's |
| `opencode` (3003) | No | Web IDE; password-protected but no per-user isolation |

---

## Inference backend choice

`llama-server` with `--parallel N` is the right call for most multi-user installs — it's portable across NVIDIA, AMD, Apple Silicon, and CPU, and the parallelism story is good enough up to roughly 10–15 concurrent users on a single capable GPU.

Beyond that, or for throughput-bound workloads (many short requests, agents firing in parallel), vLLM's continuous-batching wins are substantial. See [`VLLM-SETUP.md`](VLLM-SETUP.md) for when to switch and what's involved. vLLM is NVIDIA-mostly; if your hardware crosses platforms, stay on llama-server.

---

## Search backend under load

The default `searxng` service is excellent for individual use, but its results come from upstream public engines (Google, Bing, DuckDuckGo) that aggressively bot-block at small scale. Once you have several users issuing search queries — or agents driving searxng programmatically — you will hit captchas the proxy cannot route around.

If this becomes a problem, the optional `brave-search` extension wraps the Brave Search API (an independent crawler index, no captcha layer) behind a small JSON endpoint. It runs alongside searxng, not in place of it.

---

## What is *not* multi-tenant

Be honest with your users about this:

- **n8n workflows are global.** Anyone with n8n access can see, edit, and delete every workflow. There is no per-user workflow space.
- **Dashboard / dashboard-api have no RBAC.** Auth gates access; once in, every user has full system control (start/stop services, toggle features, see GPU state).
- **Embeddings / qdrant collections are shared.** RAG ingestion is a single namespace. One user's documents are searchable by another user's queries.
- **LiteLLM uses a shared master key.** No per-user attribution or quotas.

`open-webui` is the only service in the stack with a real multi-user account model — chat history is per-account. If your users only interact via open-webui, the sharing problems above mostly don't surface.

---

## Capacity guidance

[`HARDWARE-GUIDE.md`](HARDWARE-GUIDE.md) has the canonical per-tier user counts. Treat them as ceilings that assume the steps above are done. A default install on a Pro-tier machine will not serve 10–15 users — it will serve one at a time. The same machine *with* `LLAMA_PARALLEL=12`, network bind, and the relevant firewall rules will get close.

---

## Recommended deployment patterns

### Small team on the same LAN

1. `LLAMA_PARALLEL` set per the table above.
2. `./install.sh --lan` + `ufw` rules locked to your subnet.
3. `DASHBOARD_API_KEY` set in `.env`.
4. Expose only `open-webui` to users; keep the rest at the firewall.
5. Skip the reverse proxy — local HTTP over a trusted LAN is fine.

### Remote access for a distributed team

1. Same `LLAMA_PARALLEL` and `DASHBOARD_API_KEY` setup.
2. `BIND_ADDRESS=0.0.0.0` so the Tailscale interface is reachable.
3. Install Tailscale on the host; add team members to the tailnet.
4. Users hit `http://<tailscale-name>:3000` for open-webui. No public DNS, no certs, no nginx.
5. Block all open ports at the host firewall except SSH and the Tailscale interface.

### Public-facing demo or workshop

This is the highest-effort path because it's the riskiest one — you're putting a stack with shared n8n workflows, shared embeddings, and an unaudited proxy chain on the open internet.

1. vLLM as the inference backend ([`VLLM-SETUP.md`](VLLM-SETUP.md)) — continuous batching is the difference between handling 10 visitors and handling 60+.
2. `brave-search` instead of searxng — public engines bot-block fast under any sustained query load.
3. Caddy with automatic TLS and a rate-limit zone in front of every chat endpoint.
4. Either a custom front-end with no auth (and the assumption that users can't access anything they could damage) — *or* open-webui's account model, with `WEBUI_AUTH=true` enforced.
5. Disable or firewall every service that isn't part of the demo. n8n and dashboard especially.

A real public-facing deployment along these lines (multi-tile chat / search / video booth, ~hundreds of unique visitors over a single day, peaks above 60 concurrent) was validated this way: vLLM, Tailscale-accessed admin plane, Brave Search, no per-tile auth, with the assumption that any user-visible action was either disposable or rate-limited at the proxy. The pieces work; the recipe is what's missing from the default install.

---

## Tuning checklist

Run through this before declaring multi-user ready:

- [ ] `LLAMA_PARALLEL` set in `.env` and confirmed in llama-server logs.
- [ ] `DASHBOARD_API_KEY` set in `.env` (not auto-generated).
- [ ] `BIND_ADDRESS` set appropriately; `ods restart` run after the change.
- [ ] Firewall rules limit each open port to the intended subnet or VPN interface.
- [ ] Dashboard, dashboard-api, n8n, opencode are *not* reachable from the user-facing network.
- [ ] open-webui has `WEBUI_AUTH=true` (the default) and a strong `WEBUI_SECRET`.
- [ ] Inference backend matches user count: llama-server up to ~10–15, vLLM beyond.
- [ ] Reverse proxy or VPN in place for any remote access.
- [ ] Capacity claim from `HARDWARE-GUIDE.md` reality-checked under load before announcing.

---

## Future work

The single highest-leverage change ODS itself could make: **tier-aware default values for `LLAMA_PARALLEL`** in the installer (`installers/lib/tier-map.sh`), so a Prosumer install lands at `LLAMA_PARALLEL=6` automatically and a Pro install at `LLAMA_PARALLEL=12`. That would close most of the gap between the hardware-guide capacity claims and what a default install actually delivers.

This guide deliberately stops at documenting the current state. The installer-side change touches tier-mapping logic, which is core, and per the contribution policy that warrants a discussion issue before a PR. If a maintainer is interested, that's the natural next thread.

---

## Provenance

The configuration values, exposure-profile recommendations, and capacity caveats in this guide come from three sources:

1. The ODS codebase as of `origin/main` at the time of writing — `install-core.sh`, `docker-compose.base.yml`, `SECURITY.md`, `HARDWARE-GUIDE.md`, and the relevant extension manifests.
2. A public-facing multi-tile demo deployment validated for ~hundreds of unique visitors and peaks above 60 concurrent on a single Blackwell-class workstation (the source of the vLLM, Brave Search, and "rate-limit at the proxy, not in the app" recommendations in the public-facing pattern).
3. The community capacity numbers documented in `HARDWARE-GUIDE.md` and `FAQ.md`, which originate from a 2× RTX 4090 reference rig.

None of these are universal. Treat the guide as a starting recipe, not a benchmark.
