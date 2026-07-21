# Hermes Agent

ODS ships **Hermes Agent** — the [Nous Research open-source agent](https://github.com/nousresearch/hermes-agent) packaged as a ODS service. Hermes is a self-improving generalist agent with persistent memory, autonomous skill creation, and 70+ tools built in.

When enabled, Hermes runs in a container alongside the rest of the stack, serves its own browser dashboard on internal port 9119, and talks to the selected model provider through an OpenAI-compatible API. End users should enter through `hermes-proxy` on port 9120; direct host access to 9119 is intentionally not bound in the default stack.

## What you get

Hermes ships its own complete web UI — ODS is just packaging it. After `ods enable hermes` + `ods enable hermes-proxy`, you can browse to `http://<device>:9120` (or `hermes.<device>.local:9120` once mDNS announcement lands — see "Roadmap" below). The proxy is the LAN-facing entry; it gates access on ODS's magic-link cookie before forwarding to Hermes's internal port 9119. See [docs/HERMES-SSO.md](HERMES-SSO.md) for the full auth flow. Once past the proxy you find pages for:

- **Chat** — conversational interface with streaming responses + inline tool calls
- **Sessions** — list, switch between, prune past conversations
- **Skills** — view skills Hermes has autonomously created from your interactions; edit or delete
- **Memories** — persistent facts Hermes has learned about you
- **Profiles** — per-user agent contexts (a built-in alternative to running multiple Hermes containers)
- **Cron** — schedule recurring agent tasks
- **Models** — pick which LLM Hermes uses (defaults to your llama-server)
- **Config / Env** — Hermes's own settings
- **Logs / Analytics** — operational visibility

### Dashboard feature cards

The ODS dashboard exposes two separate Hermes entry points:

- **Hermes Agent** opens the authenticated Hermes runtime through `hermes-proxy` on port 9120. It must never link to a raw inference endpoint such as llama-server or LiteLLM.
- **Hermes Single Sign-On** opens the dashboard's **Setup / Owner** page at `/invites`, where operators manage owner cards and temporary support magic links. It is an access-management surface, not a second link to the Hermes runtime.

Hermes readiness is provider-neutral. A healthy local `llama-server` or a healthy LiteLLM route can satisfy the inference dependency, so the same feature contract works for local, cloud, and external-provider installations on Linux, macOS, Windows, and WSL.

## Architecture

```
  Browser
     │  http://<device>:9120
     ▼
  ┌─────────────────────────────────────┐
  │  ods-hermes-proxy                 │
  │    forward_auth → dashboard-api     │
  │    reverse_proxy → ods-hermes     │
  └─────────────────────────────────────┘
                 │
                 │  Docker network: ods-hermes:9119
                 ▼
  ┌─────────────────────────────────────┐
  │  ods-hermes container             │
  │                                     │
  │   hermes gateway run                │
  │     - HERMES_DASHBOARD=1 →          │
  │         React SPA + /api endpoints  │
  │     - scheduler tick() every 60s →  │
  │         fires cron jobs             │
  │     - no messaging adapters         │
  │                                     │
  │   State: /opt/data (HERMES_HOME)    │
  │     mounted from data/hermes/       │
  │                                     │
  │   Tool sandbox: local (in-container)│
  └─────────────────────────────────────┘
                  │
                  │  OpenAI-compatible API
                  ▼
  ┌─────────────────────────────────────┐
  │  llama-server (existing)            │
  │     llama.cpp at :8080/v1           │
  └─────────────────────────────────────┘
```

State layout under `data/hermes/`:

```
data/hermes/
├── config.yaml      # Bootstrapped from our cli-config.yaml.template on first start
├── .env             # Bootstrapped from upstream's .env.example
├── SOUL.md          # Bootstrapped from our SOUL.md.template
├── sessions/        # Per-session chat history
├── memories/        # Persistent agent memories
├── skills/          # Agent-authored skills
├── cron/            # Scheduled tasks
├── plans/           # Active multi-step plans
├── workspace/       # Sandboxed workspace for file ops
├── hooks/           # Custom lifecycle hooks
├── home/            # Per-profile $HOME for subprocesses (git, ssh, npm…)
└── logs/            # Hermes's own logs (separate from Docker logs)
```

## Setup

```bash
# 1. (One-time) Verify ODS's llama-server is running:
ods status llama-server

# 2. Pull + start Hermes and its auth proxy:
ods enable hermes
ods enable hermes-proxy

# 3. Open the auth-gated dashboard:
xdg-open http://localhost:9120
```

The first start takes a minute — image is ~3GB, Hermes runs its `skills_sync.py` bootstrap, and llama-server may cold-load the model on Hermes's first request. Subsequent starts are fast.

## Defaults ODS applies

- **Provider:** `custom` (OpenAI-compatible) pointing at `llama-server:8080/v1`
- **Context:** Hermes requires at least 64K tokens. Local installers run the
  bootstrap model at 64K so the agent works immediately, then move the full model
  and config to the model selector's chosen context after the background model
  upgrade completes. Large-context tiers still use 128K when they select a
  128K-capable model; constrained tiers can remain at a smaller context.
- **Compression:** enabled at `compression.threshold: 0.50` with `target_ratio: 0.20` so long sessions compact before the backend hard-rejects an over-window request.
- **Model name:** `qwen3.5-9b` (ODS's default LLM — to switch models, edit `model.default` in `data/hermes/config.yaml` after first start; there is no env-var hook for this)
- **Persona (`SOUL.md`):** a generalist ODS-aware persona (see `extensions/services/hermes/SOUL.md.template`)
- **Messaging gateways DISABLED:** Telegram / Discord / Slack / WhatsApp / Signal / Teams / Google Chat / Matrix / Mattermost / SMS — all off by default. ODS owner-card users reach Hermes through the ODS Talk mobile portal, while advanced users can still open the full Hermes web dashboard. WhatsApp is pre-seeded as disabled with `bridge_port: 3010` so upstream's default `3000` bridge does not collide with Open WebUI when users intentionally enable it. To enable any platform, see [upstream messaging docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/).
- **Network exposure:** Hermes is **not directly LAN-reachable**. Once the `hermes-proxy` extension is enabled (see [docs/HERMES-SSO.md](HERMES-SSO.md)), the proxy at port 9120 fronts Hermes and gates access on ODS's magic-link cookie. Hermes's own port 9119 is internal-only. To restore direct access (e.g. for testing without auth), re-add a `ports:` binding to `extensions/services/hermes/compose.yaml`.
- **Resource caps:** 4 CPUs / 4GB RAM hard limit, 0.5 CPU / 1GB reservation. Hermes's playwright + ML deps can be hungry; adjust in `extensions/services/hermes/compose.yaml` if needed.

## Configuration

Three layers, highest to lowest precedence:

1. **Edit `data/hermes/config.yaml`** directly — Hermes's own config file, copied from our template on first start. Survives container restarts. Reset by deleting and restarting. **The model name lives here**, not in env.
2. **Set env vars in ODS's `.env`** — `HERMES_LLM_BASE_URL`, `HERMES_LLM_API_KEY`, `HERMES_LANGUAGE`, optional `WHATSAPP_*` gateway settings, and the `HERMES_PROXY_*` proxy settings. Hermes itself has no host-port env knob in the auth-gated stack; the LAN-facing port is `HERMES_PROXY_PORT`.
3. **Fall back to ODS's defaults** — defined in `extensions/services/hermes/cli-config.yaml.template`.

To bring up Hermes pointing at a different LLM (e.g. OpenRouter, OpenAI, Anthropic), edit `data/hermes/config.yaml`'s `model.provider` and `model.base_url` and restart. The whole gamut of provider options is listed in the upstream config — Hermes supports OpenRouter / Anthropic / OpenAI / Hugging Face / NVIDIA NIM / z.ai / Kimi / Gemini / Ollama Cloud / LM Studio / etc. out of the box.

For local backends, keep `model.context_length` and `auxiliary.compression.context_length` aligned with `.env`'s `CTX_SIZE` / `MAX_CONTEXT`. Values below 64000 will make Hermes reject prompts; values above the server's real context can produce `context length exceeded` / `max compression attempts reached` loops.

## Security posture

- **`--insecure` is enabled inside the container.** Hermes's dashboard refuses non-loopback binds without it. ODS accepts that trade-off only because port 9119 is not host-bound in the default stack; the LAN-facing entry is the magic-link-gated proxy on port 9120. Do not add a public 9119 host binding.
- **The container runs as a non-root user** (UID 10000 by default, remappable via `HERMES_UID`). The entrypoint drops privileges via `gosu` before any agent code runs.
- **The container has full network access** within ODS's bridge net — Hermes can make outbound HTTP requests for tools like `web_search`. If you want to restrict this, add an iptables firewall rule on the host or run Hermes behind a forward proxy.
- **No APE policy enforcement yet.** Hermes's 70+ tools include shell + file write. The base config defaults toward less-risky tools, but Hermes can still execute shell commands inside its sandbox container. APE policy wrapping is a planned follow-up; until then, the trust model is "the user authenticated to Hermes is trusted to use the local container."

## How to bump the image pin

Hermes is a young, fast-moving project. ODS pins a reviewed upstream image tag in `compose.yaml` instead of auto-tracking `:latest`. Operators can temporarily override it with `HERMES_AGENT_IMAGE`, and can provide `HERMES_AGENT_IMAGE_FALLBACK` for registry hotfixes, but changing the shipped default is a deliberate review-and-smoke-test pass:

```bash
# 1. Pick a published upstream image tag.
curl -s 'https://hub.docker.com/v2/repositories/nousresearch/hermes-agent/tags?page_size=25' \
  | jq -r '.results[] | [.name, .last_updated] | @tsv'

# 2. Verify Docker can resolve the tag on a clean machine.
docker manifest inspect nousresearch/hermes-agent:<new-tag> >/dev/null

# 3. Review upstream release notes / commits. Skim breaking changes,
#    config-format migrations, removed env vars, and dashboard changes.

# 4. Update:
#    - extensions/services/hermes/compose.yaml
#    - installers/phases/08-images.sh
#    - config/dependency-lock.json
#    - this bump-history table

# 5. Smoke test:
#    ods restart hermes
#    docker inspect --format '{{.State.Health.Status}}' ods-hermes
#    curl http://localhost:9120/health
#    # NOTE: most /api/* routes are auth-gated; /api/status is the public
#    # JSON-backed endpoint used by ODS's health metadata and Docker probe.
#    open http://localhost:9120, sign in, send a chat, verify tool call

# 5. If it works, commit. If config.yaml format has changed, document the
#    migration in this file's "Bump history" section below.
```

## Roadmap (deferred from v1)

These were in the original integration plan but cut once we discovered Hermes ships a complete browser surface:

- **mDNS announcement** — register `hermes.<device>.local` in the ODS mDNS announcer. ✅ shipped as [#1167](https://github.com/Osmantic/ODS/pull/1167) (stacked on [#1152](https://github.com/Osmantic/ODS/pull/1152)).
- **Magic-link SSO** — magic-link cookie gates access to Hermes via the new `hermes-proxy` Caddy sidecar. ✅ shipped — see [docs/HERMES-SSO.md](HERMES-SSO.md). Known limitation: single shared Hermes for all users; real per-user isolation would require per-user containers.
- **APE policy integration** — route Hermes's tool calls through APE for allow/deny + audit. APE is already in the stack; needs a small adapter inside or in front of Hermes.
- **Voice in/out from ODS's whisper + kokoro** — Hermes has its own audio pipeline (the image bundles ffmpeg + playwright); verify whether it already proxies to local TTS/STT services or whether we need to wire that ourselves.
- **ODS-side status panel** — surface Hermes's session count + skill inventory in the ODS dashboard. Lower priority since Hermes has its own `AnalyticsPage`.
- **Per-user Hermes containers** — if true multi-user becomes a felt need, spawn one Hermes per magic-link target_username and have the proxy route based on the redeemed identity. See [docs/HERMES-SSO.md](HERMES-SSO.md#future-option-b--per-user-hermes).

## Troubleshooting

### `docker inspect ods-hermes` is not healthy, or `localhost:9120` returns 502

Hermes hasn't finished bootstrapping. Watch the logs:

```bash
docker logs -f ods-hermes
```

First start does a ~30s skills sync; subsequent starts are fast.

### Chat slows down after many ODS Talk or Hermes sessions

Some upstream Hermes builds can leave `tui_gateway.slash_worker` child
processes running after sessions that use `/slash` commands such as
`/no_think`. Each worker can hold model memory, so a long day of owner-card or
fleet-test sessions may create memory pressure even though the main Hermes
container looks healthy.

Check with:

```bash
ods doctor
docker exec ods-hermes sh -c "ps -eo pid=,etimes=,args= | grep '[t]ui_gateway[.]slash_worker'"
```

Clean up workers that exceed the age/count policy:

```bash
ods repair hermes-workers
```

Policy knobs:

```bash
HERMES_SLASH_WORKER_MAX_COUNT=8
HERMES_SLASH_WORKER_MAX_AGE_SECONDS=3600
```

`ods doctor` reports high worker counts. `ods repair hermes-workers` is
explicit and manual; ODS does not run an automatic process killer for
active Hermes sessions.

### Hermes can't reach the LLM

Inside the container, `llama-server:8080` should resolve to the llama-server container. Test with:

```bash
docker exec ods-hermes curl -fs http://llama-server:8080/v1/models
```

If that fails, the most likely cause is that the Hermes container isn't on ODS's docker network. Check `docker network inspect ods_default`.

### WhatsApp reports port 3000 already in use

ODS's bundled Hermes config uses `platforms.whatsapp.extra.bridge_port: 3010`, and the Hermes container does not bind that bridge to the host. If you see a WhatsApp bridge conflict on port 3000, you are probably running Hermes Agent natively, running upstream Hermes with host networking, or using an older `data/hermes/config.yaml` copied before this default existed.

Set the WhatsApp bridge port in `data/hermes/config.yaml` and restart Hermes:

```yaml
platforms:
  whatsapp:
    enabled: true
    extra:
      bridge_port: 3010
```

### Hermes says context length exceeded or max compression attempts reached

Check that the running backend and Hermes agree on context:

```bash
grep -E "^(CTX_SIZE|MAX_CONTEXT)=" .env
grep -n "context_length\|threshold\|target_ratio" data/hermes/config.yaml
```

For Hermes, `CTX_SIZE` / `MAX_CONTEXT`, `model.context_length`, and `auxiliary.compression.context_length` should be at least `65536`. Fresh local installs use `65536` during bootstrap, then keep the model selector's chosen full-model context after the swap. On larger tiers that may be `131072`; on constrained tiers it can stay lower.

### Sessions / memories / skills disappeared after upgrade

The image pin protects you from accidental version drift, but if you DID bump and lose data, it's almost certainly because Hermes's config format changed. Check `data/hermes/config.yaml` against upstream's current `cli-config.yaml.example`. The container's first start regenerates `config.yaml` from our template only if it doesn't exist — your old config sticks around.

### "I don't want Hermes anymore"

```bash
ods disable hermes              # stops the container
rm -rf data/hermes                # wipes all sessions / memories / skills
```

The container image stays cached — `docker image prune` removes it.

## Upstream attribution

Hermes Agent is © 2026 Nous Research, MIT-licensed. ODS's contribution is the packaging layer (`extensions/services/hermes/`) — no code is forked from upstream. The pinned image is pulled directly from `docker.io/nousresearch/hermes-agent`.

When promoting / talking about this extension, the convention is: "Hermes Agent (from Nous Research) — packaged for ODS."

## Bump history

| Date | Pinned image | Notes |
|---|---|---|
| 2026-06-01 | `nousresearch/hermes-agent:v2026.5.16` | Replace removed upstream `sha-*` tag with a published version tag; add `HERMES_AGENT_IMAGE` override/fallback path. |
| 2026-05-12 | `dd0923bb89ed2dd56f82cb63656a1323f6f42e6f` | Initial integration. |
