# Issue to PR — Autonomous CI Mode

You are running in a CI pipeline. You MUST operate fully autonomously.
Do NOT use AskUserQuestion. Do NOT pause for user input.

## Rules

1. Follow ALL design principles from CLAUDE.md: **Let It Crash** (primary), **KISS**, **Pure Functions**, **SOLID**
2. Keep changes **minimal and focused** — only implement what the issue requests
3. **Prefer no change over a wrong change** — if the issue is vague, ambiguous, or not actionable, make zero changes
4. Do NOT modify protected files:
   - `.github/workflows/*` — CI/CD pipelines
   - `.env*` — environment configuration
   - `ods/installers/*` — core installer libraries and phases
   - `ods/ods-cli` — main CLI tool
   - `ods/config/*` — backend configuration files
5. Do NOT modify unrelated code, design philosophy docs, or import ordering
6. Do NOT add unnecessary comments, docstrings, or type annotations to unchanged code
7. Do NOT create new files unless absolutely necessary — prefer editing existing files
8. Do NOT introduce security vulnerabilities (command injection, XSS, SQL injection, etc.)
9. All new Python code must pass `ruff check` and `python -m py_compile`
10. All new shell code must pass `bash -n` and `shellcheck`

## Steps

### Step 1: Understand the Issue

Read the issue details (appended below). Determine:
- Is this issue **actionable** with specific, implementable changes?
- Does it describe a bug fix, new feature, or enhancement with enough detail?
- If the issue is too vague (e.g., "make the app better", "improve performance"), make **zero changes**

### Step 2: Explore the Codebase

1. Read `CLAUDE.md` for project structure and conventions
2. Use Glob and Grep to find the files relevant to the issue
3. Read the relevant source files to understand existing patterns and conventions
4. Identify the minimal set of files that need changes

### Step 3: Implement Changes

1. Make targeted edits using the Edit tool (prefer Edit over Write for existing files)
2. Follow existing code patterns and conventions in the file you're editing
3. After each file edit, validate:
   - Python files: `python -m py_compile <file>` for syntax, `ruff check <file>` for linting
   - Shell files: `bash -n <file>` for syntax
4. Fix any syntax or lint errors before moving on

### Step 4: Verify Changes

1. Run `git diff` to review all changes
2. Verify changes are minimal and correctly address the issue
3. If related tests exist, run them with `pytest <test_file> -v`
4. Ensure no unintended modifications

## What NOT to Change

- **Protected files** listed in Rule 4 above
- **Design philosophy** sections in CLAUDE.md (Let It Crash, SOLID, KISS, Pure Functions)
- **Unrelated code** — do not refactor, clean up, or "improve" code outside the issue scope
- **Test files** — unless the issue specifically requests test changes
- **Frontend code** — unless the issue specifically involves the frontend

## Issue Details

The following issue details are provided by the workflow at runtime:
