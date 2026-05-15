# Migrating from OpenClaw to Hermes Agent

As of **2026-05-12**, Dream Server's default agent is [Hermes Agent](HERMES.md) (Nous Research, MIT). OpenClaw is deprecated and will be removed in the next release.

This document covers the migration path for existing Dream Server installs that have OpenClaw enabled.

## TL;DR

- **New installs:** Hermes is installed by default; OpenClaw is not. No action needed.
- **Existing installs:** OpenClaw keeps running as-is until you remove it. Hermes can be enabled in parallel any time. The two agents are independent — neither shares storage with the other.
- **No automatic data migration.** Sessions, memories, skills, and cron jobs in OpenClaw do not transfer. The migration is a clean break.

## Platform support in this release

The default-agent swap is wired through the **Linux, macOS, and Windows** installers. New installs select Hermes for agent-enabled profiles and keep OpenClaw disabled unless the operator explicitly opts in with `--openclaw` on Linux/macOS or `-OpenClaw` on Windows.

## Why the swap

| | OpenClaw | Hermes Agent |
|---|---|---|
| Project age | older, stable | younger (Feb 2026), fast-moving |
| Browser dashboard | yes (one surface) | yes (chat / sessions / skills / memories / cron / profiles / models / analytics / logs) |
| Persistent memory | basic | first-class, agent-curated, with FTS5 cross-session recall |
| Skills | static config | **agent autonomously creates** skill documents from successful runs |
| Tool count | ~12 | 70+ |
| Multi-platform | Discord/Telegram/Signal | Telegram/Discord/Slack/WhatsApp/Signal/Teams/Matrix/Mattermost/SMS/email — gateway abstraction |
| Voice | bring-your-own | OpenAI-compatible STT/TTS — wired through Dream's whisper + kokoro out of the box |
| Policy / audit | none | APE policy plugin (pre_tool_call hook) routes every tool call through Dream's policy engine |
| License | OSS | MIT |

The deciding factor was the self-improving loop: Hermes writes Markdown skill files after solving hard problems and reloads them automatically on the next similar task. That capability does not exist in OpenClaw.

## Coexistence (deprecation release)

In this release both agents are installable:

```bash
dream enable hermes
dream enable hermes-proxy  # recommended LAN-facing auth gate
dream enable openclaw      # still available (deprecated)
```

Ports do not conflict — Hermes is internal on 9119 and is reached through hermes-proxy on 9120; OpenClaw is on 7860.

The default at install time has flipped: `install.sh` no longer enables OpenClaw without `--openclaw`. Existing installs that already had `ENABLE_OPENCLAW=true` keep it enabled through `dream upgrade`; nothing is removed for you.

## Clean-cut migration

If you want to move now:

```bash
# 1. Enable Hermes (parallel to OpenClaw — they don't conflict)
dream enable hermes
dream enable hermes-proxy

# 2. Verify Hermes is healthy
docker inspect --format '{{.State.Health.Status}}' dream-hermes
curl http://localhost:9120/health

# 3. Re-create any cron jobs / important sessions in Hermes via its
#    dashboard at http://<device>:9120. There is no import.

# 4. When you're satisfied, stop OpenClaw
dream disable openclaw

# 5. Optionally archive OpenClaw data (it's untouched by the swap)
mv data/openclaw data/openclaw.archive.$(date +%Y%m%d)
```

If you want to keep using OpenClaw, you can — until the next release. After that, `dream upgrade` will remove the OpenClaw extension and warn (not error) if `ENABLE_OPENCLAW=true` is still set.

## n8n flows that target OpenClaw

`config/n8n/openclaw-agent-trigger.json` still ships in this release and continues to point at OpenClaw's port 7860. A `hermes-agent-trigger.json` ships alongside it for Hermes. In the default auth-gated stack, users enter through port 9120 and containers call Hermes on the internal Docker network at `dream-hermes:9119`.

In the removal release, only the Hermes trigger ships.

## What will be removed in the next release

For planning, here's what the removal PR drops:

- `extensions/services/openclaw/` (manifest, compose, README — entire directory)
- `docs/OPENCLAW-INTEGRATION.md`
- `config/openclaw/` (inject-token.js, openclaw.json, pro.json, openclaw-strix-halo.json, workspace/SYSTEM.md)
- `scripts/systemd/openclaw-session-cleanup.service` + `.timer`
- `config/n8n/openclaw-agent-trigger.json`
- `tests/test-openclaw-inject-token.sh`
- All `ENABLE_OPENCLAW` / `--openclaw` / `--no-openclaw` references in `install-core.sh` and `dream-uninstall.sh`
- The OpenClaw row from `extensions/CATALOG.md`
- `resources/blog/m1-fully-local-openclaw-launch.md` moves to `resources/legacy/blog/`

If any of these touch a workflow you care about, please open an issue before the next release ships so we can either preserve it (rename / refactor under the Hermes namespace) or document a clean alternative.

## Questions / migration pain

File an issue at <https://github.com/Light-Heart-Labs/DreamServer/issues> with the `migration` label.
