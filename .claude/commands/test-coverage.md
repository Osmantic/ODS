---
description: Identify and fill test gaps across Shell (BATS) and Python (pytest) test suites
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Edit, Write, Task
argument-hint: [--type=shell|python|dashboard|all] [--target=80] [--module=<name>] [--dry-run]
---

# Test Coverage Updater

Analyze current test coverage, identify gaps, and write missing tests to improve coverage across both Shell (BATS) and Python (pytest) test suites.

## Arguments

- `$ARGUMENTS` - Options to control scope and behavior:
  - `--type=shell|python|dashboard|all` - Test type to focus on (default: `all`)
  - `--target=N` - Coverage percentage target for Python (default: `80`); shell uses structural coverage
  - `--module=<name>` - Restrict to a specific module (e.g., `tier-map`, `helpers`, `routers.setup`)
  - `--dry-run` - Analyze gaps and report plan without writing tests
  - `--fix` - Fix failing existing tests before writing new ones

## Test Type Definitions

| Type | Location Pattern | What It Tests | Run Command |
|------|-----------------|---------------|-------------|
| **Shell unit (BATS)** | `tests/bats-tests/<lib>.bats` | Installer lib functions in isolation | `bats tests/bats-tests/<lib>.bats` |
| **Shell integration** | `tests/test-<feature>.sh` | Multi-module shell interactions | `bash tests/test-<feature>.sh` |
| **Shell contract** | `tests/contracts/test-*.sh` | Installer contract assertions | `bash tests/contracts/test-*.sh` |
| **Shell smoke** | `tests/smoke/<platform>.sh` | Platform-specific end-to-end | `bash tests/smoke/<platform>.sh` |
| **Python unit** | `dashboard-api/tests/test_<module>.py` | FastAPI routers, helpers, security | `pytest tests/test_<module>.py` |
| **Dashboard UI** | `dashboard/src/**/*.test.*` | React component tests (if present) | `npm test` (in dashboard/) |

---

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at decision points.** Test coverage involves tradeoffs between speed and thoroughness. Validate priorities rather than guessing what the user wants covered.

- **Before** writing tests — confirm which modules to prioritize
- **When** gaps are large — ask where to focus effort first
- **After** coverage run — ask about iteration vs stopping
- **When** source code looks buggy — ask before fixing vs just testing

## Workflow

### Phase 1: Coverage Baseline

#### 1.1 Shell Structural Coverage

Shell doesn't have line-level coverage tools. Instead, perform structural analysis — which modules have tests vs. not:

```bash
# List all installer lib modules
ls ods/installers/lib/*.sh

# List all BATS test files
ls ods/tests/bats-tests/*.bats

# Cross-reference: which libs lack BATS tests?
for lib in ods/installers/lib/*.sh; do
  name=$(basename "$lib" .sh)
  if [ ! -f "ods/tests/bats-tests/${name}.bats" ]; then
    echo "MISSING: $name"
  fi
done
```

Also check scripts and phases:
```bash
# Scripts with tests vs without
ls ods/scripts/*.sh
ls ods/tests/test-*.sh
```

#### 1.2 Python Coverage Report

```bash
cd ods/extensions/services/dashboard-api && pytest --cov=. --cov-report=term-missing -q --tb=no 2>&1 | tail -40
```

#### 1.3 If `--module` Provided

Filter to the specified module only.

#### 1.4 Build Gap Report

Create a ranked list of modules by coverage gap:

**Shell (structural):**

```
| Module | Has BATS Test? | Has Integration Test? | Functions Tested | Priority |
|--------|---------------|----------------------|------------------|----------|
| tier-map.sh | Yes (tier-map.bats) | Yes (test-tier-map.sh) | 2/2 | OK |
| detection.sh | Yes (detection.bats) | No | 3/5 | MEDIUM |
| constants.sh | No | No | 0/4 | CRITICAL |
| logging.sh | No | No | 0/6 | HIGH |
```

**Python:**

```
| File | Coverage | Missing Lines | Priority |
|------|----------|---------------|----------|
| helpers.py | 45% | 30-45, 60-80 | HIGH |
| gpu.py | 0% | 1-50 | CRITICAL |
| security.py | 80% | 25-30 | LOW |
```

**Priority rules:**
- CRITICAL: No tests at all (0% or no BATS file)
- HIGH: < 30% coverage or missing > 50% of functions
- MEDIUM: 30-60% coverage or missing key functions
- LOW: > 60% but below target

#### 1.5 Confirm Coverage Priorities with User

After building the gap report, ask the user what to prioritize:

```
AskUserQuestion:
  question: "Found <N> modules below target. Which should I tackle first?"
  header: "Priority"
  multiSelect: false
  options:
    - label: "Highest-impact first (Recommended)"
      description: "<N CRITICAL + M HIGH priority modules — start with untested modules>"
    - label: "Quick wins first"
      description: "Start with modules that need only 1-2 tests to reach target"
    - label: "Specific module"
      description: "I want to focus on a specific area of the codebase"
    - label: "Dry run only"
      description: "Just show me the gap report — don't write any tests yet"
```

**If "Specific module"**: Ask which module to focus on.

---

### Phase 2: Analyze Existing Test Patterns

Before writing ANY tests, study the project's conventions.

#### 2.1 Read Test Infrastructure

**For Shell:**
- Read existing BATS tests in `tests/bats-tests/` for patterns
- Check `tests/bats/` for bats-support and bats-assert availability

**For Python:**
- Read `extensions/services/dashboard-api/tests/conftest.py` for fixtures
- Read existing test files for patterns

#### 2.2 Module-to-Test Mapping Reference

**Shell modules:**

| Source Module | Test Location | Fixtures Needed |
|---------------|---------------|-----------------|
| `installers/lib/tier-map.sh` | `tests/bats-tests/tier-map.bats` | Stub `error`, `log`, `export -f` |
| `installers/lib/detection.sh` | `tests/bats-tests/detection.bats` | Stub `error`, `log`, `export -f` |
| `installers/lib/compose-select.sh` | `tests/bats-tests/compose-select.bats` | Stub `error`, `log`, `export -f` |
| `installers/lib/packaging.sh` | `tests/bats-tests/packaging.bats` | Stub `error`, `log`, `export -f` |
| `installers/lib/progress.sh` | `tests/bats-tests/progress.bats` | Stub `error`, `log`, `export -f` |
| `installers/lib/constants.sh` | `tests/bats-tests/constants.bats` | None (pure constants) |
| `installers/lib/logging.sh` | `tests/bats-tests/logging.bats` | None (defines `log`, `error`) |
| `installers/lib/ui.sh` | `tests/bats-tests/ui.bats` | Stub `log` |
| `scripts/resolve-compose-stack.sh` | `tests/test-compose-stack.sh` | Extension manifests |

**Python modules:**

| Source Module | Test Location | Fixtures Needed |
|---------------|---------------|-----------------|
| `routers/agents.py` | `tests/test_routers.py` (or split) | `test_client`, `mock_aiohttp_session` |
| `routers/features.py` | `tests/test_routers.py` | `test_client` |
| `routers/privacy.py` | `tests/test_routers.py` | `test_client` |
| `routers/setup.py` | `tests/test_routers.py` | `test_client`, `setup_config_dir` |
| `routers/updates.py` | `tests/test_routers.py` | `test_client` |
| `routers/workflows.py` | `tests/test_routers.py` | `test_client` |
| `helpers.py` | `tests/test_helpers.py` | `data_dir`, `install_dir`, `mock_aiohttp_session` |
| `security.py` | `tests/test_security.py` | `test_client` |
| `gpu.py` | `tests/test_gpu.py` | None (reads system info) |

---

### Phase 3: Generate Tests by Type

Process gaps in priority order (CRITICAL first). For each module:

#### 3.1 Read the Source Module

Read the source file and identify:
- All public functions (shell) or endpoints/functions (python)
- Their signatures, parameters, and dependencies
- Which dependencies need mocking vs real fixtures
- Edge cases: empty inputs, missing env vars, error conditions

#### 3.2 Write Shell (BATS) Tests

Follow ODS BATS conventions strictly:

```bash
#!/usr/bin/env bats
# ============================================================================
# BATS tests for installers/lib/<module>.sh
# ============================================================================
# Tests: <function_1>(), <function_2>()

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions that <module>.sh expects
    error() { echo "ERROR: $*" >&2; return 1; }
    export -f error
    log() { :; }
    export -f log

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/<module>.sh"
}

# -- <function_1> -----------------------------------------------------------

@test "<function_1>: <expected behavior for happy path>" {
    # Arrange
    INPUT_VAR="valid-value"

    # Act
    <function_1>

    # Assert
    assert_equal "$OUTPUT_VAR" "expected"
}

@test "<function_1>: <expected behavior for edge case>" {
    # Arrange
    INPUT_VAR=""

    # Act
    run <function_1>

    # Assert
    assert_failure
}
```

#### 3.3 Write Python (pytest) Tests

Follow ODS pytest conventions:

```python
"""Tests for <module>."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestEndpointName:
    """Tests for /endpoint."""

    def test_returns_expected_result(self, test_client):
        """GET /endpoint returns correct data."""
        response = test_client.get("/endpoint", headers=test_client.auth_headers)
        assert response.status_code == 200
        assert "key" in response.json()

    def test_rejects_unauthenticated(self, test_client):
        """GET /endpoint requires auth."""
        response = test_client.get("/endpoint")
        assert response.status_code == 403
```

**Rules:**
- Use `class Test*` grouping per function/endpoint
- Use AAA pattern (Arrange/Act/Assert)
- Use fixtures from conftest.py (`test_client`, `install_dir`, `data_dir`, etc.)
- Do NOT add `try/except` in tests (Let It Crash principle)
- Do NOT modify source code to make it "more testable"

---

### Phase 4: Validate Tests

#### 4.1 Run New Tests Only

**Shell:**
```bash
cd ods && bats tests/bats-tests/<module>.bats
```

**Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest tests/test_<module>.py -v --tb=short
```

#### 4.2 Fix Failures

If tests fail:
1. Read the error output carefully
2. Determine if the failure is in the TEST or the SOURCE code
3. Fix the test if the source code behavior is correct
4. NEVER modify source code to make tests pass (unless there's a genuine bug)

#### 4.3 Run Full Suite

After all new tests pass individually:

**Shell:**
```bash
cd ods && make test && make bats
```

**Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest tests/ -v --tb=short
```

#### 4.4 Run Linting

```bash
cd ods && make lint
```

---

### Phase 5: Coverage Verification

#### 5.1 Re-run Coverage

**Shell (structural):**
```bash
# Re-check which modules now have tests
for lib in ods/installers/lib/*.sh; do
  name=$(basename "$lib" .sh)
  if [ -f "ods/tests/bats-tests/${name}.bats" ]; then
    echo "COVERED: $name"
  else
    echo "MISSING: $name"
  fi
done
```

**Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest --cov=. --cov-report=term-missing -q --tb=no 2>&1 | tail -40
```

#### 5.2 Compare Before/After

Report coverage delta:

```
## Coverage Report

### Shell (Structural)
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Modules with BATS tests | 5/N | ?/N | +? |
| Integration test scripts | X | ? | +? |

### Python
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Total Coverage | ??% | ??% | +??% |
| Files with 0% | X | ? | -? |

### Per-Module Improvements

| Module | Before | After | New Tests Added |
|--------|--------|-------|-----------------|
| <module> | No tests | BATS test | 5 |
| helpers.py | 45% | ??% | 3 |
```

#### 5.3 Ask Whether to Iterate

If coverage is still below target, ask the user rather than auto-iterating:

```
AskUserQuestion:
  question: "Coverage improved. Want to continue adding tests?"
  header: "Iterate"
  multiSelect: false
  options:
    - label: "Continue — add more tests"
      description: "<K modules remaining without tests — next batch would cover <list>"
    - label: "Good enough — stop here"
      description: "Accept current coverage and move on"
    - label: "Switch focus"
      description: "Cover a different area instead of continuing the current batch"
```

If continuing:
1. Re-run Phase 1 to identify remaining gaps
2. Focus on modules with the largest gaps
3. Repeat Phases 3-5 until target is reached or user stops

---

### Phase 6: Summary

Present final results:

```
## Test Coverage Update Summary

**Target**: N% (Python) / structural (Shell)
**Tests Added**: X BATS, Y pytest
**Suites Passing**: make test, make bats, pytest

### New Test Files Created
- tests/bats-tests/<module>.bats (N tests)
- dashboard-api/tests/test_<module>.py (N tests)

### Modified Test Files
- tests/bats-tests/<module>.bats (+N tests)

### Coverage by Area
| Area | Coverage | Status |
|------|----------|--------|
| installers/lib/ | X/Y modules tested | OK/NEEDS WORK |
| scripts/ | X/Y scripts tested | OK/NEEDS WORK |
| dashboard-api/ | ??% | OK/NEEDS WORK |

### Remaining Gaps (if any)
- installers/lib/<module>.sh - Complex I/O, needs platform mocking
- ...

### Verification Commands
cd ods && make test && make bats                              # Shell tests
cd ods/extensions/services/dashboard-api && pytest tests/ -v  # Python tests
cd ods && make gate                                           # Full gate
```

---

## Anti-Patterns to Avoid

### DO NOT write tests that:
- Test private/internal functions directly (test via public interface)
- Duplicate existing test coverage (check first!)
- Depend on test execution order
- Use `sleep` for synchronization
- Catch exceptions that should propagate (Let It Crash)
- Use `|| true` or silent error suppression

### DO NOT:
- Modify source code to make it "more testable" (test what exists)
- Add type stubs or docstrings to source files (only touch test files)
- Create test utility frameworks or base classes (KISS)
- Write parameterized tests for < 3 cases (just write separate tests)

---

## Example Usage

```
# Full coverage update
/test-coverage

# Only shell tests
/test-coverage --type=shell

# Only Python tests targeting 70%
/test-coverage --type=python --target=70

# Single module focus
/test-coverage --module=tier-map

# Preview gaps without writing tests
/test-coverage --dry-run

# Fix existing failures first, then add coverage
/test-coverage --fix
```

## Notes

- Always read existing BATS tests for patterns before writing new ones
- Always read `conftest.py` before writing pytest tests — fixture availability changes
- Shell structural coverage = which modules have BATS test files
- Python coverage = pytest-cov line-level coverage
- BATS tests load `bats-support` and `bats-assert` from `tests/bats/`
- Never mock Pydantic validation — let ValidationError propagate per Let It Crash
- ODS CLAUDE.md forbids `2>/dev/null` — never suppress errors in tests
