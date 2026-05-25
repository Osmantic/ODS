# Dream CLI Decomposition Plan

`dream-cli` is the operator control surface for status, lifecycle, config,
model, extension, backup, and diagnostic commands. It is intentionally shell
native because it lives beside a shell-first installer, but the file is large
enough that future changes need a staged decomposition plan.

This is a behavior-preserving roadmap. It is not a rewrite proposal.

## Goals

- Keep the installed `dream` command stable.
- Split low-risk helpers before lifecycle or host-mutating commands.
- Preserve Bash portability for installed hosts.
- Add or keep contract tests before moving command groups.
- Make future forks able to modify one command family without understanding the
  whole CLI at once.

## Non-Goals

- No big-bang Rust, Go, Python, or Node rewrite.
- No command syntax changes as part of extraction.
- No change to install location, symlink behavior, or support-bundle output.
- No hidden dependency on developer-only tooling.

## Current Command Families

| Family | Examples | Risk |
|--------|----------|------|
| Read-only status | `status`, `list`, `logs`, `config show` | Medium |
| Diagnostics | `doctor`, support-bundle helpers | High |
| Lifecycle | `start`, `stop`, `restart`, service restart aliases | High |
| Model management | `model current`, `model list`, `model swap` | High |
| Mode/config | `mode local`, `mode cloud`, presets, env mutation | High |
| Extensions | `enable`, `disable`, audit/list commands | High |
| Backup/restore | backup, restore, preset import/export | High |

## Extraction Order

1. **Read-only formatting helpers**
   - Move table rendering, service alias lookup, and masked config formatting
     into small sourced modules.
   - Validation: CLI smoke and snapshot-style output checks.

2. **Read-only status and logs**
   - Extract commands that inspect Docker/service state without mutating host
     files.
   - Validation: `dream status`, `dream list`, and log alias tests.

3. **Diagnostics**
   - Extract `dream doctor` orchestration while keeping individual diagnostic
     scripts as the source of truth.
   - Validation: doctor smoke, support-bundle redaction tests.

4. **Model and mode commands**
   - Extract model catalog, swap, and mode helpers only after current contract
     tests cover rollback, model identity, and generated config writers.
   - Validation: model selector tests, generated config tests, model swap smoke.

5. **Extensions**
   - Extract extension enable/disable/list behavior after manifest and compose
     resolver contracts cover the changed paths.
   - Validation: extension audit, compose resolver checks, dashboard extension
     flow when behavior changes.

6. **Lifecycle and backup/restore last**
   - Keep start/stop/restart and restore flows in the main file until lower-risk
     extractions are proven.
   - Validation: lifecycle lane, `dream restart`, `dream doctor`, backup/restore
     round trip where applicable.

## Guardrails For Each PR

Each decomposition PR should:

- move one command family or helper family only;
- keep the public command syntax unchanged;
- include a before/after validation note;
- avoid changing compose flags, env parsing, or service aliases unless that is
  the explicit purpose of the PR;
- preserve shellcheck/lint behavior where available;
- explain whether release-grade fleet validation is required before release.

## Success Criteria

The CLI is healthier when:

- `dream-cli` becomes a dispatcher plus shared compatibility glue;
- command families live in files with narrow ownership;
- tests prove behavior before and after each move;
- forks can safely override or patch a command family without modifying the
  entire operator CLI.
