---
description: Code review git changes using multi-model consensus
allowed-tools: AskUserQuestion, Bash, Read, Edit, Glob, Grep, mcp__pal__consensus, mcp__pal__codereview, mcp__pal__listmodels
argument-hint: [commits=5] [--staged] [--branch <target>] [--focus <area>] [--post-pr]
---

# Git Code Review with Multi-Model Consensus

Perform comprehensive code review using PAL MCP multi-model consensus. Reviews recent commits, staged changes, or branch comparisons with focus on security, quality, performance, and architecture.

## Arguments

- `$ARGUMENTS` - Review configuration:
  - `commits=N` - Number of recent commits to review (default: 5)
  - `--staged` - Review only staged changes instead of commits
  - `--branch <target>` - Compare current branch against target branch (e.g., `main`)
  - `--focus <area>` - Focus area: `security`, `performance`, `quality`, `architecture`, or `full` (default: `full`)
  - `--post-pr` - Post review as PR comment if in PR context

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at every decision point.** Code review is subjective — what matters depends on context, and assumptions about reviewer priorities are frequently wrong. The cost of asking is low; the cost of reviewing the wrong scope or missing what the user cares about is high. Specifically:

- **Before** reviewing — confirm the review scope and what the user cares about
- **When** the diff is large — let the user choose what to focus on first
- **When** categorization is ambiguous — ask about severity levels
- **When** models disagree — let the user break the tie on issue severity
- **After** findings — ask what to do next (fix, re-review, post to PR)
- **When** tradeoffs exist — present them explicitly instead of picking silently

The user should feel like they're directing the review, not receiving a fire-and-forget report.

## Workflow

### 1. Parse Arguments

Extract configuration from `$ARGUMENTS`:
- Default: `commits=5`, `focus=full`
- Flags: `--staged`, `--branch`, `--focus`, `--post-pr`

Determine review mode (mutually exclusive):

| Mode | Trigger | Description |
|------|---------|-------------|
| Commits | Default or `commits=N` | Review last N commits |
| Staged | `--staged` flag | Review staged changes only |
| Branch | `--branch <target>` | Compare against target branch |

### 2. Validate Git State

```bash
# Verify git repository
git rev-parse --is-inside-work-tree

# Get current branch
CURRENT_BRANCH=$(git branch --show-current)
echo "Current branch: $CURRENT_BRANCH"
```

Mode-specific validation:

**For Commits mode:**
```bash
# Check available commits
TOTAL_COMMITS=$(git rev-list --count HEAD)
if [ "$TOTAL_COMMITS" -lt "$N" ]; then
  echo "Warning: Only $TOTAL_COMMITS commits available, reviewing all"
fi
```

**For Staged mode:**
```bash
# Verify staged changes exist
if [ -z "$(git diff --cached --name-only)" ]; then
  echo "Error: No staged changes to review"
  echo "Hint: Stage changes with 'git add <files>' first"
  exit 1
fi
```

**For Branch mode:**
```bash
# Verify target branch exists
git fetch origin "$TARGET"
git rev-parse --verify "origin/$TARGET" || {
  echo "Error: Branch 'origin/$TARGET' not found"
  echo "Available remote branches:"
  git branch -r | head -10
  exit 1
}
```

### 3. Discover Available Models

Call `mcp__pal__listmodels` to get available models for consensus. Select top 2 models by score, preferring different providers for diversity.

**Model Selection Criteria:**
1. Sort available models by score descending
2. Pick first 3 distinct providers if possible
3. Fallback to fewer models if not enough available

### 4. Gather Git Context

#### For Commits Mode (default)
```bash
N=5  # or parsed value

# Get files changed in last N commits
git log --oneline -$N --name-only --pretty=format: | sort -u | grep -v '^$'

# Get diff with extended context (10 lines instead of default 3)
git diff -U10 HEAD~$N..HEAD

# Get commit summaries with authors
git log --oneline -$N --format='%h %s (%an)'

# Get diff statistics
git diff --stat HEAD~$N..HEAD
```

#### For Staged Mode
```bash
# Get staged files
git diff --cached --name-only

# Get staged diff with extended context
git diff -U10 --cached

# Get staged statistics
git diff --cached --stat
```

#### For Branch Comparison Mode
```bash
TARGET=main  # or parsed value

# Get files changed vs target branch
git diff --name-only origin/$TARGET...HEAD

# Get full diff vs target with extended context
git diff -U10 origin/$TARGET...HEAD

# Get commits on this branch not in target
git log --oneline origin/$TARGET..HEAD

# Get diff statistics
git diff --stat origin/$TARGET...HEAD
```

#### 4a. Confirm Review Scope with User

After gathering git context, present a summary of what will be reviewed and let the user confirm or adjust:

```
AskUserQuestion:
  question: "Here's what I'll be reviewing. Does this scope look right?"
  header: "Scope"
  multiSelect: false
  options:
    - label: "Looks good — proceed"
      description: "<N files changed, +X/-Y lines, commits/staged/branch summary>"
    - label: "Too much — narrow scope"
      description: "I only want to review a subset of these changes"
    - label: "Too little — expand scope"
      description: "Include more commits or compare against a different branch"
    - label: "Different focus"
      description: "I want to focus on a specific area (security, performance, etc.)"
```

**If "Too much — narrow scope"**: Ask which files or directories to focus on:

```
AskUserQuestion:
  question: "Which areas should I focus the review on?"
  header: "Focus"
  multiSelect: true
  options:
    - label: "Shell scripts (installers/, scripts/, ods-cli)"
      description: "<N files, +X/-Y lines in shell scripts>"
    - label: "Python API (dashboard-api/)"
      description: "<N files, +X/-Y lines in dashboard-api/>"
    - label: "Dashboard UI (dashboard/src/)"
      description: "<N files, +X/-Y lines in dashboard/>"
    - label: "Docker/Config"
      description: "<N files in docker-compose, manifests, config/>"
```

**If "Different focus"**: Ask which review focus area to use, overriding the `--focus` flag.

### 5. Categorize Changed Files

Map files to review focus areas:

| Pattern | Category | Review Focus |
|---------|----------|--------------|
| `*.sh` in `installers/lib/` | Installer Libraries | `set -euo pipefail`, pure functions, POSIX compat |
| `*.sh` in `installers/phases/` | Installer Phases | Sequential correctness, error handling |
| `*.sh` in `scripts/` | Operational Scripts | Safety, idempotency |
| `ods-cli` | CLI Tool | Command handling, user-facing output |
| `*.py` in `dashboard-api/` | Python API | FastAPI patterns, security, SOLID |
| `*.jsx`, `*.tsx` in `dashboard/src/` | Dashboard UI | React patterns, Tailwind usage |
| `manifest.yaml` | Extension Manifests | Schema compliance, port conflicts |
| `docker-compose*.yml` | Docker Config | Security (127.0.0.1 binding), correctness |
| `*.md` | Documentation | Accuracy, consistency with code |
| `config/*.json` | Backend Config | Valid JSON, tier/model correctness |

Skip binary files (note them but don't include in diff review).

### 6. Handle Large Diffs

If diff exceeds ~1000 lines:

#### 6a. Ask User How to Handle Large Diff

```
AskUserQuestion:
  question: "The diff is large (~<N> lines across <M> files). How should I handle it?"
  header: "Large diff"
  multiSelect: false
  options:
    - label: "Review in chunks by category (Recommended)"
      description: "Group files by type (shell, python, frontend, config) and review each separately"
    - label: "Focus on highest-risk files only"
      description: "Skip low-risk changes (docs, tests, config) and focus on production code"
    - label: "Review everything in one pass"
      description: "Send the full diff to models — may lose detail on individual files"
    - label: "Let me pick specific files"
      description: "I'll tell you exactly which files to review"
```

**If "Let me pick specific files"**: Ask which files to review, then scope the diff down accordingly.

Then proceed with the chosen strategy:

1. **Group files by category** (shell, python, frontend, config)
2. **Review each group separately** with PAL consensus
3. **Synthesize findings** from all chunks into single report
4. **Deduplicate** overlapping issues

### 7. Run PAL MCP Consensus

Use `mcp__pal__consensus` with the models discovered in Step 3.

**Consensus Workflow:**
- `step 1`: Your initial analysis with the diff
- `step 2`: First model response + notes
- `step 3`: Second model response + notes
- `step 4`: Third model response + synthesis
- `total_steps` = number of models + 1

**Review Focus Areas:**

| Focus | Checks |
|-------|--------|
| `security` | Secrets, injection, auth, `set -euo pipefail`, 127.0.0.1 binding, input validation |
| `performance` | Unnecessary subshells, large file iteration, Docker layer caching |
| `quality` | KISS, pure functions, naming, error handling (Let It Crash) |
| `architecture` | Extension system compliance, functional core / imperative shell |
| `full` | All of the above (default) |

**If PAL consensus fails:** Fall back to `mcp__pal__codereview` for single-model review.

#### 7a. Present Model Disagreements to User

If models disagree on the severity or existence of an issue, **do NOT resolve the disagreement yourself**. Present each significant disagreement:

```
AskUserQuestion:
  question: "Models disagreed on <specific issue>. How should this be classified?"
  header: "Severity"
  multiSelect: false
  options:
    - label: "<Model A's severity> (e.g., Critical)"
      description: "<Model A> flagged this as <severity> because: <reasoning>"
    - label: "<Model B's severity> (e.g., Low)"
      description: "<Model B> considers this <severity> because: <reasoning>"
    - label: "Dismiss this finding"
      description: "This isn't actually an issue — it's intentional or acceptable"
```

Repeat for each significant disagreement (up to 4 per `AskUserQuestion` call). Skip for minor differences that don't change the severity tier.

#### 7b. Validate Critical/High Findings

If any findings are classified as **Critical** or **High**, present them to the user for confirmation before including in the final report. False positives at these severity levels erode trust in the review:

```
AskUserQuestion:
  question: "I found <N> critical/high issues. Do these look like real problems, or should I reclassify any?"
  header: "Validate"
  multiSelect: true
  options:
    - label: "<Issue 1 summary>"
      description: "[FILE:LINE] — <brief description>. Classified as <severity>"
    - label: "<Issue 2 summary>"
      description: "[FILE:LINE] — <brief description>. Classified as <severity>"
    - label: "<Issue 3 summary>"
      description: "[FILE:LINE] — <brief description>. Classified as <severity>"
    - label: "All look correct"
      description: "Keep all critical/high classifications as-is"
```

Selected items are confirmed as real issues. Unselected critical/high items should be downgraded to Medium with a note that the user reviewed and reclassified them. If "All look correct" is selected, keep everything as-is.

Skip this question if there are no Critical or High findings.

### 8. Format Output (GitHub PR Comment Style)

```markdown
## Code Review: Multi-Model Consensus

**Review Mode**: [Commits (last N) | Staged Changes | Branch Comparison vs TARGET]
**Files Reviewed**: N files
**Lines Changed**: +X / -Y

---

### Executive Summary
[2-3 sentence overview of changes and overall assessment]

---

### Critical Issues
> Must be addressed before merge

- [ ] **[FILE:LINE]** - [Issue description]
  - **Category**: Security/Performance/Bug Risk
  - **Severity**: Critical
  - **Recommendation**: [Specific fix]

### High Priority
> Should be addressed

- [ ] **[FILE:LINE]** - [Issue description]
  - **Category**: [Category]
  - **Recommendation**: [Specific fix]

### Medium Priority
> Recommended improvements

- [ ] **[FILE:LINE]** - [Issue description]
  - **Recommendation**: [Suggestion]

### Low Priority / Suggestions
> Nice to have

- [ ] [Suggestion]

---

### Positive Observations
- [Good pattern observed]
- [Well-implemented feature]

---

### Review Summary

| Category | Rating | Notes |
|----------|--------|-------|
| Security | X/5 | [Brief note] |
| Code Quality | X/5 | [Brief note] |
| Performance | X/5 | [Brief note] |
| Architecture | X/5 | [Brief note] |
| Test Coverage | X/5 | [Brief note] |

**Overall Assessment**: [APPROVE / REQUEST CHANGES / NEEDS DISCUSSION]

---

### Model Consensus

| Model | Key Findings |
|-------|--------------|
| [Model 1] | [Summary of key points] |
| [Model 2] | [Summary of key points] |
| [Model 3] | [Summary of key points] |

**Agreement Areas**: [Where models agreed]
**Divergent Views**: [Where models differed, if any]

---

*Generated by Claude Code Review with PAL MCP Consensus*
```

### 9. Optional: Post to PR

If `--post-pr` flag is set AND in a PR context:

```bash
# Check if we're in a PR context
PR_NUMBER=$(gh pr view --json number -q .number || echo "")

if [ -n "$PR_NUMBER" ]; then
  # Post review as PR comment
  gh pr comment "$PR_NUMBER" --body "$(cat <<'EOF'
[Formatted review output]
EOF
)"
  echo "Review posted to PR #$PR_NUMBER"
else
  echo "Not in PR context - review displayed but not posted"
fi
```

### 10. Post-Review Follow-Up

Always ask the user what they want to do next after presenting the review:

```
AskUserQuestion:
  question: "Review complete (<overall assessment>). What would you like to do next?"
  header: "Next"
  multiSelect: false
  options:
    - label: "Auto-fix issues"
      description: "Attempt to fix all critical and high issues automatically"
    - label: "Review a specific file in detail"
      description: "Deep-dive into one file that needs closer inspection"
    - label: "Post to PR"
      description: "Post this review as a comment on the current PR"
    - label: "Done"
      description: "Review is complete — no further action needed"
```

**If "Auto-fix issues"**: Ask which severity levels to fix:

```
AskUserQuestion:
  question: "Which issues should I attempt to fix?"
  header: "Fix scope"
  multiSelect: true
  options:
    - label: "Critical issues (<N>)"
      description: "Must-fix items that block merge"
    - label: "High priority (<N>)"
      description: "Should-fix items"
    - label: "Medium priority (<N>)"
      description: "Recommended improvements"
    - label: "All issues"
      description: "Fix everything that can be automated"
```

Then attempt fixes using `Edit` and report results.

**If "Review a specific file in detail"**: Ask which file, then re-run consensus on just that file's diff with maximum context.

## Error Handling

| Scenario | Action |
|----------|--------|
| Not a git repo | Exit with error message |
| No commits in range | Warn and adjust N to available commits |
| No staged changes | Error with hint to use `git add` |
| Branch not found | Error with list of available branches |
| No changes to review | Exit early with message |
| PAL MCP unavailable | Fall back to `mcp__pal__codereview` |
| No models available | Error - PAL MCP not configured |

## Example Usage

```bash
# Review last 5 commits (default)
/code-review

# Review last 10 commits
/code-review commits=10

# Review only staged changes
/code-review --staged

# Compare feature branch against main
/code-review --branch main

# Security-focused review of last 3 commits
/code-review commits=3 --focus security

# Full review and post to PR
/code-review --branch main --post-pr

# Quick review of staged changes with security focus
/code-review --staged --focus security
```

## Notes

- Requires PAL MCP server configured with at least one model provider
- Models are discovered dynamically via `mcp__pal__listmodels`
- Review depth scales with diff size (larger diffs get chunked)
- Shell files get additional `set -euo pipefail` and POSIX compatibility review per CLAUDE.md
- Extended diff context (-U10) provides better code understanding
- Binary files are noted but excluded from diff review
