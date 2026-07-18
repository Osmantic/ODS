# Omnigent — Agent Meta-Harness on ODS

Omnigent ([omnigent-ai/omnigent](https://github.com/omnigent-ai/omnigent),
Apache 2.0) is a meta-harness: one server that coordinates sandboxed
sessions of CLI coding agents — Claude Code, Codex, OpenCode, Cursor, Pi —
behind a uniform API, with stateful policies (cost budgets, permission
rules) and live session sharing.

On ODS it turns "we ship one coding harness (OpenCode)" into "run any
coding harness, governed, against your local models."

> **Alpha software.** Omnigent moves fast. ODS pins the server image
> (`OMNIGENT_IMAGE_TAG`, currently `v0.5.1`); bump it deliberately and
> re-run the smoke checks at the bottom of this page.

## The split you must understand first

Enabling the extension gives you the **server only**:

| Piece | Where it runs | Installed by |
|---|---|---|
| Omnigent server + web UI | Docker (`ods-omnigent`, port 6767) | `ods enable omnigent` |
| Runners (execute agents, sandbox them) | **Your host machine** | You, manually (below) |
| CLI harnesses (claude, codex, opencode, …) | Your host, driven by runners | You, per harness |

This is upstream's design, not an ODS limitation: the server image
deliberately contains no harness SDKs, no tmux, and no LLM API keys.
It also means the sandboxing runs directly on the host, where it
belongs — no Docker-in-Docker.

## 1. Enable the server

```bash
ods enable omnigent
echo "OMNIGENT_ACCOUNTS_COOKIE_SECRET=$(openssl rand -hex 32)" >> .env
ods restart omnigent
```

Open `http://localhost:6767` and create the first account — Omnigent's
accounts flow is first-user-is-admin (verified against `v0.5.1`; no
pre-generated password exists). Auth is on by default; the port is
bound to `127.0.0.1` like every ODS service.

## 2. Install a runner on the host

Host prerequisites (verified on a clean macOS install):

- **tmux** — Omnigent spawns native-terminal harnesses inside tmux;
  without it sessions fail with "Native OpenCode terminal failed to
  start". `brew install tmux` (macOS) / distro package (Linux).
- **OpenCode version pin** — Omnigent v0.5.1 requires OpenCode
  `>=1.17.7,<1.18.0`. ODS's own opencode service may install a newer
  CLI; if the versions clash, install a pinned copy for the runner
  (`npm i -g opencode-ai@1.17.7`) and make sure it resolves first on
  the PATH of the shell that runs `omnigent host`. Re-check this pin
  whenever you bump `OMNIGENT_IMAGE_TAG`.

Install the runner CLI, any one of:

```bash
curl -fsSL https://raw.githubusercontent.com/omnigent-ai/omnigent/main/scripts/install_oss.sh | sh
# or
uv tool install omnigent
# or
brew install omnigent-ai/tap/omnigent
```

Register the host as a runner against the local server:

```bash
omnigent host --server http://localhost:6767
```

Keep this daemon running (its own terminal, tmux window, or a service
manager) — if it exits, session launches fail with
`(504): host did not respond to launch request`.

or run a single agent session against it:

```bash
omnigent run path/to/agent.yaml --server http://localhost:6767
```

## 3. Point harnesses at your local models

Run `omnigent setup` and choose the **gateway** (OpenAI-compatible)
credential type:

- **Base URL:** `http://localhost:4000/v1` (LiteLLM — recommended, works
  in local, cloud, and hybrid modes) or your llama-server endpoint
  (`http://localhost:11434/v1` on Linux Docker installs,
  `http://localhost:8080/v1` on native macOS/Windows paths).
- **API key:** any non-empty string (llama-server does not validate it,
  but the SDKs require the field — same trick as `HERMES_LLM_API_KEY`).

Harness selection lives in the agent YAML:

```yaml
executor:
  harness: opencode   # or: claude-sdk, claude-native, codex, cursor, pi
```

Which harnesses make sense fully local:

| Harness | Fully local? | Notes |
|---|---|---|
| `opencode` | Yes | Same harness ODS already bundles; gateway-friendly |
| `codex` | Yes | Works against OpenAI-compatible gateways |
| `claude-sdk` / `claude-native` | Partly | Best with Anthropic keys or a Claude subscription; local via gateway is possible but harness features assume Claude models |
| `cursor`, `pi` | Cloud-leaning | Expect their own accounts/keys |

## 4. Policies

Omnigent policies are **stateful and enforced in code**, not prompts:
spend budgets that pause an agent at a threshold, permission rules,
network constraints. The budget policies are the headline win for ODS
**cloud/hybrid mode** — nothing else in the stack stops an agent from
burning API spend. In pure local mode they matter less (your inference
is free).

### How this layers with APE

- **Omnigent** governs at the *session* level: cost, permissions,
  sharing.
- **APE** governs at the *tool-call* level: command allowlists, path
  guards, rate limits, tamper-evident audit log.

They stack conceptually, but today **Omnigent-launched agents do not
flow through APE** — their tool calls are not in APE's audit log. If
APE auditing is part of your threat model, treat Omnigent sessions
accordingly. Closing this gap is tracked in
[Osmantic/ODS#1867](https://github.com/Osmantic/ODS/issues/1867).

## Upgrading

```bash
# .env: bump the pin
OMNIGENT_IMAGE_TAG=v0.6.0
ods restart omnigent
```

Then smoke-check:

1. `curl -fsS http://localhost:6767/health` returns 200.
2. Web UI login works (cookie secret unchanged → sessions survive).
3. A host runner registers and completes a trivial `omnigent run`.

## State & reset

Everything lives in `data/omnigent/` — SQLite DB (`artifacts/chat.db`),
artifacts, account data. `ods disable omnigent` stops the service;
deleting `data/omnigent/` is a factory reset.
