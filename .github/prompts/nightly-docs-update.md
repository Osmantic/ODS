# Nightly Documentation Update (Autonomous CI Mode)

You are running in a CI pipeline. You MUST operate fully autonomously.
Do NOT use AskUserQuestion. Do NOT pause for user input. Do NOT use Write to create new files.

## Rules

1. ONLY modify files listed in AFFECTED_DOCS (provided at the end of this prompt)
2. NEVER modify: `.github/`, `.env*`, `*.py`, `*.sh`, `*.ts`, `*.tsx`, `*.yml`, `*.yaml`
3. Be CONSERVATIVE — only update sections where code has demonstrably changed
4. Do NOT add new sections, restructure documents, or change formatting/style
5. Do NOT modify design principles, philosophy, or instructional content in CLAUDE.md
6. Focus on: factual data (tables, file paths, model names, env vars, API routes, command examples)
7. When cross-doc inconsistency found: code is the source of truth — update the doc to match code
8. When ambiguous: SKIP the change entirely (do not guess)
9. Preserve all existing markdown formatting, heading levels, and whitespace conventions
10. Do NOT remove content unless it references files/features that no longer exist in the codebase

## Steps

### Step 1: Gather Context

Run `git log --oneline -N` (N = COMMITS_TO_ANALYZE) to see recent changes and understand what was modified.

Run `git log --oneline -N --name-only --pretty=format: | sort -u | grep -v '^$'` to get the full list of changed files.

### Step 2: For Each File in AFFECTED_DOCS

For each documentation file listed in AFFECTED_DOCS:

1. **Read the documentation file** using the Read tool
2. **Read the relevant source-of-truth code files** (see mapping below)
3. **Compare**: identify sections that are factually outdated (wrong file paths, missing routes, incorrect model names, outdated env vars, stale tables)
4. **Use Edit tool** to update ONLY the outdated sections — make minimal, targeted edits
5. Move on to the next file

### Step 3: Verify Changes

After all updates, run `git diff` to verify:
- Changes are minimal and correct
- No unintended modifications
- Formatting is preserved

## Source-of-Truth Mapping

Use this mapping to determine which code files to read when validating each documentation file.

### README.md (ods/README.md)

| Doc Section | Source of Truth |
|-------------|----------------|
| Service manifests table | `ods/extensions/services/*/manifest.yaml` |
| CLI commands | `ods/ods-cli` |
| Environment variables | `ods/.env.example`, `ods/.env.schema.json` |
| Docker Compose services | `ods/docker-compose.base.yml`, GPU overlays |
| Test commands | `ods/Makefile`, `ods/tests/` directory layout |
| Install instructions | `ods/install-core.sh`, `ods/installers/phases/` |

### CLAUDE.md

| Doc Section | Source of Truth |
|-------------|----------------|
| Repository Structure | Actual directory layout |
| Build & Development Commands | `ods/Makefile` targets |
| Extension System | `ods/extensions/services/*/manifest.yaml` |
| GPU Backend / Tier System | `ods/config/backends/*.json`, `ods/installers/lib/tier-map.sh` |
| Dashboard API | `ods/extensions/services/dashboard-api/routers/*.py` |
| Key File Paths | Actual file existence verification |
| CI Workflows | `.github/workflows/*.yml` |

## Per-Document Validation Rules

### README.md
- Service list must match existing extension manifests
- CLI examples must match actual ods-cli commands
- Environment variables must match `.env.example`
- Do NOT modify the project description, badges, or contribution guidelines

### CLAUDE.md
- Repository Structure section paths must point to files that exist
- Build commands must match Makefile targets
- Extension list must match `extensions/services/` directory
- Do NOT modify Design Philosophy, Let It Crash, KISS, Pure Functions, or SOLID sections

## Important Reminders

- You are in CI — there is NO human to ask questions to
- If a documentation file in AFFECTED_DOCS does not exist, skip it silently
- Every edit must be verifiable against actual source code — do not infer or hallucinate
- Prefer no change over a wrong change
