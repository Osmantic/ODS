---
description: Assign roles to Agent teams for planning purposes given a user prompt
allowed-tools: AskUserQuestion, Read, Glob, Grep, Task, TaskOutput, TaskCreate, TaskUpdate, TaskList, Edit, Bash, mcp__pal__consensus, mcp__pal__listmodels
argument-hint: <task-description>
---

# Agent Team Planner

Analyze a user prompt, decompose it into workstreams, and interactively assign agent roles to form a coordinated team plan.

## Arguments

- `$ARGUMENTS` - A description of the task, feature, or project to plan for.

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at every decision point.** Do NOT assume user intent — validate it. The cost of asking is low; the cost of building the wrong plan is high. When in doubt, ask. Specifically:

- **Before** narrowing scope — confirm what's in and out
- **Before** committing to an approach — present alternatives
- **After** discovering something unexpected — share findings and get direction
- **Between** phases — checkpoint progress and confirm next steps
- **When** models disagree — let the user break the tie
- **When** tradeoffs exist — present them explicitly

The user should feel like a co-pilot throughout the entire planning process, not a rubber stamp at the end.

## Workflow

### Phase 1: Analyze the Prompt

Read and understand `$ARGUMENTS`. Identify:

1. **Scope** - What is being asked? (feature, refactor, bug fix, migration, research, etc.)
2. **Domains touched** - Which parts of the codebase are involved? (installer libs, installer phases, scripts, CLI, dashboard-api, dashboard UI, extensions, Docker config, tests, docs)
3. **Complexity signals** - Multi-file? Cross-cutting concerns? External dependencies? Breaking changes?
4. **Risks** - What could go wrong? What needs careful attention?

Before proceeding, explore the relevant codebase areas to ground your analysis in reality:

```
# Identify relevant files
Glob for patterns matching the task domains
Grep for key symbols, functions, or patterns mentioned in the prompt
Read critical files to understand current state
```

#### 1a. Validate Understanding with User

After initial analysis, **always** confirm your understanding before proceeding. Do NOT silently assume scope:

```
AskUserQuestion:
  question: "Here's my understanding of the task. Is this correct, or should I adjust scope?"
  header: "Scope"
  multiSelect: false
  options:
    - label: "Looks correct"
      description: "<1-2 sentence summary of your understanding of scope and domains>"
    - label: "Broader scope"
      description: "I want this to also cover <adjacent areas you identified but weren't sure about>"
    - label: "Narrower scope"
      description: "Focus only on the core ask — skip <peripheral concerns you identified>"
```

#### 1b. Clarify Ambiguities

If `$ARGUMENTS` contains ANY ambiguity — vague terms, multiple interpretations, or implicit assumptions — ask immediately:

```
AskUserQuestion:
  question: "<Specific clarifying question about the ambiguity>"
  header: "Clarify"
  multiSelect: false
  options:
    - label: "<Interpretation A>"
      description: "<What this would mean for the plan>"
    - label: "<Interpretation B>"
      description: "<What this would mean for the plan>"
    - label: "<Interpretation C>"
      description: "<What this would mean for the plan>"
```

Repeat for each distinct ambiguity (up to 4 questions per `AskUserQuestion` call). Do not batch unrelated ambiguities into a single question.

### Phase 2: Propose Workstreams

Based on your analysis, decompose the task into **2-5 parallel workstreams**. Each workstream is a cohesive unit of work that one agent can own.

Examples of workstream decomposition:

| Task Type | Possible Workstreams |
|-----------|---------------------|
| New extension | Manifest + compose, Backend integration, Dashboard UI, Tests, Docs |
| Installer fix | Root cause analysis, Fix in lib/phases, BATS tests, Smoke test update |
| CLI feature | ods-cli implementation, Shell tests, Documentation |
| Dashboard feature | API router, Frontend component, API tests, E2E flow |
| Refactor | Architecture design, Migration, Test updates, Cleanup |

#### 2a. Prioritize Workstreams with User

Before moving to consensus, ask the user to set priorities. This prevents over-investing in low-value workstreams:

```
AskUserQuestion:
  question: "Which aspect of this task matters most to you?"
  header: "Priority"
  multiSelect: false
  options:
    - label: "<Highest-value workstream>"
      description: "Focus resources here first — other workstreams are secondary"
    - label: "Balanced approach"
      description: "All workstreams are equally important — distribute effort evenly"
    - label: "<Speed-focused option>"
      description: "Get a working version fast — skip polish, defer tests/docs"
    - label: "<Quality-focused option>"
      description: "Thorough implementation with tests and review — take the time needed"
```

#### 2b. Surface Unexpected Findings

If codebase exploration in Phase 1 revealed surprises (tech debt, undocumented patterns, unexpected complexity), surface them NOW before committing to a plan:

```
AskUserQuestion:
  question: "I found something unexpected during exploration: <finding>. How should this affect our plan?"
  header: "Discovery"
  multiSelect: false
  options:
    - label: "Address it now"
      description: "Add a workstream or expand scope to handle this finding"
    - label: "Note and defer"
      description: "Acknowledge but don't let it expand scope — handle separately later"
    - label: "Ignore"
      description: "Not relevant to this task — proceed as planned"
```

Skip this question if exploration revealed no surprises.

### Phase 3: Multi-Model Consensus on Workstreams

Use PAL MCP consensus to validate and refine the proposed workstreams with multiple models before presenting to the user.

#### 3a. Discover Available Models

Call `mcp__pal__listmodels` to get available models. Select top 2 models by score, preferring different providers for diversity.

#### 3b. Run Consensus Review

Use `mcp__pal__consensus` to get multi-model input on the proposed plan. Include in your consensus prompt:

1. **The original task** (`$ARGUMENTS`)
2. **Codebase findings** from Phase 1 (key files, patterns, current architecture)
3. **Proposed workstreams** from Phase 2

Ask the models to evaluate:
- Are the workstreams correctly decomposed? (missing work, unnecessary splits, wrong boundaries)
- Are there dependency risks between workstreams that would block parallel execution?
- What are the highest-risk areas that need dedicated attention?
- Should any workstreams be merged or split further?

**Consensus Workflow:**
- `step 1`: Your initial workstream proposal with codebase context
- `step 2`: First model response + notes on disagreements
- `step 3`: Second model response + synthesis
- `total_steps` = number of models + 1

#### 3c. Present Consensus Disagreements to User

If models disagree on any aspect of the plan, **do NOT resolve the disagreement yourself**. Present each disagreement to the user for a decision:

```
AskUserQuestion:
  question: "Models disagreed on <specific point>. Which approach do you prefer?"
  header: "Tiebreak"
  multiSelect: false
  options:
    - label: "<Model A's recommendation>"
      description: "<Model A> suggests this because: <reasoning>"
    - label: "<Model B's recommendation>"
      description: "<Model B> suggests this because: <reasoning>"
    - label: "Neither — use my approach"
      description: "I have a different idea (provide details)"
```

Repeat for each significant disagreement (up to 4 questions per call).

#### 3d. Incorporate Consensus

Revise the workstream proposal based on model feedback AND user tiebreak decisions:
- Merge or split workstreams where models agreed on changes
- Apply user decisions where models disagreed
- Add risk flags where models identified concerns

**If PAL consensus fails:** Continue with your original Phase 2 proposal — consensus is advisory, not blocking.

### Phase 4: Interactive Role Assignment

Use `AskUserQuestion` to walk the user through team composition. Ask questions in sequence, adapting based on prior answers.

#### Question 1: Confirm Workstreams (Consensus-Informed)

Present the consensus-refined workstreams and ask the user to confirm or adjust. If models flagged concerns or suggested changes, note them in the option descriptions:

```
AskUserQuestion:
  question: "I've identified these workstreams for the task. Which should we staff?"
  header: "Workstreams"
  multiSelect: true
  options:
    - label: "<Workstream 1 name>"
      description: "<What this workstream covers and delivers>"
    - label: "<Workstream 2 name>"
      description: "<What this workstream covers and delivers>"
    - label: "<Workstream 3 name>"
      description: "<What this workstream covers and delivers>"
    - label: "<Workstream 4 name>"
      description: "<What this workstream covers and delivers>"
```

#### Question 2: Execution Strategy

Ask how the team should coordinate:

```
AskUserQuestion:
  question: "How should agents coordinate across workstreams?"
  header: "Strategy"
  multiSelect: false
  options:
    - label: "Parallel (Recommended)"
      description: "All workstreams execute simultaneously. Fastest, best for independent work."
    - label: "Sequential"
      description: "Workstreams execute in dependency order. Safest for tightly coupled changes."
    - label: "Hybrid"
      description: "Independent workstreams run in parallel; dependent ones are sequenced."
```

#### Question 3: Agent Specialization per Workstream

For each confirmed workstream, ask what agent role should own it. Present role options relevant to that workstream's nature.

Available agent roles and their strengths:

| Role | Best For | Agent Type | Tools |
|------|----------|------------|-------|
| **Architect** | Design decisions, API contracts, data models | `Plan` | Read, Glob, Grep |
| **Implementer** | Writing production code, editing files | `general-purpose` | All |
| **Explorer** | Codebase research, finding patterns, understanding code | `Explore` | Read, Glob, Grep |
| **Test Engineer** | Writing tests, running test suites, coverage | `general-purpose` | All |
| **DevOps** | Docker, CI/CD, scripts, infrastructure | `Bash` | Bash |
| **Reviewer** | Code review, quality checks, security audit | `general-purpose` | Read, Glob, Grep |

For each workstream, ask:

```
AskUserQuestion:
  question: "What role should own the '<Workstream Name>' workstream?"
  header: "<Workstream>"
  multiSelect: false
  options:
    - label: "<Most appropriate role> (Recommended)"
      description: "<Why this role fits>"
    - label: "<Alternative role 1>"
      description: "<Why this could also work>"
    - label: "<Alternative role 2>"
      description: "<Different tradeoff>"
```

#### Question 4: Risk Mitigation

If complexity signals were detected, ask about risk handling:

```
AskUserQuestion:
  question: "How should we handle risks for this task?"
  header: "Risks"
  multiSelect: true
  options:
    - label: "Add Reviewer agent"
      description: "Dedicated agent reviews all changes before they're finalized"
    - label: "TDD approach"
      description: "Test Engineer writes tests first, Implementer makes them pass"
    - label: "Incremental commits"
      description: "Each workstream commits in small verified steps"
    - label: "Dry run first"
      description: "Explorer agent validates the plan against codebase before implementation"
```

### Phase 4.5: Checkpoint — Confirm Before Plan Generation

Before investing effort in detailed agent briefs, do a quick checkpoint with the user. Summarize the decisions made so far:

```
AskUserQuestion:
  question: "Here's the plan so far. Ready to generate detailed agent briefs, or need changes?"
  header: "Checkpoint"
  multiSelect: false
  options:
    - label: "Looks good — generate briefs"
      description: "<Summary: N workstreams, execution strategy, roles assigned, risk mitigations>"
    - label: "Change workstreams"
      description: "Go back to Phase 4 Q1 — I want to adjust which workstreams are included"
    - label: "Change roles"
      description: "Go back to Phase 4 Q3 — I want to reassign agent roles"
    - label: "Start over"
      description: "Re-analyze from Phase 1 — my requirements have changed"
```

### Phase 5: Generate Team Plan

After all questions are answered, produce a structured team plan using `TaskCreate` for each workstream. Output the plan in this format:

---

## Team Plan: `<Task Summary>`

### Mission
`<1-2 sentence goal derived from $ARGUMENTS>`

### Team Composition

| # | Workstream | Role | Agent Type | Dependencies | Key Files |
|---|------------|------|------------|--------------|-----------|
| 1 | `<name>` | `<role>` | `<subagent_type>` | None | `<files>` |
| 2 | `<name>` | `<role>` | `<subagent_type>` | Workstream 1 | `<files>` |
| ... | | | | | |

### Execution Order

Based on the chosen strategy, show the execution graph:

```
[Parallel]
  +-- Agent 1: <Workstream> (<Role>)
  +-- Agent 2: <Workstream> (<Role>)
  +-- Agent 3: <Workstream> (<Role>)

[Then Sequential]
  +-- Agent 4: <Workstream> (<Role>) — depends on 1, 2
```

### Agent Briefs

For each agent, provide a detailed brief:

**Agent <N>: <Role> — <Workstream>**
- **Objective**: What this agent must accomplish
- **Inputs**: What context/files it needs
- **Outputs**: What it produces
- **Constraints**: Rules it must follow (from CLAUDE.md design principles)
- **Done when**: Clear completion criteria
- **Output Contract**: Every agent brief MUST end with the following instruction:

> **REQUIRED**: When your work is complete, emit the following fenced block as the LAST thing in your output. This is mandatory — the orchestrator parses it to coordinate downstream agents.
>
> ````
> ```AGENT_REPORT
> status: COMPLETED | PARTIAL | FAILED
> files_modified:
>   - /absolute/path/to/file1.py
>   - /absolute/path/to/file2.sh
> key_decisions:
>   - "Chose X over Y because Z"
>   - "Split module into two files for SRP"
> output_summary: >
>   2-5 sentence summary of what was accomplished, what changed,
>   and anything downstream agents need to know.
> blockers: none | "Description of unresolved issue"
> tests_run: 0
> tests_passed: 0
> ```
> ````

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `<risk>` | Low/Med/High | Low/Med/High | `<chosen mitigation>` |

### Model Consensus Summary

| Model | Key Feedback |
|-------|--------------|
| [Model 1] | [Summary of workstream feedback] |
| [Model 2] | [Summary of workstream feedback] |

**Agreement Areas**: [Where models agreed on the plan]
**Divergent Views**: [Where models differed — presented to user for decision]
**Plan Adjustments Made**: [Changes incorporated from consensus feedback]

---

### Phase 6: Offer Execution

After presenting the plan, ask the user whether to proceed:

```
AskUserQuestion:
  question: "Team plan is ready. How should we proceed?"
  header: "Execute"
  multiSelect: false
  options:
    - label: "Launch supervised (Recommended)"
      description: "Wave-based execution with handoff documents between waves, conflict detection, and final reconciliation"
    - label: "Launch fire-and-forget"
      description: "Spawn all agents immediately with no inter-agent coordination (legacy behavior)"
    - label: "Export plan only"
      description: "Save the plan to a file without executing"
    - label: "Revise plan"
      description: "Go back and adjust workstreams or roles"
```

**If "Launch fire-and-forget"**: Use the `Task` tool to spawn each agent with its brief as the prompt, using the assigned `subagent_type`. Launch all agents immediately per the execution strategy chosen in Phase 4. Skip Phases 7-10.

**If "Launch supervised"**: Proceed to Phase 7 (Wave Planning).

### Phase 7: Wave Planning

Group agents into execution waves based on the Dependencies column from the Team Composition table.

#### 7a. Build Wave Schedule

- **Wave 1**: All agents with `Dependencies: None`
- **Wave 2**: Agents that depend only on Wave 1 agents
- **Wave 3**: Agents that depend on Wave 1 or Wave 2 agents
- **Wave 4**: Everything else (maximum 4 waves — if more are needed, flatten by merging the deepest waves)

Present the wave schedule:

```
Wave 1 (parallel): Agent 1 (<Workstream>), Agent 3 (<Workstream>)
Wave 2 (parallel): Agent 2 (<Workstream>) — needs Agent 1
Wave 3 (sequential): Agent 4 (<Workstream>) — needs Agent 2, Agent 3
```

#### 7b. Confirm Wave Schedule

Present the wave schedule and ask the user to approve before execution begins:

```
AskUserQuestion:
  question: "Here's the proposed execution order. Does this look right?"
  header: "Waves"
  multiSelect: false
  options:
    - label: "Approve wave schedule"
      description: "<Wave 1: Agents X, Y (parallel) -> Wave 2: Agent Z (depends on X) -> ...>"
    - label: "Reorder waves"
      description: "I want to change which agents run first"
    - label: "Flatten to one wave"
      description: "Run everything in parallel — I accept the dependency risks"
```

#### 7c. File Overlap Detection

Build a file-overlap map from each agent brief's Key Files column. For each wave, check if any two parallel agents target the same files.

If overlap is found:

```
AskUserQuestion:
  question: "Agents <X> and <Y> in Wave <N> both target <file>. How should we handle this?"
  header: "Overlap"
  multiSelect: false
  options:
    - label: "Sequence them (Recommended)"
      description: "Run Agent <X> first, then Agent <Y> with updated file context"
    - label: "Keep parallel"
      description: "Accept the risk — agents work on different parts of the file"
    - label: "Merge into one agent"
      description: "Combine both workstreams into a single agent"
```

Adjust wave assignments based on user decisions.

If NO overlap is found, still briefly confirm: "No file overlaps detected across parallel agents in any wave. Proceeding to execution."

### Phase 8: Supervised Execution Loop

Repeat the following steps for each wave in order. The orchestrator (you) stays active throughout — do NOT yield control.

#### 8.1 Launch Wave

For each agent in the current wave, compose the full prompt:

1. **Agent Brief** from Phase 5
2. **Handoff Document** from prior wave (if Wave 2+) — include only summaries from agents this agent declared as dependencies
3. **Output Contract** reminder — restate the `AGENT_REPORT` format requirement

Launch each agent with `Task(run_in_background: true)` and record the returned task IDs.

```
Task:
  subagent_type: <from Team Composition>
  description: "<Workstream name> (Wave <N>)"
  prompt: <Agent Brief + Handoff Document + Output Contract>
  run_in_background: true
```

#### 8.2 Wait for Completion

For each agent in the wave, call `TaskOutput(task_id, block: true, timeout: 600000)` (10-minute timeout).

**Parse the `AGENT_REPORT`**: Extract the fenced `AGENT_REPORT` block from the agent's output. Parse the YAML-like fields into structured data.

**If `AGENT_REPORT` is missing**: Synthesize a minimal report from the raw output:
- `status`: PARTIAL (assume incomplete if no report)
- `files_modified`: scan output for file paths mentioned in Edit/Write tool calls
- `key_decisions`: "No structured report provided"
- `output_summary`: first 500 characters of agent output
- `blockers`: "Agent did not emit structured report"

**If timeout**: Ask the user:

```
AskUserQuestion:
  question: "Agent <N> (<Workstream>) has not completed after 10 minutes. What should we do?"
  header: "Timeout"
  multiSelect: false
  options:
    - label: "Keep waiting"
      description: "Wait another 10 minutes for the agent to finish"
    - label: "Skip this agent"
      description: "Continue without this agent's output — mark as FAILED"
    - label: "Abort execution"
      description: "Stop all execution and report current state"
```

#### 8.3 Conflict Detection

After all agents in the wave complete, collect `files_modified` from every agent's `AGENT_REPORT`.

**If multiple agents modified the same file:**
1. Run `git diff` on the conflicting file to inspect changes
2. Use `Read` to examine the current file state
3. If changes are compatible (different sections of the file): note in the handoff document and proceed
4. If actual conflicts exist: attempt resolution via `Edit`, or ask the user:

```
AskUserQuestion:
  question: "Agents <X> and <Y> made conflicting changes to <file>. How should we resolve?"
  header: "Conflict"
  multiSelect: false
  options:
    - label: "Keep Agent <X>'s version"
      description: "<summary of X's changes>"
    - label: "Keep Agent <Y>'s version"
      description: "<summary of Y's changes>"
    - label: "Manual merge"
      description: "I'll resolve this myself — pause execution"
```

#### 8.4 Synthesize Handoff Document

Produce a markdown handoff document (under 2000 tokens) summarizing the wave's results. Structure:

```markdown
## Wave <N> Handoff

### Agent <X>: <Workstream> — <STATUS>
**Files modified**: <list>
**Key decisions**: <list>
**Summary**: <output_summary from AGENT_REPORT>
**Blockers**: <blockers or "none">

### Agent <Y>: <Workstream> — <STATUS>
...
```

**Selective injection**: When composing prompts for the next wave, include ONLY the handoff sections for agents that the downstream agent declared as dependencies. Do not dump the entire handoff document into every agent's prompt.

#### 8.5 Inter-Wave Check-In

After each wave completes (except the final wave), present a progress summary and ask the user how to proceed:

```
AskUserQuestion:
  question: "Wave <N> complete. <X/Y agents succeeded>. Review the handoff summary above — ready to launch Wave <N+1>?"
  header: "Progress"
  multiSelect: false
  options:
    - label: "Continue to Wave <N+1>"
      description: "Launch next wave with the handoff document as-is"
    - label: "Adjust next wave"
      description: "I want to modify the next wave's agents or briefs before launching"
    - label: "Pause and review"
      description: "Let me inspect the changes so far before continuing"
    - label: "Abort remaining waves"
      description: "Stop here — what's done is enough for now"
```

**If "Adjust next wave"**: Ask follow-up questions about what to change (add/remove agents, modify briefs, change dependencies), then recompose the wave.

**If "Pause and review"**: Wait for the user to inspect files and return. Do not proceed until they explicitly say to continue.

**If "Abort remaining waves"**: Skip directly to Phase 9 (Final Reconciliation) with only the completed waves.

#### 8.6 Advance

- Update `TaskUpdate` for completed workstream tasks (mark `completed` or note partial/failed status)
- Log wave completion: which agents succeeded, which failed, total files modified
- Proceed to the next wave (if user approved in 8.5)

### Phase 9: Final Reconciliation

After all waves complete, perform a reconciliation check:

#### 9.1 File System Consistency

Run `git status` to verify the working tree state. Confirm all expected files were modified and no unexpected changes exist.

#### 9.2 Lint and Format

Run `cd ods && make lint` to verify shell syntax and Python compile checks. For dashboard changes, also run `cd ods/extensions/services/dashboard && npm run lint`.

If issues are found, fix them.

#### 9.3 Test Suite

If any agent reported running tests (`tests_run > 0`), run the relevant test suites to verify the full suite passes:

- **Shell tests**: `cd ods && make test && make bats`
- **Python tests**: `cd ods/extensions/services/dashboard-api && pytest tests/ -v`

Report results.

#### 9.4 Cross-Agent Consistency

For files modified by multiple agents across different waves:
1. `Read` each file and check for:
   - Import conflicts (duplicate imports, missing imports)
   - Interface mismatches (function signatures changed by one agent but called by another's code)
   - Missing connections (new functions/classes not wired into the system)
2. Fix any issues found, or report them to the user

Present reconciliation results:

| Check | Status | Details |
|-------|--------|---------|
| File system | PASS/FAIL | `<git status summary>` |
| Lint (shell + python) | PASS/FAIL | `<make lint output summary>` |
| Tests (shell) | PASS/FAIL/SKIPPED | `<make test + make bats summary>` |
| Tests (python) | PASS/FAIL/SKIPPED | `<pytest summary>` |
| Cross-agent consistency | PASS/FAIL | `<issues found>` |

If any check fails, offer:

```
AskUserQuestion:
  question: "Reconciliation found issues. How should we proceed?"
  header: "Fix"
  multiSelect: false
  options:
    - label: "Auto-fix (Recommended)"
      description: "Attempt to resolve lint, format, and simple consistency issues automatically"
    - label: "Spawn fix agent"
      description: "Launch a dedicated agent to resolve the issues"
    - label: "Manual resolution"
      description: "I'll fix these myself"
```

### Phase 10: Execution Summary

Present the final report:

#### Agent Results

| # | Workstream | Role | Wave | Status | Files Modified | Tests |
|---|------------|------|------|--------|----------------|-------|
| 1 | `<name>` | `<role>` | 1 | COMPLETED | 3 | 5/5 |
| 2 | `<name>` | `<role>` | 1 | COMPLETED | 2 | 3/3 |
| 3 | `<name>` | `<role>` | 2 | PARTIAL | 1 | 0/0 |

#### Key Decisions Made

Aggregate `key_decisions` from all agent reports into a numbered list.

#### Conflicts Resolved

List any file conflicts detected in Phase 8.3 and how they were resolved.

#### Reconciliation Results

Summary from Phase 9 — pass/fail for each check.

#### All Modified Files

Deduplicated list of every file modified across all agents and waves.

#### Next Steps

Based on agent blockers and reconciliation results, suggest concrete next actions (e.g., "Agent 3 reported a blocker on X — manual review needed").

#### 10.1 Post-Execution Follow-Up

Always ask the user what they want to do next:

```
AskUserQuestion:
  question: "Execution complete. What would you like to do next?"
  header: "Next"
  multiSelect: true
  options:
    - label: "Run make gate"
      description: "Run the full validation suite (lint + test + bats + smoke + simulate)"
    - label: "Create PR"
      description: "Commit changes and open a pull request"
    - label: "Spawn fix agent"
      description: "Address blockers or partial completions from the execution"
    - label: "Done for now"
      description: "I'll review the changes manually"
```

## Error Handling (Supervised Mode)

Error handling follows the **Let It Crash** principle — failures are visible, not hidden. No silent swallowing, no complex retry chains.

### Agent Crash (no output returned)

Ask the user:
```
AskUserQuestion:
  question: "Agent <N> (<Workstream>) crashed with no output. What should we do?"
  header: "Crash"
  multiSelect: false
  options:
    - label: "Retry once"
      description: "Relaunch the agent with the same brief (max 1 retry per agent)"
    - label: "Skip agent"
      description: "Continue without this agent — mark as FAILED in handoff"
    - label: "Abort execution"
      description: "Stop all execution and report current state"
```

**Max 1 retry per agent.** If the retry also fails, mark as FAILED and proceed.

### PARTIAL Status

Include partial results in the handoff document with clear gaps noted:

```markdown
### Agent <N>: <Workstream> — PARTIAL
**Completed**: <what was done>
**Incomplete**: <what was NOT done>
**Blockers**: <from AGENT_REPORT>
```

Downstream agents receiving this handoff should be warned that their dependency produced partial output.

### FAILED Status

If an agent with downstream dependents fails:
- Inject a `FAILED_DEPENDENCY` warning into each dependent agent's prompt:

```
!! FAILED DEPENDENCY: Agent <N> (<Workstream>) failed. Its expected outputs
are NOT available. You must either:
1. Work without this dependency (if possible)
2. Emit status: PARTIAL in your AGENT_REPORT explaining what you couldn't do
```

### All Agents in a Wave Fail

If every agent in a wave fails or crashes, abort execution immediately. Present the Execution Summary (Phase 10) with what was accomplished in prior waves.

## Constraints

- Maximum 5 workstreams per plan (keep it focused)
- Maximum 4 execution waves (flatten deeper dependency chains)
- Maximum 1 retry per crashed agent (Let It Crash — don't loop)
- Handoff documents must stay under 2000 tokens (concise summaries, not full output)
- Each workstream must have exactly one owning role
- Agent briefs must reference specific files discovered during Phase 1 exploration
- All plans must respect CLAUDE.md design principles (Let It Crash, KISS, Pure Functions, SOLID)
- Do not propose workstreams for work that doesn't exist (e.g., no "Docs" workstream if no docs need updating)
- Orchestrator stays active throughout supervised execution — do not yield control between waves
- Agents must report `files_modified` with absolute paths in their `AGENT_REPORT`

## Example Usage

```
/team-plan Add a new Whisper voice transcription extension to the stack
/team-plan Refactor GPU detection to support multi-GPU systems
/team-plan Fix the health check timeout bug in dashboard-api
/team-plan Add rate limiting to the dashboard API endpoints
```

## Notes

- The quality of the plan depends on thorough codebase exploration in Phase 1
- Adapt the number of questions based on task complexity — simple tasks need fewer questions
- For trivial tasks (single file, single concern), skip team planning and suggest direct implementation instead
- Agent briefs should be self-contained — each agent should be able to work without additional context from other agents
- Requires PAL MCP server configured with at least one model provider for consensus
- Models are discovered dynamically via `mcp__pal__listmodels`
- If PAL MCP is unavailable, consensus is skipped gracefully — the plan proceeds with your own analysis only
