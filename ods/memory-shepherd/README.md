# Memory Shepherd

Periodic memory reset for persistent LLM agents. Keeps agents on-mission by archiving their scratch notes and resetting their memory files to a known-good baseline.

## The Problem

Persistent LLM agents accumulate state over time. Their working memory fills with stale notes, outdated context, and resolved task details. Without intervention:

- Agents **drift from their defined roles**, gradually shifting behavior as old context influences new decisions
- Context becomes **bloated with irrelevant information**, degrading response quality
- Agents sometimes **rewrite their own instructions**, subtly altering their operating parameters
- Stale context creates **confusion between past and present tasks**

## The Solution

Memory Shepherd implements a simple pattern:

1. **Baseline** — A curated identity document (who the agent is, its rules, capabilities, and pointers) lives above a `---` separator in the agent's `MEMORY.md`
2. **Scratch notes** — The agent writes working notes below the separator during operation
3. **Reset cycle** — On a schedule (default: every 3 hours), scratch notes are archived and `MEMORY.md` is restored to the baseline

The result: agents always start from a clean, operator-controlled state while their accumulated notes are preserved in timestamped archives.

## How It Works

```
MEMORY.md
┌─────────────────────────────────────────┐
│  ## Who I Am                            │
│  ## Critical Rules                      │  ← Baseline (operator-controlled)
│  ## Capabilities                        │     Never modified by the agent
│  ## Where to Find Things                │
├─────────────────────────────────────────┤
│  ---                                    │  ← Separator (the contract)
├─────────────────────────────────────────┤
│  ## Scratch Notes                       │
│  - Found bug in auth module             │  ← Agent scratch notes
│  - PR #42 approved, waiting on CI       │     Written during operation
│  - Need to follow up on deployment      │     Archived + cleared on reset
└─────────────────────────────────────────┘
```

Each reset cycle:
1. Reads the current `MEMORY.md`
2. Finds the last `---` separator
3. Extracts everything below it (scratch notes)
4. Archives scratch notes to a timestamped file
5. Atomically replaces `MEMORY.md` with the baseline
6. Cleans up archives older than 30 days (configurable)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/Osmantic/ODS.git
cd ODS/memory-shepherd

# Create your config from the example
cp memory-shepherd.conf.example memory-shepherd.conf

# Edit the config — point it at your agent's MEMORY.md
vim memory-shepherd.conf

# Create a baseline for your agent
cp baselines/example-agent-MEMORY.md baselines/my-agent-MEMORY.md
vim baselines/my-agent-MEMORY.md

# Test a manual reset
./memory-shepherd.sh my-agent

# Install systemd timers for automatic resets
./install.sh
```

## Configuration Reference

Memory Shepherd uses an INI-style config file. The search order is:

1. `$MEMORY_SHEPHERD_CONF` environment variable
2. `./memory-shepherd.conf` (next to the script)
3. `/etc/memory-shepherd/memory-shepherd.conf`

### `[general]` Section

| Key | Default | Description |
|-----|---------|-------------|
| `baseline_dir` | `./baselines` | Directory containing baseline MEMORY.md files |
| `archive_dir` | `./archives` | Root directory for archived scratch notes |
| `max_memory_size` | `16384` | Max memory file size (bytes) before warning |
| `archive_retention_days` | `30` | Delete archives older than this |
| `separator` | `---` | The line that separates baseline from scratch notes |

### Agent Sections

Each `[agent-name]` section defines one managed agent:

| Key | Required | Description |
|-----|----------|-------------|
| `memory_file` | Yes* | Absolute path to the agent's MEMORY.md |
| `baseline` | Yes | Filename of the baseline in `baseline_dir` |
| `archive_subdir` | No | Subdirectory under `archive_dir` (default: agent name) |
| `remote_host` | No | Hostname/IP for remote agents (triggers SCP mode) |
| `remote_user` | No | SSH user for remote agents (default: current user) |
| `remote_memory` | Yes* | Path to MEMORY.md on the remote machine |

*`memory_file` is required for local agents; `remote_memory` is required when `remote_host` is set.

### Example Config

```ini
[general]
baseline_dir=./baselines
archive_dir=./archives
max_memory_size=16384
archive_retention_days=30

[code-reviewer]
memory_file=/home/deploy/code-reviewer/.openclaw/workspace/MEMORY.md
baseline=code-reviewer-MEMORY.md

[monitor-bot]
memory_file=/home/deploy/monitor/.openclaw/workspace/MEMORY.md
baseline=monitor-bot-MEMORY.md
archive_subdir=monitor

[remote-agent]
remote_host=10.0.0.50
remote_user=deploy
remote_memory=/home/deploy/agent/.openclaw/workspace/MEMORY.md
baseline=remote-agent-MEMORY.md
```

## The `---` Separator Convention

The separator is a contract between the operator and the agent:

**Above the line** is the operator's domain. It defines who the agent is, what rules it follows, what tools it has, and where to find things. The agent must never modify this section.

**Below the line** is the agent's domain. It's scratch space for working notes, observations, task tracking, and anything the agent needs during its current work cycle.

For this contract to work, the agent needs to know about it. Include a brief explanation in your baseline:

```markdown
*This is your baseline memory. You can add notes below the --- line.
Your additions will be periodically archived and this file reset to baseline.*
```

See [docs/WRITING-BASELINES.md](docs/WRITING-BASELINES.md) for a comprehensive guide to writing effective baselines.

## Writing Effective Baselines

A good baseline answers: "If this agent lost all memory, what does it need to start working correctly?"

Key sections:
- **Identity** — Role, purpose, who it reports to
- **Rules** — 5-7 hard boundaries (specific and actionable, not vague)
- **Autonomy tiers** — What it can do freely vs. what needs approval
- **Capabilities** — Models, tools, services it can access
- **Pointers** — Where to find docs, repos, configs (point, don't paste)
- **Memory system** — Explain the reset cycle so the agent writes better notes

**Size sweet spot:** 12-20KB. Under 5KB means the agent will spend cycles rediscovering context. Over 25KB means you're probably including content that belongs in separate docs.

The full guide is at [docs/WRITING-BASELINES.md](docs/WRITING-BASELINES.md).

## Systemd Timers

`install.sh` creates systemd timer/service pairs:

- **`memory-shepherd.timer`** — Resets all agents every 3 hours (enabled by default)
- **`memory-shepherd-<agent>.timer`** — Per-agent timer with staggered scheduling (installed but not enabled)

```bash
# Install timers (detects root vs. user mode automatically)
./install.sh

# Preview without installing
./install.sh --dry-run

# Custom systemd prefix
./install.sh --prefix /etc/systemd/system

# Remove all timers
./uninstall.sh

# Manual reset
./memory-shepherd.sh all           # Reset all agents
./memory-shepherd.sh code-reviewer # Reset one agent

# Check timer status
systemctl list-timers | grep memory
journalctl -u memory-shepherd      # View logs
```

## Optional: File Integrity Protection

The baseline files in `baselines/` are critical — if they get corrupted or overwritten, your agents get bad resets. For production deployments, consider:

**Immutable flag (simple):**
```bash
# Prevent modification of baseline files
sudo chattr +i baselines/*.md

# To update a baseline, temporarily remove the flag
sudo chattr -i baselines/my-agent-MEMORY.md
vim baselines/my-agent-MEMORY.md
sudo chattr +i baselines/my-agent-MEMORY.md
```

**Checksum validation (paranoid):**
```bash
# Generate checksums after writing baselines
sha256sum baselines/*.md > baselines/.checksums

# Add a pre-reset check to your workflow
sha256sum --check baselines/.checksums || echo "BASELINE TAMPERING DETECTED"
```

**Watchdog process:** For multi-agent systems where agents have filesystem access, a separate watchdog that monitors baseline integrity and auto-restores from a protected backup adds another layer of defense.

## Architecture

```
                  ┌──────────────────┐
                  │  systemd timer   │
                  │  (every 3 hours) │
                  └────────┬─────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │  memory-shepherd.sh    │
              │  reads config, loops   │
              │  over agents           │
              └────────────┬───────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │  Agent A     │ │  Agent B     │ │  Agent C     │
    │  (local)     │ │  (local)     │ │  (remote)    │
    └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
           │                │                │
           ▼                ▼                ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │ 1. Read      │ │ 1. Read      │ │ 1. SCP down  │
    │ 2. Extract   │ │ 2. Extract   │ │ 2. Extract   │
    │    scratch   │ │    scratch   │ │    scratch   │
    │ 3. Archive   │ │ 3. Archive   │ │ 3. Archive   │
    │ 4. Reset     │ │ 4. Reset     │ │ 4. SCP up    │
    └──────────────┘ └──────────────┘ └──────────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │  archives/       │
                  │  ├── agent-a/    │
                  │  │   └── *.md    │
                  │  ├── agent-b/    │
                  │  │   └── *.md    │
                  │  └── agent-c/    │
                  │      └── *.md    │
                  └──────────────────┘
```

## Safety Features

- **Lock file** prevents concurrent resets from overlapping
- **Stale lock detection** auto-removes locks older than 2 minutes
- **Baseline size validation** refuses to reset if the baseline is under 1000 bytes (likely corrupt)
- **Atomic file replacement** uses copy-then-move to prevent partial writes
- **Missing separator handling** backs up the entire memory file before resetting
- **Missing memory file handling** creates from baseline instead of failing
- **Archive retention** automatically cleans up old archives
- **Log rotation** prevents unbounded log growth

## License

Apache 2.0 — see [LICENSE](../LICENSE).
