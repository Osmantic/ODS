# Writing Effective Baselines

A baseline is the persistent identity of your agent. It's everything above the `---` separator in MEMORY.md — the part that survives every reset cycle. This guide covers how to write baselines that keep agents focused, capable, and aligned.

## What a Baseline Is

A baseline is the answer to: "If this agent lost all memory of what it's been doing, what does it need to know to continue operating correctly?"

It is NOT a task list. It's not a conversation history. It's the agent's constitution — its identity, rules, capabilities, and pointers to where everything lives.

## What Makes a Good Baseline

### 1. Identity First

Start with who the agent is. This anchors everything else.

```markdown
## Who I Am
I am CodeReviewer, an automated code review agent. I review pull requests
on the main repository, flag issues, suggest improvements, and approve
changes that meet quality standards. I report to the engineering lead.
```

Be specific. "I am a helpful assistant" is useless. "I review PRs on the acme-corp/backend repo, focusing on security and performance" gives the agent something to work with.

### 2. Rules That Actually Matter

Don't write 50 rules. Write 5-7 that matter enough to never violate. These are your hard boundaries.

Good rules are specific and actionable:
- "Never push directly to the main branch"
- "Never modify another agent's MEMORY.md"
- "Always run tests before committing"

Bad rules are vague:
- "Be careful" (with what?)
- "Don't do anything dangerous" (define dangerous)
- "Follow best practices" (which ones?)

### 3. Autonomy Tiers

The most effective pattern we've found is explicit autonomy tiers. Agents need to know what they can do freely, what needs a heads-up, and what needs approval.

```markdown
## Autonomy Tiers

**Do freely:** Read files, run tests, draft PRs, update scratch notes
**Do then notify:** Merge approved PRs, update documentation
**Ask first:** Change CI/CD config, modify shared infrastructure
**Never do:** Delete branches, modify production databases, bypass review
```

This eliminates the "should I ask or just do it?" hesitation that wastes cycles.

For a deeper dive into autonomy tiers and infrastructure protection, see
the Guardian service configuration in `docker-compose.base.yml`.

### 4. Capabilities and Tools

Tell the agent what it can actually use. Agents that know their tools are dramatically more effective than ones guessing.

```markdown
## My Capabilities
**Model:** Claude Sonnet 4.5 via API
**Tools:** Bash, file I/O, web search, GitHub CLI
**Can access:** Internal wiki, CI logs, monitoring dashboard
**Cannot access:** Production database, customer data, billing system
```

### 5. Pointers, Not Content

A baseline should point to information, not contain it. Don't paste your entire project architecture into MEMORY.md — point to where the docs live.

```markdown
## Where to Find Things
| What | Where |
|------|-------|
| Architecture docs | /docs/ARCHITECTURE.md |
| API reference | /docs/API.md |
| Deployment guide | /ops/DEPLOY.md |
| Team contacts | /docs/TEAM.md |
```

This keeps baselines small and avoids stale copies of information that lives elsewhere.

## What NOT to Put in a Baseline

- **Current tasks or status** — That's what scratch notes are for
- **Conversation context** — Each session starts fresh; the baseline provides enough to start working
- **Frequently changing data** — API endpoints that rotate, version numbers, deployment targets. Point to a config file instead.
- **Long reference material** — Don't paste a 50-line API reference. Link to it.
- **Other agents' details** — A brief team table is fine, but don't include their full capabilities or instructions

## The Scratch Notes Contract

The `---` separator is a contract between you (the operator) and the agent:

- **Above the line:** The operator controls this. The agent must not modify it. It defines who the agent is and how it operates.
- **Below the line:** The agent controls this. It's scratch space for working notes, observations, and state that helps during the current work cycle.

Include this contract in the baseline itself so the agent understands the system:

```markdown
## How to Persist Knowledge
- **Short-term:** Write below the `---` line. These notes get archived on reset.
- **Medium-term:** Save files in your workspace directory.
- **Long-term:** Commit to the project repository.
```

Agents that understand the reset system write better notes — they prioritize what matters and move important discoveries to permanent storage before the next cycle.

## Size Guidelines

From our experience running multi-agent systems:

- **Too small (< 5KB):** Not enough context. The agent spends cycles rediscovering things.
- **Sweet spot (12-20KB):** Enough to fully specify identity, rules, capabilities, and pointers.
- **Too large (> 25KB):** You're probably including content that should be in separate docs. The baseline becomes hard to maintain and review.

The minimum safety threshold in memory-shepherd is 1000 bytes — anything smaller than that is almost certainly corrupt or empty, and the reset will abort rather than overwrite a working memory file with garbage.

## Section Ordering

We recommend this order, which flows from "who am I" to "how do I work":

1. **Who I Am** — Identity and role
2. **The Team** — Who else is involved
3. **Critical Rules** — Hard boundaries
4. **Work Habits** — Standing orders and norms
5. **Autonomy Tiers** — What needs approval vs. what doesn't
6. **My Capabilities** — Tools, models, access
7. **Where to Find Things** — Pointers to persistent information
8. **How to Persist Knowledge** — The memory system explanation

The exact sections don't matter as much as having a consistent structure that the agent encounters the same way every reset cycle. Consistency breeds reliability.

## Teaching Agents About the System

The most important trick: include an explanation of the memory system in the baseline itself. Agents that know their memory gets reset behave differently — and better — than agents that don't.

```markdown
*This is your baseline memory. You can add notes below the --- line.
Your additions will be periodically archived and this file reset to baseline.
For anything worth keeping long-term, write it to your project repo.*
```

This one paragraph, placed at the top of every baseline, completely changes agent behavior. Instead of treating MEMORY.md as a permanent document, they treat scratch notes as what they are: temporary working memory that needs to be distilled and externalized.

## Reviewing and Updating Baselines

Baselines aren't write-once. Review them when:

- The agent's role changes
- You notice the agent repeatedly rediscovering the same information (add it to the baseline)
- You notice the agent consistently ignoring a rule (simplify or remove it — unenforced rules add noise)
- The team structure changes
- New tools or capabilities are added

When updating a baseline, update the file in your `baselines/` directory. The next reset cycle will automatically propagate the change.
