---
description: Enforce strict TDD workflow - write test first, see it fail, then implement
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Edit, Write, Task
argument-hint: <feature-description> [--shell|--python] [--file <test_file>]
---

# TDD TDD TDD - Strict Test-Driven Development

Enforce the classic **Red -> Green -> Refactor** cycle. Tests are written FIRST, implementation comes SECOND.

> "TDD TDD TDD" - the repetition is intentional. This skill enforces discipline.

ODS has two test ecosystems: **Shell (BATS + bash assert)** and **Python (pytest)**. This skill handles both.

## Arguments

- `$ARGUMENTS` - Feature description and options:
  - First part: Feature description in quotes or as text
  - `--shell` - Write shell/BATS test (default for installers/, scripts/, ods-cli)
  - `--python` - Write Python/pytest test (default for dashboard-api/)
  - `--file <path>` - Explicit test file path

## The Three Laws of TDD

1. **You may not write production code until you have written a failing test**
2. **You may not write more test than is sufficient to fail**
3. **You may not write more production code than is sufficient to pass the test**

---

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at decision points.** TDD involves judgment about what to test, test granularity, and when the implementation is "done." The user should direct these choices.

- **Before** writing the test — confirm what behavior to test and test type
- **After** RED phase — validate that the failure is the right kind before implementing
- **After** GREEN phase — ask if the implementation approach is acceptable
- **After** each cycle — ask whether to iterate or stop

## Workflow

### Phase 0: Understand the Feature

Before writing anything:

1. **Parse `$ARGUMENTS`** to extract feature description and flags
2. **Analyze the feature** - what behavior needs to be tested?
3. **Determine test ecosystem** (Shell or Python) based on the feature area:

#### Shell Test Locations (BATS)

| Feature Area | Test Path | Implementation Path |
|--------------|-----------|---------------------|
| Installer libraries | `tests/bats-tests/<lib>.bats` | `installers/lib/<lib>.sh` |
| Installer phases | `tests/bats-tests/<phase>.bats` | `installers/phases/<phase>.sh` |
| Scripts | `tests/test-<script>.sh` | `scripts/<script>.sh` |
| CLI features | `tests/test-<feature>.sh` | `ods-cli` |
| Compose selection | `tests/bats-tests/compose-select.bats` | `installers/lib/compose-select.sh` |

#### Python Test Locations (pytest)

| Feature Area | Test Path | Implementation Path |
|--------------|-----------|---------------------|
| API routers | `extensions/services/dashboard-api/tests/test_<router>.py` | `extensions/services/dashboard-api/routers/<router>.py` |
| API helpers | `extensions/services/dashboard-api/tests/test_helpers.py` | `extensions/services/dashboard-api/helpers.py` |
| Security | `extensions/services/dashboard-api/tests/test_security.py` | `extensions/services/dashboard-api/security.py` |
| GPU detection | `extensions/services/dashboard-api/tests/test_gpu.py` | `extensions/services/dashboard-api/gpu.py` |

4. **Confirm test approach with user:**

   ```
   AskUserQuestion:
     question: "Here's my understanding. Does this test plan look right?"
     header: "Test plan"
     multiSelect: false
     options:
       - label: "Looks good — write the test"
         description: "Test: <what behavior to test>. Type: <shell/python>. File: <test path>"
       - label: "Different behavior to test"
         description: "I want to test something different than what you described"
       - label: "Different test type"
         description: "Use <shell instead of python / vice versa>"
       - label: "Multiple test cases"
         description: "I want to test several scenarios — let me list them"
   ```

5. **Check existing tests** for patterns and fixtures.

   For Shell/BATS:
   ```bash
   ls ods/tests/bats-tests/
   ```

   For Python:
   ```bash
   ls ods/extensions/services/dashboard-api/tests/
   ```

---

### Phase 1: RED - Write Failing Test

**CRITICAL**: Write the test FIRST. No implementation code yet.

#### Shell (BATS) Test Template

```bash
#!/usr/bin/env bats
# Tests for <module>

load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions that lib modules expect
    error() { echo "ERROR: $*" >&2; return 1; }
    export -f error
    log() { :; }
    export -f log

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/<module>.sh"
}

@test "<function_name>: <expected behavior>" {
    # Arrange
    SOME_VAR="test-value"

    # Act
    <function_name>

    # Assert
    assert_equal "$RESULT_VAR" "expected-value"
}
```

#### Python (pytest) Test Template

```python
"""Tests for <module>."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestFeatureName:
    """Tests for [feature description]."""

    def test_feature_does_expected_thing(self, test_client):
        """[Feature] should [expected behavior]."""
        # Arrange
        # ... setup test data

        # Act
        response = test_client.get("/endpoint", headers=test_client.auth_headers)

        # Assert
        assert response.status_code == 200
        assert response.json()["key"] == "expected"
```

#### Step 1.2: Run Test and VERIFY IT FAILS

**For Shell/BATS:**
```bash
cd ods && bats tests/bats-tests/<module>.bats
```

**For Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest tests/test_<module>.py -v -k "test_function_name"
```

**STOP AND CHECK**:
- [ ] Does the test fail?
- [ ] Does it fail for the RIGHT reason? (e.g., `ImportError`, missing function, assertion error)

**If test PASSES without implementation**:
> The test is invalid. It doesn't test new behavior. REWRITE IT.

**If test fails with wrong error**:
> The test has setup issues. Fix the test setup, then continue.

**If test fails with assertion error or import error**:
> Perfect! This is the RED state. Proceed to GREEN phase.

#### Step 1.3: Document the Expected Failure

Before moving on, note:
- What error message appeared
- What the test is actually testing
- What implementation is needed

---

### Phase 2: GREEN - Minimal Implementation

**RULE**: Write the MINIMUM code to make the test pass. Nothing more.

#### Step 2.1: Implement Just Enough

```bash
# BAD - Over-engineering in shell
resolve_something() {
    local input="$1"
    log "Processing $input"
    validate_input "$input" || { error "Invalid"; return 1; }
    local cached; cached=$(check_cache "$input")
    if [ -n "$cached" ]; then echo "$cached"; return 0; fi
    # ... complex logic
}

# GOOD - Minimal implementation for test
resolve_something() {
    local input="$1"
    echo "resolved-$input"
}
```

#### Step 2.2: Run Test and VERIFY IT PASSES

**For Shell/BATS:**
```bash
cd ods && bats tests/bats-tests/<module>.bats
```

**For Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest tests/test_<module>.py -v -k "test_function_name"
```

**STOP AND CHECK**:
- [ ] Does the test pass?
- [ ] Did you write ONLY what was needed?

**If test still fails**:
> Debug the implementation. Do NOT modify the test (unless it has a bug).

**If test passes**:
> GREEN state achieved. Proceed to REFACTOR phase.

---

### Phase 3: REFACTOR - Clean Up

**RULE**: Improve code quality while keeping tests GREEN.

#### Step 3.1: Check for Code Smells

Review both test and implementation for:
- [ ] Duplicate code
- [ ] Poor naming
- [ ] Long functions (> 30 lines)
- [ ] Deep nesting (> 3 levels)

#### Step 3.2: Refactor If Needed

Make small improvements:
- Extract helper functions
- Improve variable names
- Remove duplication

#### Step 3.3: Run Tests After Each Change

**For Shell/BATS:**
```bash
cd ods && bats tests/bats-tests/<module>.bats
```

**For Python:**
```bash
cd ods/extensions/services/dashboard-api && pytest tests/test_<module>.py -v
```

**If test fails after refactoring**:
> UNDO the refactoring. Refactoring should never break tests.

#### Step 3.4: Run Linting

**For Shell:**
```bash
cd ods && bash -n <modified-file> && make lint
```

**For Python:**
```bash
cd ods && python3 -m py_compile extensions/services/dashboard-api/<modified-file>
```

#### Step 3.5: Run Full Test Suite

```bash
# Shell: ensure no regressions
cd ods && make test && make bats

# Python: ensure no regressions
cd ods/extensions/services/dashboard-api && pytest tests/ -v
```

---

## Iteration

After completing one Red-Green-Refactor cycle, ask the user:

```
AskUserQuestion:
  question: "Cycle complete — test passes and code is clean. Continue with more test cases?"
  header: "Iterate"
  multiSelect: false
  options:
    - label: "Next test case"
      description: "Suggested next: <edge case or error condition based on feature>"
    - label: "I'll specify the next test"
      description: "I have a specific scenario I want tested next"
    - label: "Done — feature complete"
      description: "Current test coverage is sufficient for this feature"
    - label: "Run full test suite"
      description: "Verify no regressions before stopping"
```

Common progression:
1. Happy path test
2. Edge case tests
3. Error handling tests
4. Integration tests (if needed)

---

## Anti-Patterns to AVOID

### DON'T: Write Implementation First
```
# WRONG - This is not TDD
1. Write function
2. Write test
3. Test passes

# RIGHT - TDD way
1. Write test
2. Test fails (RED)
3. Write function
4. Test passes (GREEN)
5. Refactor
```

### DON'T: Write Tests That Can't Fail
```bash
# BAD - This BATS test is useless
@test "true returns true" {
    run true
    assert_success
}

# GOOD - Testing behavior
@test "resolve_tier_config: tier 0 returns error" {
    TIER=0
    run resolve_tier_config
    assert_failure
}
```

### DON'T: Over-Engineer in GREEN Phase
```bash
# BAD - Too much for first iteration
resolve_tier_config() {
    validate_tier "$TIER" || { error "Invalid tier"; return 1; }
    log "Resolving tier $TIER"
    case "$TIER" in
        1) TIER_NAME="Entry Level"; LLM_MODEL="qwen3.5-9b" ;;
        # ... 10 more tiers with caching, logging, etc.
    esac
}

# GOOD - Start simple, add complexity via new tests
resolve_tier_config() {
    case "$TIER" in
        1) TIER_NAME="Entry Level"; LLM_MODEL="qwen3.5-9b" ;;
    esac
}
```

---

## Example Session

**User**: `/tdd "add validation for GPU tier 0 in tier-map.sh"`

**Phase 0 - Understand**:
- Feature: Validate that tier 0 is rejected
- Test type: Shell (BATS)
- Test location: `tests/bats-tests/tier-map.bats`
- Implementation: `installers/lib/tier-map.sh`

**Phase 1 - RED**:
```bash
# tests/bats-tests/tier-map.bats (append)
@test "resolve_tier_config: tier 0 returns error" {
    TIER=0
    run resolve_tier_config
    assert_failure
    assert_output --partial "ERROR"
}
```

Run: `cd ods && bats tests/bats-tests/tier-map.bats --filter "tier 0"`
Result: FAILS (tier 0 falls through with no error) - Good!

**Phase 2 - GREEN**:
```bash
# installers/lib/tier-map.sh — add to resolve_tier_config()
resolve_tier_config() {
    if [ "${TIER:-0}" -eq 0 ]; then
        error "GPU tier 0 is unsupported — no compatible GPU detected"
        return 1
    fi
    # ... existing cases
}
```

Run: `cd ods && bats tests/bats-tests/tier-map.bats --filter "tier 0"`
Result: PASSES - Good!

**Phase 3 - REFACTOR**:
- Check naming: "unsupported" is clear
- Run linting: `bash -n installers/lib/tier-map.sh`
- Run full suite: `make test && make bats`

**Iterate**: Next test - negative tier value, non-numeric tier...

---

## ODS-Specific Patterns

### BATS Test Setup

All BATS tests in ODS follow this pattern:

```bash
load '../bats/bats-support/load'
load '../bats/bats-assert/load'

setup() {
    # Stub logging functions the lib expects
    error() { echo "ERROR: $*" >&2; return 1; }
    export -f error
    log() { :; }
    export -f log

    # Source the library under test
    source "$BATS_TEST_DIRNAME/../../installers/lib/<module>.sh"
}
```

### Python Test Fixtures (from conftest.py)

Available fixtures for dashboard-api tests:
- `test_client` - FastAPI TestClient with Bearer auth (use `test_client.auth_headers`)
- `install_dir` - Isolated install directory with `.env` file
- `data_dir` - Isolated data directory for bootstrap/token files
- `setup_config_dir` - Isolated config directory for setup/persona files
- `mock_aiohttp_session` - Factory for mock aiohttp sessions

### Mocking External Services (Python)

```python
from unittest.mock import AsyncMock, patch

def test_with_mocked_service(test_client, mock_aiohttp_session):
    """Test with mocked external HTTP call."""
    session = mock_aiohttp_session(status=200, json_data={"ok": True})
    with patch("helpers._get_aio_session", AsyncMock(return_value=session)):
        response = test_client.get("/health", headers=test_client.auth_headers)
        assert response.status_code == 200
```

---

## Quick Reference

| Phase | Action | Verify |
|-------|--------|--------|
| RED | Write test | Test FAILS |
| GREEN | Write minimal impl | Test PASSES |
| REFACTOR | Clean up | Tests still PASS |

```bash
# Shell quick commands
cd ods && bats tests/bats-tests/<lib>.bats          # Run BATS test
cd ods && make test && make bats                      # Run all shell tests
cd ods && bash -n <file> && make lint                 # Lint shell

# Python quick commands
cd ods/extensions/services/dashboard-api && pytest tests/test_<module>.py -v -k "test_name"
cd ods/extensions/services/dashboard-api && pytest tests/ -v   # Run all
cd ods && make lint                                             # Lint all
```

---

## Notes

- Always check existing BATS tests in `tests/bats-tests/` for patterns before writing new ones
- Always check `dashboard-api/tests/conftest.py` for available pytest fixtures
- Shell tests use `set -euo pipefail` implicitly via BATS
- BATS tests use `assert_equal`, `assert_success`, `assert_failure`, `assert_output` from bats-assert
- Python tests: do NOT add `try/except` in tests (Let It Crash principle)
- Coverage is tracked structurally for shell (which modules have BATS tests vs not)
