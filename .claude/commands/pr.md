---
description: Create a new branch, commit changes, push, create PR, and fix all failed checks (except claude review)
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Edit, Write, Task, WebFetch
argument-hint: <branch-name> [commit message]
---

# PR Workflow Skill

Create a new branch from main, stage all changes, commit, push, create a PR, and iteratively fix all failing checks until they pass.

## Arguments

- `$ARGUMENTS` - Branch name (required) and optional commit message
  - Format: `<branch-name> [commit message in quotes]`
  - Example: `feat/add-auth "Add user authentication"`

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at every decision point.** PRs are visible to the team — assumptions about commit messages, PR descriptions, and fix strategies should be validated. Specifically:

- **Before** committing — confirm commit message and staged files
- **Before** creating the PR — confirm title and description
- **When** checks fail — ask how to handle before auto-fixing
- **After** completion — ask about next steps

## Workflow

### 1. Parse Arguments

Extract branch name and commit message from `$ARGUMENTS`:
- First word is the branch name
- Remaining text (if any) is the commit message
- If no commit message provided, generate one based on staged changes

### 2. Verify Clean State & Create Branch

```bash
# Fetch latest from origin
git fetch origin

# Create and checkout new branch from main
git checkout -b <branch-name> origin/main
```

### 3. Stage and Commit Changes

```bash
# Check what files have changes
git status

# Stage all changes
git add -A

# If no commit message provided, analyze changes for a message
git diff --cached --stat
```

#### 3a. Confirm What Gets Committed

Before staging, present the changes and ask the user to confirm:

```
AskUserQuestion:
  question: "These files will be staged and committed. Does this look right?"
  header: "Stage"
  multiSelect: false
  options:
    - label: "Stage all changes"
      description: "<N files changed, +X/-Y lines — summary of key changes>"
    - label: "Let me pick files"
      description: "I want to selectively stage specific files"
    - label: "Review changes first"
      description: "Show me the diff before I decide"
```

**If "Let me pick files"**: Ask which files to include/exclude.

Generate a commit message following conventional commits format if not provided:
- `feat:` for new features
- `fix:` for bug fixes
- `refactor:` for refactoring
- `docs:` for documentation
- `test:` for tests
- `chore:` for maintenance

Commit with the message:
```bash
git commit -m "$(cat <<'EOF'
<commit message>

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

### 4. Push to Remote

```bash
git push -u origin <branch-name>
```

### 5. Create Pull Request

#### 5a. Confirm PR Title and Description

Before creating the PR, present the proposed title and summary for approval:

```
AskUserQuestion:
  question: "Here's the proposed PR. Ready to create it, or want to adjust?"
  header: "PR"
  multiSelect: false
  options:
    - label: "Create PR"
      description: "Title: '<proposed title>' — <N commits, summary of changes>"
    - label: "Change title"
      description: "I want a different PR title"
    - label: "Edit description"
      description: "I want to adjust the PR body/summary"
    - label: "Add reviewers"
      description: "I want to assign specific reviewers"
```

```bash
# Get the diff summary for PR description
git log origin/main..HEAD --oneline

# Create PR using gh CLI
gh pr create --title "<PR title>" --body "$(cat <<'EOF'
## Summary
<bullet points summarizing changes>

## Test plan
- [ ] <testing steps>

Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### 6. Monitor and Fix Failed Checks

**CRITICAL**: This is an iterative loop that continues until all checks pass (except claude review).

```bash
# Get the PR number
PR_NUMBER=$(gh pr view --json number -q .number)

# Wait for checks to start
sleep 10
```

**Check Loop**:

1. **Get check status**:
   ```bash
   gh pr checks $PR_NUMBER --json name,state,conclusion
   ```

2. **Identify failed checks** (exclude "claude" from check names):
   - Filter out any check with "claude" in the name (case-insensitive)
   - Focus on checks with `conclusion: "failure"` or `state: "failure"`

3. **For each failed check**, ask the user before fixing:

   ```
   AskUserQuestion:
     question: "<check-name> failed: <brief error>. How should I handle this?"
     header: "Check fail"
     multiSelect: false
     options:
       - label: "Auto-fix (Recommended)"
         description: "<Brief description of the proposed fix>"
       - label: "Show me the details"
         description: "Display the full error log before deciding"
       - label: "Skip this check"
         description: "Continue without fixing — I'll handle it manually"
       - label: "Abort"
         description: "Stop the PR workflow — I need to investigate"
   ```

   If approved:
   - Get the check run details and logs
   - Analyze the failure reason
   - Fix the issue in the code
   - Commit the fix with message: `fix: address <check-name> failure`
   - Push the changes

4. **Wait and re-check**:
   ```bash
   # Wait for checks to re-run
   sleep 30

   # Check status again
   gh pr checks $PR_NUMBER
   ```

5. **Repeat until all non-claude checks pass**

### 7. Report Success

Once all checks pass (or only claude review remains):
- Display the PR URL
- List all commits made
- Summarize what was fixed

Ask the user about next steps:

```
AskUserQuestion:
  question: "PR created and all checks passing. What next?"
  header: "Next"
  multiSelect: false
  options:
    - label: "Done"
      description: "PR is ready — no further action needed"
    - label: "Run make gate"
      description: "Run the full local validation suite (lint + test + bats + smoke + simulate)"
    - label: "Request review"
      description: "Assign reviewers to the PR"
    - label: "Run code review"
      description: "Run /code-review on the PR changes"
```

## Skipped Checks

The following checks are intentionally skipped and NOT addressed:
- Any check with "claude" in the name (claude review, claude-review, etc.)

## Example Usage

```
/pr feat/add-dark-mode "Add dark mode toggle to settings"
/pr fix/auth-bug
/pr refactor/cleanup-utils "Refactor utility functions for clarity"
```

## Notes

- Always creates branch from `origin/main`
- Uses conventional commit format
- Will make multiple commits if multiple check fixes are needed
- Times out after 10 failed check fix attempts (asks for user guidance)
- Never skips or bypasses checks - always fixes the underlying issue
