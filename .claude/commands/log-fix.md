---
description: Read Docker/installer logs and iteratively fix bugs based on log analysis
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Edit, Write, Task
argument-hint: [--time <1h|30m|1d>] [--source <docker|installer|api|all>] [--level <ERROR|WARNING>] [--service <name>]
---

# Log-Fix: Iterative Bug Fixing from Log Analysis

Analyze logs from Docker containers, installer runs, and service health checks. Identify errors, perform root cause analysis, and interactively fix issues with user approval.

> "Logs tell stories. This skill reads them and writes the fixes."

## Arguments

- `$ARGUMENTS` - Optional filters and configuration:
  - `--time <duration>` - Time range: `1h`, `30m`, `1d`, `7d` (default: `1h`)
  - `--source <source>` - Log source: `docker`, `installer`, `api`, `all` (default: `all`)
  - `--level <level>` - Minimum severity: `ERROR`, `WARNING`, `all` (default: `ERROR`)
  - `--service <name>` - Filter by service name (e.g., `ods-llama-server`, `ods-open-webui`, `ods-dashboard`)

---

## Phase 0: Log Source Discovery

Detect all available log sources before analysis.

### 0.1 Check Docker Compose Logs

```bash
# Find the resolved compose stack
COMPOSE_FILES=""
if [ -f "ods/docker-compose.base.yml" ]; then
  COMPOSE_FILES="-f ods/docker-compose.base.yml"
  for overlay in ods/docker-compose.{amd,nvidia,apple}.yml; do
    [ -f "$overlay" ] && COMPOSE_FILES="$COMPOSE_FILES -f $overlay"
  done
fi

# Check running containers
if [ -n "$COMPOSE_FILES" ]; then
  docker compose $COMPOSE_FILES ps --format "table {{.Name}}\t{{.Status}}" || echo "Docker not running"
else
  echo "No compose files found"
fi
```

### 0.2 Check Installer Logs

```bash
INSTALL_LOG="$HOME/.ods/install.log"
if [ -f "$INSTALL_LOG" ]; then
  INSTALL_SIZE=$(du -h "$INSTALL_LOG" | cut -f1)
  INSTALL_LINES=$(wc -l < "$INSTALL_LOG")
  echo "Installer log: $INSTALL_LOG ($INSTALL_SIZE, $INSTALL_LINES lines)"
else
  echo "Installer log: NOT FOUND at $INSTALL_LOG"
fi
```

### 0.3 Check Service Health Endpoints

```bash
# LLM server health
curl -sf http://127.0.0.1:8080/health && echo "LLM server: HEALTHY" || echo "LLM server: UNREACHABLE"

# Dashboard API health
curl -sf http://127.0.0.1:3001/health && echo "Dashboard API: HEALTHY" || echo "Dashboard API: UNREACHABLE"

# Open WebUI
curl -sf http://127.0.0.1:3000 && echo "Open WebUI: HEALTHY" || echo "Open WebUI: UNREACHABLE"
```

### 0.4 Display Discovery Summary

```
## Log Sources Detected

| Source | Status | Location/Details |
|--------|--------|------------------|
| Docker | [RUNNING/STOPPED/NOT FOUND] | N services via compose stack |
| Installer | [FOUND/NOT FOUND] | ~/.ods/install.log (N lines) |
| LLM Server | [HEALTHY/UNREACHABLE] | 127.0.0.1:8080/health |
| Dashboard API | [HEALTHY/UNREACHABLE] | 127.0.0.1:3001/health |
| Open WebUI | [HEALTHY/UNREACHABLE] | 127.0.0.1:3000 |

Proceeding with: [sources based on --source flag]
```

---

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at decision points.** Bug fixing involves judgment calls about severity, root causes, and fix strategies. The user should direct the investigation, not passively watch.

- **Before** analyzing — confirm which sources and time range to investigate
- **After** triage — let the user choose which issues to dig into
- **Before** each fix — always present the proposed change for approval
- **When** root cause is uncertain — present multiple hypotheses
- **After** fixes — ask about next steps

## Phase 1: Log Collection and Parsing

Collect and normalize logs from detected sources.

### 1.1 Docker Container Logs

```bash
# All services, filtered by time
docker compose $COMPOSE_FILES logs --since "${TIME_ARG:-1h}" --no-color 2>&1 | grep -iE "(error|exception|failed|fatal|panic)"

# Per-service logs (if --service specified)
docker compose $COMPOSE_FILES logs --since "${TIME_ARG:-1h}" --no-color "${SERVICE_NAME}" 2>&1
```

### 1.2 Installer Log Parsing

```bash
# Recent errors from installer log
grep -iE "(error|fail|fatal|abort)" "$HOME/.ods/install.log" | tail -50
```

### 1.3 Dashboard API Logs

```bash
# API container logs
docker compose $COMPOSE_FILES logs --since "${TIME_ARG:-1h}" --no-color ods-dashboard-api 2>&1
```

---

## Phase 2: Triage and Clustering

Group related errors and rank by severity.

### 2.1 Incident Clustering

Group by:
1. Same error type (identical/similar messages)
2. Same service/container
3. Same time window (errors within 5s)

### 2.2 Severity Ranking

| Priority | Criteria |
|----------|----------|
| P0 | Service crash, container restart loop, database corruption |
| P1 | ERROR level, health check failure, model load failure |
| P2 | WARNING level, deprecation, slow response |
| P3 | Informational, cleanup suggestions |

### 2.3 Known Error Patterns

| Error Pattern | Likely Cause | Location |
|--------------|--------------|----------|
| `model not found` | Wrong model name in config, model not downloaded | `config/backends/*.json`, LLM server |
| `port already in use` | Another process on same port | Docker compose port mapping |
| `connection refused` on health | Service not started yet, crashed | Container logs |
| `CUDA out of memory` | Model too large for GPU VRAM | `config/backends/nvidia.json`, tier mapping |
| `no matching manifest` | Docker image not available for platform | `docker-compose*.yml` |
| `permission denied` | File ownership, Docker socket access | Installer phases, Docker daemon |
| `DNS resolution failed` | Network config, Docker DNS | Docker network settings |
| `GGUF file not found` | Model file missing or wrong path | `config/backends/*.json` |
| `INSTALL_PHASE` error | Installer phase crash | `installers/phases/*.sh` |

### 2.4 Display Triage Summary

```
## Error Triage Summary

**Time Range**: [start] to [end]
**Total Errors**: N
**Unique Types**: M

### Top Issues by Frequency

| # | Count | Service/Component | Error Pattern | Priority |
|---|-------|-------------------|---------------|----------|
| 1 | 15 | ods-llama-server | CUDA out of memory | P0 |
| 2 | 8 | ods-dashboard-api | Connection refused to LLM | P1 |
```

#### 2.5 Ask User Which Issues to Investigate

```
AskUserQuestion:
  question: "Found <N> unique issues. Which should I investigate?"
  header: "Investigate"
  multiSelect: true
  options:
    - label: "#1: <top issue> (<count>x, P<severity>)"
      description: "<Service> — <brief error>"
    - label: "#2: <second issue> (<count>x, P<severity>)"
      description: "<Service> — <brief error>"
    - label: "#3: <third issue> (<count>x, P<severity>)"
      description: "<Service> — <brief error>"
    - label: "All issues"
      description: "Investigate every issue found in the triage"
```

---

## Phase 3: Root Cause Analysis

For each incident, perform deep analysis.

### 3.1 Component Mapping

Map error sources to ODS code:

| Error Source | Code Location |
|-------------|---------------|
| Installer phase crash | `installers/phases/<NN>-<phase>.sh` |
| Installer lib error | `installers/lib/<module>.sh` |
| Script failure | `scripts/<script>.sh` |
| CLI error | `ods-cli` |
| Dashboard API error | `extensions/services/dashboard-api/routers/*.py` |
| Dashboard API helper | `extensions/services/dashboard-api/helpers.py` |
| Container config | `docker-compose.base.yml`, GPU overlays |
| Model/tier config | `config/backends/<backend>.json` |
| Extension manifest | `extensions/services/<name>/manifest.yaml` |
| Health check | `scripts/health-check.sh` or API endpoints |

### 3.2 Log Correlation

For errors with context:
```bash
# Get surrounding log context for an error
docker compose $COMPOSE_FILES logs --since "${TIME_ARG:-1h}" --no-color "${SERVICE}" 2>&1 | grep -B5 -A5 "ERROR_PATTERN"
```

### 3.3 Present Root Cause and Get Direction

When root cause analysis yields multiple hypotheses, ask the user:

```
AskUserQuestion:
  question: "Root cause analysis for '<error type>'. Which hypothesis seems most likely?"
  header: "Root cause"
  multiSelect: false
  options:
    - label: "<Hypothesis A>"
      description: "<Evidence supporting this> — fix would be: <brief fix description>"
    - label: "<Hypothesis B>"
      description: "<Evidence supporting this> — fix would be: <brief fix description>"
    - label: "Investigate more"
      description: "I need more context before deciding — show me related logs"
    - label: "Skip this issue"
      description: "Move on to the next error"
```

Skip this question when root cause is unambiguous (single clear hypothesis with strong evidence).

---

## Phase 4: Interactive Fix Loop

Present each fix, get approval, apply, validate.

### 4.1 Fix Workflow

1. **Present** - Show error, root cause, affected files
2. **Show diff** - Display exact code changes
3. **Ask permission** - Use AskUserQuestion
4. **Apply if approved** - Use Edit tool
5. **Validate** - Run relevant tests or health checks
6. **Report** - Success/failure

### 4.2 User Options

```
AskUserQuestion:
  question: "Proposed fix for '<error>': <brief description>. How should I proceed?"
  header: "Fix"
  multiSelect: false
  options:
    - label: "Apply this fix"
      description: "<Files to modify, lines changed>"
    - label: "Write a test first (/tdd)"
      description: "Use TDD — write a failing test that reproduces the bug, then fix"
    - label: "Show more details"
      description: "Display full diff, related logs, and affected code before deciding"
    - label: "Skip this issue"
      description: "Move on to the next error without fixing"
```

### 4.3 Validation After Fix

| Fix Type | Validation Command |
|----------|-------------------|
| Installer lib (`.sh`) | `bash -n <file> && cd ods && make lint` |
| Installer phase (`.sh`) | `bash -n <file> && cd ods && make lint` |
| Script (`.sh`) | `bash -n <file> && cd ods && make lint` |
| BATS test fix | `cd ods && bats tests/bats-tests/<module>.bats` |
| Dashboard API (`.py`) | `cd ods/extensions/services/dashboard-api && pytest tests/ -v` |
| Docker compose | `docker compose $COMPOSE_FILES config` |
| Config JSON | `python3 -c "import json; json.load(open('<file>'))"` |
| Extension manifest | `python3 -c "import yaml; yaml.safe_load(open('<file>'))"` |

### 4.4 Health Check After Fix

After applying fixes that affect running services:

```bash
# Restart affected service
docker compose $COMPOSE_FILES restart "${SERVICE_NAME}"

# Wait for health
sleep 5

# Check health endpoints
curl -sf http://127.0.0.1:8080/health && echo "LLM: OK" || echo "LLM: FAIL"
curl -sf http://127.0.0.1:3001/health && echo "API: OK" || echo "API: FAIL"
curl -sf http://127.0.0.1:3000 && echo "WebUI: OK" || echo "WebUI: FAIL"
```

---

## Phase 5: Docker Control

Full docker compose control for container-level fixes.

### 5.1 Container Commands

```bash
# Restart specific service
docker compose $COMPOSE_FILES restart <service-name>

# Rebuild and restart
docker compose $COMPOSE_FILES up --build -d <service-name>

# View health
docker compose $COMPOSE_FILES ps

# Tail logs
docker compose $COMPOSE_FILES logs -f --tail=100 <service-name>

# Full restart
docker compose $COMPOSE_FILES down && docker compose $COMPOSE_FILES up -d
```

---

## Phase 6: TDD Integration

Suggest `/tdd` when appropriate.

### 6.1 When to Suggest TDD

| Condition | Action |
|-----------|--------|
| Bug has no test | Suggest `/tdd` first |
| Bug is in installer lib | Suggest BATS test via `/tdd --shell` |
| Bug is in dashboard-api | Suggest pytest via `/tdd --python` |
| Fix is complex | Suggest `/tdd` to validate |

---

## Phase 7: Summary Report

After all fixes, display summary.

```
## Log-Fix Session Summary

**Logs Analyzed**: [sources]
**Time Range**: [range]
**Errors Found**: N
**Unique Issues**: M

### Issues Addressed

| # | Service/Component | Issue | Status | Action |
|---|-------------------|-------|--------|--------|
| 1 | ods-llama-server | CUDA OOM | FIXED | Reduced model tier |
| 2 | ods-dashboard-api | Connection refused | SKIPPED | User skipped |
| 3 | installer phase 05 | Permission denied | FIXED | Fixed file perms |

### Files Modified
- config/backends/nvidia.json
- installers/phases/05-docker.sh

### Validation Results
| File | Test | Result |
|------|------|--------|
| nvidia.json | JSON parse | PASSED |
| 05-docker.sh | bash -n + make lint | PASSED |

### Health Check Results
| Service | Status |
|---------|--------|
| LLM Server (8080) | HEALTHY |
| Dashboard API (3001) | HEALTHY |
| Open WebUI (3000) | HEALTHY |

### Remaining Issues
| Priority | Issue | Recommendation |
|----------|-------|----------------|
| P2 | Slow model load | Consider smaller model for tier |

---
Next Steps:
- Run `/pr-check` to validate all changes
- Consider `/tdd` for issues without test coverage
- Run `make gate` for full validation
```

---

## Component to Test Mapping

| Component | Test Location |
|-----------|---------------|
| `installers/lib/*.sh` | `tests/bats-tests/<module>.bats` |
| `installers/phases/*.sh` | `tests/contracts/test-installer-contracts.sh` |
| `scripts/*.sh` | `tests/test-<script>.sh` |
| `extensions/services/dashboard-api/**` | `extensions/services/dashboard-api/tests/test_*.py` |
| `docker-compose*.yml` | `docker compose config` (validation) |

---

## Example Sessions

### Quick Docker Error Fix

```
/log-fix --time 30m --source docker --level ERROR

Found 5 errors. Top: "CUDA out of memory" (3x) in ods-llama-server

[Shows analysis — model too large for GPU tier]

> Apply fix: reduce model in config/backends/nvidia.json

Applied fix. Restarting service... HEALTHY
Continue? [Y/n]
```

### Installer Log Investigation

```
/log-fix --source installer

Installer log: ~/.ods/install.log (450 lines)

INSTALL_PHASE=05-docker failed: "permission denied: /var/run/docker.sock"

Root cause: User not in docker group.
[Shows fix for phase script to check group membership]
```

### Service Health Check

```
/log-fix --service ods-dashboard-api

Dashboard API: UNREACHABLE at 127.0.0.1:3001

Container status: Restarting (exit code 1)
Logs: "ModuleNotFoundError: No module named 'routers'"

Root cause: Missing volume mount or build issue.
[Shows rebuild command]
```

---

## Error Recovery

| Scenario | Recovery |
|----------|----------|
| Docker not installed | Inform user, suggest install |
| No compose files | Check if ods/ exists |
| Container not running | Offer `docker compose up -d` |
| Installer log empty | Check if installer has been run |
| Health endpoint unreachable | Check if service is supposed to be running |
| Tests fail after fix | Offer to revert via `git checkout <file>` |

---

## Notes

- Use Read/Grep tools for log parsing (not cat)
- Docker logs require compose files to be present
- Health check URLs: LLM=8080, API=3001, WebUI=3000
- Never auto-commit — always ask permission
- Suggest `/tdd` for bugs without test coverage
- Suggest `/pr-check` before committing fixes
- ODS binds to 127.0.0.1 by default for security
