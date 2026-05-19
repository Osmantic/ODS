# Hermes Agent

Dream Server ships **Hermes Agent** — the [Nous Research open-source agent](https://github.com/nousresearch/hermes-agent) packaged as a Dream Server service. Hermes is a self-improving generalist agent with persistent memory, autonomous skill creation, and 70+ tools built in.

When enabled, Hermes runs in a container alongside the rest of the stack, serves its own browser dashboard on internal port 9119, and talks to the local LLM via an OpenAI-compatible API. End users should enter through `hermes-proxy` on port 9120; direct host access to 9119 is intentionally not bound in the default stack.

## What you get

Hermes ships its own complete web UI — Dream Server is just packaging it. After `dream enable hermes` + `dream enable hermes-proxy`, you can browse to `http://<device>:9120` (or `hermes.<device>.local:9120` once mDNS announcement lands — see "Roadmap" below). The proxy is the LAN-facing entry; it gates access on Dream Server's magic-link cookie before forwarding to Hermes's internal port 9119. See [docs/HERMES-SSO.md](HERMES-SSO.md) for the full auth flow. Once past the proxy you find pages for:

- **Chat** — conversational interface with streaming responses + inline tool calls
- **Sessions** — list, switch between, prune past conversations
- **Skills** — view skills Hermes has autonomously created from your interactions; edit or delete
- **Memories** — persistent facts Hermes has learned about you
- **Profiles** — per-user agent contexts (a built-in alternative to running multiple Hermes containers)
- **Cron** — schedule recurring agent tasks
- **Models** — pick which LLM Hermes uses (defaults to your llama-server)
- **Config / Env** — Hermes's own settings
- **Logs / Analytics** — operational visibility

## Architecture

```
  Browser
     │  http://<device>:9120
     ▼
  ┌─────────────────────────────────────┐
  │  dream-hermes-proxy                 │
  │    forward_auth → dashboard-api     │
  │    reverse_proxy → dream-hermes     │
  └─────────────────────────────────────┘
                 │
                 │  Docker network: dream-hermes:9119
                 ▼
  ┌─────────────────────────────────────┐
  │  dream-hermes container             │
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
# 1. (One-time) Verify Dream Server's llama-server is running:
dream status llama-server

# 2. Pull + start Hermes and its auth proxy:
dream enable hermes
dream enable hermes-proxy

# 3. Open the auth-gated dashboard:
xdg-open http://localhost:9120
```

The first start takes a minute — image is ~3GB, Hermes runs its `skills_sync.py` bootstrap, and llama-server may cold-load the model on Hermes's first request. Subsequent starts are fast.

## Defaults Dream Server applies

- **Provider:** `custom` (OpenAI-compatible) pointing at `llama-server:8080/v1`
- **Context:** Hermes requires at least 64K tokens. Local installers run the bootstrap model at 64K so Hermes works immediately, then move the full model and Hermes config to 128K after the background model upgrade completes.
- **Compression:** enabled at `compression.threshold: 0.50` with `target_ratio: 0.20` so long sessions compact before the backend hard-rejects an over-window request.
- **Model name:** `qwen3.5-9b` (Dream Server's default LLM — to switch models, edit `model.default` in `data/hermes/config.yaml` after first start; there is no env-var hook for this)
- **Persona (`SOUL.md`):** a generalist Dream-Server-aware persona (see `extensions/services/hermes/SOUL.md.template`)
- **Messaging gateways DISABLED:** Telegram / Discord / Slack / WhatsApp / Signal / Teams / Google Chat / Matrix / Mattermost / SMS — all off by default. Dream Server users reach Hermes via the web dashboard. To enable any platform, see [upstream messaging docs](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/).
- **Network exposure:** Hermes is **not directly LAN-reachable**. Once the `hermes-proxy` extension is enabled (see [docs/HERMES-SSO.md](HERMES-SSO.md)), the proxy at port 9120 fronts Hermes and gates access on Dream Server's magic-link cookie. Hermes's own port 9119 is internal-only. To restore direct access (e.g. for testing without auth), re-add a `ports:` binding to `extensions/services/hermes/compose.yaml`.
- **Resource caps:** 4 CPUs / 4GB RAM hard limit, 0.5 CPU / 1GB reservation. Hermes's playwright + ML deps can be hungry; adjust in `extensions/services/hermes/compose.yaml` if needed.

## Configuration

Three layers, highest to lowest precedence:

1. **Edit `data/hermes/config.yaml`** directly — Hermes's own config file, copied from our template on first start. Survives container restarts. Reset by deleting and restarting. **The model name lives here**, not in env.
2. **Set env vars in Dream Server's `.env`** — `HERMES_LLM_BASE_URL`, `HERMES_LLM_API_KEY`, `HERMES_LANGUAGE`, and the `HERMES_PROXY_*` proxy settings. Hermes itself has no host-port env knob in the auth-gated stack; the LAN-facing port is `HERMES_PROXY_PORT`.
3. **Fall back to Dream Server's defaults** — defined in `extensions/services/hermes/cli-config.yaml.template`.

To bring up Hermes pointing at a different LLM (e.g. OpenRouter, OpenAI, Anthropic), edit `data/hermes/config.yaml`'s `model.provider` and `model.base_url` and restart. The whole gamut of provider options is listed in the upstream config — Hermes supports OpenRouter / Anthropic / OpenAI / Hugging Face / NVIDIA NIM / z.ai / Kimi / Gemini / Ollama Cloud / LM Studio / etc. out of the box.

For local backends, keep `model.context_length` and `auxiliary.compression.context_length` aligned with `.env`'s `CTX_SIZE` / `MAX_CONTEXT`. Values below 64000 will make Hermes reject prompts; values above the server's real context can produce `context length exceeded` / `max compression attempts reached` loops.

## Security posture

- **`--insecure` is enabled inside the container.** Hermes's dashboard refuses non-loopback binds without it. Dream Server accepts that trade-off only because port 9119 is not host-bound in the default stack; the LAN-facing entry is the magic-link-gated proxy on port 9120. Do not add a public 9119 host binding.
- **The container runs as a non-root user** (UID 10000 by default, remappable via `HERMES_UID`). The entrypoint drops privileges via `gosu` before any agent code runs.
- **The container has full network access** within Dream Server's bridge net — Hermes can make outbound HTTP requests for tools like `web_search`. If you want to restrict this, add an iptables firewall rule on the host or run Hermes behind a forward proxy.
- **No APE policy enforcement yet.** Hermes's 70+ tools include shell + file write. The base config defaults toward less-risky tools, but Hermes can still execute shell commands inside its sandbox container. APE policy wrapping is a planned follow-up; until then, the trust model is "the user authenticated to Hermes is trusted to use the local container."

## How to bump the SHA pin

Hermes is a young, fast-moving project (~3 months old as of v1 integration). The SHA pin in `compose.yaml` lets us audit upstream changes deliberately rather than auto-tracking `:latest`. Bumping is a 5-minute pass:

```bash
# 1. Pick the new SHA. Upstream publishes a sha-<full-sha> tag for every
#    main push, so any commit on NousResearch/hermes-agent's main is fair game.
gh api repos/NousResearch/hermes-agent/commits/main --jq '{sha,date:.commit.author.date,msg:.commit.message}'

# 2. Read the diff since our current pin. Skim breaking changes,
#    config-format migrations, removed env vars.
gh api repos/NousResearch/hermes-agent/compare/<old-sha>...<new-sha> --jq '.commits[].commit.message'

# 3. Update extensions/services/hermes/compose.yaml — the only place the
#    pin appears.

# 4. Smoke test:
#    dream restart hermes
#    docker inspect --format '{{.State.Health.Status}}' dream-hermes
#    curl http://localhost:9120/health
#    # NOTE: most /api/* routes are auth-gated; /api/status is the public
#    # JSON-backed endpoint used by Dream's health metadata and Docker probe.
#    open http://localhost:9120, sign in, send a chat, verify tool call

# 5. If it works, commit. If config.yaml format has changed, document the
#    migration in this file's "Bump history" section below.
```

## Roadmap (deferred from v1)

These were in the original integration plan but cut once we discovered Hermes ships a complete browser surface:

- **mDNS announcement** — register `hermes.<device>.local` in the Dream Server mDNS announcer. ✅ shipped as [#1167](https://github.com/Light-Heart-Labs/DreamServer/pull/1167) (stacked on [#1152](https://github.com/Light-Heart-Labs/DreamServer/pull/1152)).
- **Magic-link SSO** — magic-link cookie gates access to Hermes via the new `hermes-proxy` Caddy sidecar. ✅ shipped — see [docs/HERMES-SSO.md](HERMES-SSO.md). Known limitation: single shared Hermes for all users; real per-user isolation would require per-user containers.
- **APE policy integration** — route Hermes's tool calls through APE for allow/deny + audit. APE is already in the stack; needs a small adapter inside or in front of Hermes.
- **Voice in/out from Dream Server's whisper + kokoro** — Hermes has its own audio pipeline (the image bundles ffmpeg + playwright); verify whether it already proxies to local TTS/STT services or whether we need to wire that ourselves.
- **Dream-side status panel** — surface Hermes's session count + skill inventory in the Dream dashboard. Lower priority since Hermes has its own `AnalyticsPage`.
- **Per-user Hermes containers** — if true multi-user becomes a felt need, spawn one Hermes per magic-link target_username and have the proxy route based on the redeemed identity. See [docs/HERMES-SSO.md](HERMES-SSO.md#future-option-b--per-user-hermes).

## Troubleshooting

### `docker inspect dream-hermes` is not healthy, or `localhost:9120` returns 502

Hermes hasn't finished bootstrapping. Watch the logs:

```bash
docker logs -f dream-hermes
```

First start does a ~30s skills sync; subsequent starts are fast.

### Hermes can't reach the LLM

Inside the container, `llama-server:8080` should resolve to the llama-server container. Test with:

```bash
docker exec dream-hermes curl -fs http://llama-server:8080/v1/models
```

If that fails, the most likely cause is that the Hermes container isn't on Dream Server's docker network. Check `docker network inspect dream-server_default`.

### Hermes says context length exceeded or max compression attempts reached

Check that the running backend and Hermes agree on context:

```bash
grep -E "^(CTX_SIZE|MAX_CONTEXT)=" .env
grep -n "context_length\|threshold\|target_ratio" data/hermes/config.yaml
```

For Hermes, `CTX_SIZE` / `MAX_CONTEXT`, `model.context_length`, and `auxiliary.compression.context_length` should be at least `65536`. Fresh local installs use `65536` during bootstrap and `131072` after the full model swap.

### Sessions / memories / skills disappeared after upgrade

The SHA pin protects you from accidental version drift, but if you DID bump and lose data, it's almost certainly because Hermes's config format changed. Check `data/hermes/config.yaml` against upstream's current `cli-config.yaml.example`. The container's first start regenerates `config.yaml` from our template only if it doesn't exist — your old config sticks around.

### "I don't want Hermes anymore"

```bash
dream disable hermes              # stops the container
rm -rf data/hermes                # wipes all sessions / memories / skills
```

The container image stays cached — `docker image prune` removes it.

## Upstream attribution

Hermes Agent is © 2026 Nous Research, MIT-licensed. Dream Server's contribution is the packaging layer (`extensions/services/hermes/`) — no code is forked from upstream. The pinned image is pulled directly from `docker.io/nousresearch/hermes-agent`.

When promoting / talking about this extension, the convention is: "Hermes Agent (from Nous Research) — packaged for Dream Server."

## Bump history

| Date | Pinned SHA | Notes |
|---|---|---|
| 2026-05-12 | `dd0923bb89ed2dd56f82cb63656a1323f6f42e6f` | Initial integration. |
