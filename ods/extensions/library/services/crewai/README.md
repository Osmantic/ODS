# CrewAI

Multi-agent framework for AI workflows. Build teams of autonomous AI agents that collaborate, delegate tasks, and work together to solve complex problems through a no-code visual builder.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable crewai
ods disable crewai
```

Your data is preserved when disabling. To re-enable later: `ods enable crewai`

## Access

- **URL:** `http://localhost:8501`

## First-Time Setup

1. Enable the service: `ods enable crewai`
2. Open `http://localhost:8501`
3. Create agents, define tasks, and run crews through the visual builder

## Data

Agents, tasks, crews and tools are stored in `data/crewai/crewai.db`.
Installs made before this change kept that database inside the container, so
anything created there is not carried over when the container is recreated.
