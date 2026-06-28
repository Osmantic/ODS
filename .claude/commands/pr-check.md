---
description: Run all local CI checks before creating a PR
allowed-tools: AskUserQuestion, Bash, Read, Edit, Write, Glob, Grep
argument-hint: (no arguments)
---

# PR Check Skill

Run all local CI checks before creating a PR. On failure, offer to fix issues interactively.

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at decision points.** CI check failures require judgment about whether to auto-fix, skip, or investigate. The user should approve each fix strategy.

- **When** a check fails — ask how to handle before auto-fixing
- **When** multiple checks fail — let the user prioritize which to fix first
- **After** all checks pass — ask about next steps (PR, review, etc.)

## Pre-flight

Before running checks, verify the Makefile exists:

```bash
ls -la ods/Makefile
```

If the Makefile doesn't exist, inform the user and stop.

## Checks Overview

Display to user before running:

> **Running 8 ODS checks:**
> 1. **Shell lint** - `bash -n` syntax check on all `.sh` files
> 2. **Python compile** - `py_compile` check on dashboard-api modules
> 3. **Tier map + contract tests** - Unit and contract test suite
> 4. **BATS unit tests** - Shell library tests via BATS
> 5. **Smoke tests** - Platform-specific smoke tests
> 6. **Installer simulation** - Installer simulation harness
> 7. **Dashboard build/lint** - ESLint + Vite production build
> 8. **Secret scan** - Pre-commit hooks (gitleaks, private keys)

## Execute Script

Run the full gate (covers checks 1-6):

```bash
cd ods && make gate
```

Then run dashboard checks:

```bash
cd ods/extensions/services/dashboard && npm run lint && npm run build
```

Then run secret scan:

```bash
pre-commit run --all-files
```

**IMPORTANT**: Stream output to user in real-time.

## On Success

If all checks pass:

```
AskUserQuestion:
  question: "All 8 PR checks passed! What would you like to do next?"
  header: "Next"
  multiSelect: false
  options:
    - label: "Create PR"
      description: "Run /pr to create a pull request with these changes"
    - label: "Run code review"
      description: "Run /code-review before creating the PR"
    - label: "Done"
      description: "Checks passed — I'll handle the rest manually"
```

## On Failure - Interactive Fix Loop

When a check fails, you MUST:

### 1. Identify Which Check Failed

Parse the output to determine which step failed:

**From `make gate`:**
- Shell syntax check failed = Check 1
- Python compile check failed = Check 2
- Tier map / contract tests failed = Check 3
- BATS unit tests failed = Check 4
- Smoke tests failed = Check 5
- Installer simulation failed = Check 6

**From dashboard:**
- `npm run lint` failed = Check 7 (lint)
- `npm run build` failed = Check 7 (build)

**From pre-commit:**
- gitleaks / private key detected = Check 8

### 2. Ask User How to Handle Each Failure

Use `AskUserQuestion` for each failed check rather than text-based offers:

```
AskUserQuestion:
  question: "Check <N> (<check name>) failed. How should I handle it?"
  header: "<check>"
  multiSelect: false
  options:
    - label: "Auto-fix (Recommended)"
      description: "<Specific fix strategy for this check type>"
    - label: "Show error details"
      description: "Display the full error output before deciding"
    - label: "Skip this check"
      description: "Move on — I'll handle it manually"
    - label: "Abort"
      description: "Stop running checks — I need to investigate first"
```

**Fix strategies by check type:**

| Check | Auto-Fix Strategy |
|-------|------------------|
| **[1] Shell lint** | Read the failing file, fix syntax error (missing quotes, bad conditionals, etc.) |
| **[2] Python compile** | Read the failing .py file, fix syntax/import error |
| **[3] Tier map / contracts** | Read failing test and source, fix the logic mismatch |
| **[4] BATS tests** | Read failing .bats test and source lib, fix the assertion or implementation |
| **[5] Smoke tests** | Read the smoke test script, fix platform-specific issue |
| **[6] Simulation** | Read simulation harness output, fix installer phase issue |
| **[7] Dashboard lint/build** | Run `npx eslint --fix` in dashboard/, manually fix remaining; fix build errors |
| **[8] Secret scan** | Identify the secret, remove it from code, add to .gitignore if needed |

If multiple checks fail, ask about priority order:

```
AskUserQuestion:
  question: "<N> checks failed: <list>. Which should I fix first?"
  header: "Priority"
  multiSelect: false
  options:
    - label: "Fix in order (Recommended)"
      description: "Address failures in check order: <ordered list>"
    - label: "Lint first"
      description: "Fix lint/syntax issues first — they may resolve other failures"
    - label: "Tests first"
      description: "Fix test failures first — they indicate real bugs"
    - label: "Let me choose"
      description: "I'll tell you which check to fix first"
```

### 3. After Fix - Re-run Checks

After applying any fix:

```bash
cd ods && make gate
```

Continue the fix loop until:
- All checks pass, OR
- User decides to stop

### 4. Track Fixes Applied

Keep a mental note of all fixes applied during the session. On final success, summarize:

> **All PR checks passed!**
>
> **Fixes applied:**
> - Fixed shell syntax error in `installers/lib/detection.sh`
> - Fixed BATS assertion in `tests/bats-tests/tier-map.bats`
> - Removed accidental API key from `config/litellm.yaml`

## Error Recovery

If a check fails in an unexpected way (not a code issue):
- **Missing dependency**: Offer to install it (`npm ci` for dashboard, `pip install pre-commit`)
- **BATS not found**: Check `tests/bats/` submodule: `git submodule update --init`
- **Timeout**: Suggest re-running or checking system resources

## Notes

- `make gate` runs: lint + test + bats + smoke + simulate (checks 1-6 in one command)
- Dashboard checks must be run separately (not in Makefile gate)
- Secret scan requires `pre-commit` to be installed
- The Makefile uses `set -e` semantics so it stops on first failure within each target
- All shell files must pass `bash -n` syntax check
- Python compile check covers `main.py` and `agent_monitor.py` in dashboard-api
