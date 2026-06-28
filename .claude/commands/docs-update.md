---
description: Review and update all ODS documentation based on recent git changes
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Edit, Write
argument-hint: [commits=20] [--dry-run] [--scope=all|readme|claude|extensions|dashboard-api]
---

# ODS Documentation Review and Update

Review all project documentation against recent git changes with full project context. This command understands the installer architecture, extension system, GPU tier mapping, Docker compose layering, dashboard API, and dashboard UI.

## Arguments

- **commits**: Number of recent commits to analyze (default: 20)
- **--dry-run**: Preview changes without applying them
- **--scope**: Limit scope to specific docs (default: all)
  - `all` - All documentation files
  - `readme` - README.md only
  - `claude` - CLAUDE.md only
  - `extensions` - Extension-related docs only
  - `dashboard-api` - Dashboard API README only

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at decision points.** Documentation changes are visible to the whole team and affect onboarding. Confirm scope and validate findings before modifying files.

- **Before** analyzing — confirm which docs and how many commits to review
- **After** generating the staleness report — let the user choose which docs to update
- **Before** applying edits — present proposed changes for approval
- **When** cross-doc inconsistencies are found — ask which doc is the source of truth

## Documentation Files (In-Scope)

| Doc File | Purpose | Scope Key |
|----------|---------|-----------|
| `README.md` | Main project documentation | readme |
| `CLAUDE.md` | Claude Code instructions | claude |
| `ods/README.md` | Core product documentation (if exists) | readme |
| `ods/extensions/services/dashboard-api/README.md` | Dashboard API documentation | dashboard-api |
| `ods/extensions/services/dashboard/README.md` | Dashboard UI documentation (if exists) | readme |

## Excluded Files

- `node_modules/`, `.venv/` - Dependencies
- `data/` - Runtime data
- `ods/token-spy/` - Dev tools
- `.git/` - Git internals

---

## Step 1: Gather Git Context

### 1.1 Changed Files Analysis

Run `git log --oneline -N --name-only --pretty=format: | sort -u | grep -v '^$'` (N = commit count, default 20).

### 1.2 Get Commit Context

Run `git log --oneline -N` for change summary.

### 1.3 Categorize Changes and Map to Documentation

Use this mapping table to identify which docs need updating:

| Code Pattern | Affected Documentation |
|--------------|----------------------|
| `ods/installers/lib/*.sh` | CLAUDE.md (installer architecture, key file paths) |
| `ods/installers/phases/*.sh` | CLAUDE.md (installer architecture) |
| `ods/scripts/*.sh` | CLAUDE.md (build commands, key file paths) |
| `ods/ods-cli` | README.md, CLAUDE.md |
| `ods/extensions/services/*/manifest.yaml` | CLAUDE.md (extension system) |
| `ods/docker-compose*.yml` | README.md, CLAUDE.md (Docker compose layering) |
| `ods/config/backends/*.json` | CLAUDE.md (GPU backend / tier system) |
| `ods/extensions/services/dashboard-api/**` | CLAUDE.md, dashboard-api/README.md |
| `ods/extensions/services/dashboard/**` | CLAUDE.md, README.md |
| `ods/tests/**` | CLAUDE.md (build commands) |
| `ods/Makefile` | CLAUDE.md (build commands) |
| `.github/workflows/*` | CLAUDE.md (CI workflows) |
| `ods/.env.example` | CLAUDE.md, README.md |
| `ods/.env.schema.json` | CLAUDE.md |
| `install.sh`, `install.ps1` | README.md |

---

## Step 2: Filter by Scope

If `--scope` argument provided, filter affected docs:

| Scope Arg | Files to Process |
|-----------|-----------------|
| `all` | All documentation files |
| `readme` | README.md, ods/README.md |
| `claude` | CLAUDE.md only |
| `extensions` | Extension manifests, CLAUDE.md (extension system section) |
| `dashboard-api` | dashboard-api/README.md only |

---

## Step 3: Per-Document Validation

For each affected document, validate against its source-of-truth files.

### 3.1 README.md Validation

**Source of truth files:**
- `install.sh`, `install.ps1` - Install commands
- `ods/ods-cli` - CLI commands and usage
- `ods/docker-compose.base.yml` - Core service definitions
- `ods/docker-compose.{amd,nvidia,apple}.yml` - GPU overlays
- `ods/.env.example` - Environment variables

**Sections to validate:**
1. **Installation commands** - Match actual install script paths and flags
2. **System requirements** - OS support, GPU requirements
3. **Service list** - All services deployed by the stack
4. **CLI usage** - ods-cli commands
5. **Environment variables** - Match `.env.example`
6. **Docker Compose commands** - Correct file references

### 3.2 CLAUDE.md Validation

**Source of truth files:**
- `ods/install-core.sh` - Installer orchestrator
- `ods/installers/lib/*.sh` - Installer libraries
- `ods/installers/phases/*.sh` - Installer phases
- `ods/scripts/*.sh` - Operational scripts
- `ods/Makefile` - Build targets
- `ods/extensions/services/*/manifest.yaml` - Extension manifests
- `ods/docker-compose*.yml` - Docker compose files
- `ods/config/backends/*.json` - Backend configs
- `.github/workflows/*.yml` - CI workflow files
- `ods/.env.example`, `ods/.env.schema.json` - Env schema

**Sections to validate:**
1. **Repository Structure** - File paths and descriptions match reality
2. **Build & Development Commands** - make targets match Makefile
3. **CI Workflows** - Workflow descriptions match actual .yml files
4. **Installer Architecture** - Phase count, lib descriptions
5. **Extension System** - Manifest schema, extension list
6. **GPU Backend / Tier System** - Tier mapping, backend configs
7. **Docker Compose Layering** - Compose file names and merge strategy
8. **Dashboard API** - Router list, auth mechanism
9. **Key File Paths** - All referenced paths exist

### 3.3 dashboard-api/README.md Validation

**Source of truth files:**
- `ods/extensions/services/dashboard-api/main.py` - FastAPI app
- `ods/extensions/services/dashboard-api/routers/*.py` - API routers
- `ods/extensions/services/dashboard-api/security.py` - Auth
- `ods/extensions/services/dashboard-api/helpers.py` - Utilities
- `ods/extensions/services/dashboard-api/tests/` - Test structure

**Sections to validate:**
1. **Router list** - All routers documented
2. **API endpoints** - Endpoint descriptions match code
3. **Authentication** - Auth mechanism described correctly
4. **Test instructions** - pytest commands work

---

## Step 4: Cross-Reference Validation

### 4.1 Consistency Checks

Ensure information is consistent across docs:
- Build commands in CLAUDE.md match Makefile targets
- Extension list matches actual `extensions/services/*/` directories
- Docker compose file names consistent everywhere
- Environment variables match across all docs
- File paths exist and are correct

### 4.2 File Path Validation

For each doc, extract all referenced file paths and verify they exist using Glob.

### 4.3 Command Validation

Verify documented commands are accurate:
- `make lint`, `make test`, `make bats`, `make gate`, `make doctor`
- `bash ods-cli <subcommand>`
- `docker compose -f docker-compose.base.yml ...`
- `cd extensions/services/dashboard && npm run dev`
- `cd extensions/services/dashboard && npm run build`
- `cd extensions/services/dashboard && npm run lint`
- `cd extensions/services/dashboard-api && pytest tests/`

---

### 4.4 Ask About Cross-Doc Inconsistencies

If information conflicts between documents (e.g., different build commands in README.md vs CLAUDE.md):

```
AskUserQuestion:
  question: "Found inconsistency between <Doc A> and <Doc B>: <description>. Which is correct?"
  header: "Conflict"
  multiSelect: false
  options:
    - label: "<Doc A> is correct"
      description: "Update <Doc B> to match <Doc A>"
    - label: "<Doc B> is correct"
      description: "Update <Doc A> to match <Doc B>"
    - label: "Neither — check the code"
      description: "Both docs are wrong — derive the correct info from source code"
    - label: "Skip"
      description: "Leave the inconsistency for now"
```

## Step 5: Generate Multi-Doc Staleness Report

```markdown
## Documentation Staleness Report

### Summary
- **Commits Analyzed**: N
- **Docs Analyzed**: X
- **Docs with Issues**: Y
- **Scope**: [scope argument or "all"]

---

### README.md
**Status**: Stale / Up-to-date
**Affected by**: [list of changed files that impact this doc]

#### Critical Issues
- [ ] [Issue description]

#### Missing Documentation
- [ ] [What's missing]

#### Outdated Information
- [ ] [What changed]

---

### CLAUDE.md
**Status**: Stale / Up-to-date
**Affected by**: [list of changed files]

[...same format...]

---

### dashboard-api/README.md
[...same format...]

```

---

## Step 6: Apply Updates

### If --dry-run:

1. Display full multi-doc staleness report
2. Show proposed edits for each doc (before/after snippets)
3. Exit without changes

### If applying (default):

#### 6a. Confirm Which Docs to Update

```
AskUserQuestion:
  question: "Staleness report ready. Which docs should I update?"
  header: "Update"
  multiSelect: true
  options:
    - label: "README.md"
      description: "<N issues found — <brief summary>>"
    - label: "CLAUDE.md"
      description: "<N issues found — <brief summary>>"
    - label: "dashboard-api/README.md"
      description: "<N issues found — <brief summary>>"
    - label: "All stale docs"
      description: "Update every doc that has issues"
```

1. Process each user-selected doc in order:
   - CLAUDE.md first (primary developer reference)
   - README.md second (must stay consistent with CLAUDE.md)
   - Other docs after

2. For each doc:
   - Use Edit tool for required changes
   - Preserve existing formatting and style
   - Update tables in-place
   - Add new sections only if major features missing

3. After all edits:
   - Run `git diff` to show all documentation changes
   - Present summary for review

---

## Step 7: Output Summary

```markdown
## Documentation Update Summary

**Commits Analyzed**: N
**Scope**: [scope or "all"]
**Docs Updated**: X of Y

### Changes by Document

| Document | Status | Sections Updated | Changes |
|----------|--------|------------------|---------|
| README.md | Updated/Skipped | X | [brief] |
| CLAUDE.md | Updated/Skipped | X | [brief] |
| dashboard-api/README.md | Updated/Skipped | X | [brief] |

### Verification Commands

# Review all documentation changes
git diff README.md CLAUDE.md ods/extensions/services/dashboard-api/README.md

# Commit all doc updates
git add README.md CLAUDE.md ods/extensions/services/dashboard-api/README.md
git commit -m "docs: update documentation for recent changes"

# Revert all doc changes if needed
git checkout README.md CLAUDE.md ods/extensions/services/dashboard-api/README.md
```

---

## Safety

- Only modifies `.md` files in the documented scope
- Never touches `node_modules/`, `.venv/`, `data/`, generated outputs
- Git-based rollback always available
- Human review via `git diff`
- Dry-run mode for preview
- Scope filtering to limit blast radius
