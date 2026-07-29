---
description: Deep research a topic using web search (Rube MCP) and multi-model consensus analysis (PAL MCP)
allowed-tools: AskUserQuestion, Bash, Read, Glob, Grep, Write, WebFetch, mcp__rube__RUBE_SEARCH_TOOLS, mcp__rube__RUBE_MULTI_EXECUTE_TOOL, mcp__rube__RUBE_MANAGE_CONNECTIONS, mcp__rube__RUBE_GET_TOOL_SCHEMAS, mcp__rube__RUBE_REMOTE_BASH_TOOL, mcp__pal__consensus, mcp__pal__thinkdeep, mcp__pal__chat, mcp__pal__challenge, mcp__pal__listmodels
argument-hint: <research topic or question> [--depth shallow|medium|deep] [--output <filepath>] [--models N]
---

# Deep Research with Multi-Model Consensus

Conduct comprehensive research on any topic by combining real-time web search (via Rube/Composio MCP) with multi-model deep analysis and consensus synthesis (via PAL MCP). Produces a structured research report with sourced findings, cross-validated analysis, and confidence assessments.

## Arguments

- `$ARGUMENTS` - Research configuration:
  - First positional argument: The research topic, question, or area of investigation
  - `--depth <level>` - Research depth: `shallow` (quick overview, 2-3 searches), `medium` (balanced, 5-7 searches, default), `deep` (exhaustive, 10+ searches with follow-up queries)
  - `--output <filepath>` - Save the final report to a file (default: print to console)
  - `--models <N>` - Number of models for consensus analysis (default: 3, min: 2, max: 5)

## Philosophy: Ask Early, Ask Often

**This skill should liberally use `AskUserQuestion` at every decision point.** Research is inherently exploratory — assumptions about what the user wants are frequently wrong. The cost of asking is low; the cost of researching the wrong angle is high. Specifically:

- **Before** searching — confirm the research plan and sub-questions
- **When** the topic is ambiguous — clarify intent, scope, and angle
- **After** initial searches — share what was found and ask about direction
- **When** gaps are identified — let the user prioritize which gaps matter
- **When** contradictions surface — present both sides and ask for guidance
- **When** models disagree — let the user break the tie
- **Before** finalizing — confirm the report meets the user's needs
- **After** delivery — ask about follow-up research

The user should feel like a research partner steering the investigation, not a passive recipient of a pre-baked report.

## Workflow

### Phase 1: Parse Arguments and Plan Research

Extract the research topic from `$ARGUMENTS`. Parse optional flags:
- Default: `depth=medium`, `models=3`, no file output
- Identify the core question and decompose it into 3-7 sub-questions that, when answered together, provide comprehensive coverage.

**Sub-question decomposition strategy:**

| Depth | Sub-questions | Searches per sub-question | Follow-ups |
|-------|--------------|---------------------------|------------|
| `shallow` | 2-3 | 1 | 0 |
| `medium` | 3-5 | 1-2 | 1 per gap |
| `deep` | 5-7 | 2-3 | 2-3 per gap |

#### 1a. Clarify Ambiguous Topics

If `$ARGUMENTS` contains vague terms, multiple possible interpretations, or implicit assumptions, ask immediately before decomposing:

```
AskUserQuestion:
  question: "<Specific clarifying question about the research topic>"
  header: "Clarify"
  multiSelect: false
  options:
    - label: "<Interpretation A>"
      description: "<What this would mean for the research direction>"
    - label: "<Interpretation B>"
      description: "<What this would mean for the research direction>"
    - label: "<Interpretation C>"
      description: "<What this would mean for the research direction>"
```

Repeat for each distinct ambiguity. Common ambiguities to check for:
- **Scope**: Does the user want a broad overview or a narrow deep-dive?
- **Recency**: Current state of affairs, historical analysis, or both?
- **Audience**: Technical depth appropriate for experts or general audience?
- **Angle**: Neutral survey, pro/con analysis, or advocacy for a position?

#### 1b. Validate Research Plan with User

After decomposing sub-questions, **always** present the plan and ask for confirmation:

```
AskUserQuestion:
  question: "Here's my research plan. Should I proceed, or adjust the sub-questions?"
  header: "Plan"
  multiSelect: false
  options:
    - label: "Looks good — proceed"
      description: "<N sub-questions, ~M estimated searches, depth=level>"
    - label: "Adjust sub-questions"
      description: "I want to add, remove, or rephrase some of the sub-questions"
    - label: "Change depth"
      description: "I want a different depth level than <current level>"
    - label: "Different angle"
      description: "The sub-questions are missing the angle I care about"
```

**If "Adjust sub-questions"**: Ask a follow-up about what to change, then revise.

**If "Different angle"**: Ask what angle they want, revise sub-questions accordingly.

### Phase 2: Discover Search Tools

Use `mcp__rube__RUBE_SEARCH_TOOLS` to find web search and research tools. Start a new session.

```
RUBE_SEARCH_TOOLS:
  session: { generate_id: true }
  model: "claude-opus-4-6"
  queries:
    - use_case: "search the web for information about a topic"
    - use_case: "deep research and comprehensive web search"
    - use_case: "scrape and extract content from a web page URL"
```

From the response:
1. **Record the session_id** - reuse for ALL subsequent Rube calls
2. **Check connection status** for returned toolkits
3. If no active connection exists, call `RUBE_MANAGE_CONNECTIONS` with the required toolkit names and present the auth link to the user
4. **Identify the best tools** for:
   - Web search (e.g., `EXA_SEARCH`, `SERPAPI_SEARCH`, `TAVILY_SEARCH`, or similar)
   - URL content extraction (e.g., `FIRECRAWL_SCRAPE`, `EXA_GET_CONTENTS`, or similar)
   - Deep research if available (e.g., `TAVILY_EXTRACT`, or similar)

If `RUBE_SEARCH_TOOLS` returns tools with `schemaRef` instead of full `input_schema`, call `RUBE_GET_TOOL_SCHEMAS` to load the complete schemas before execution.

### Phase 3: Execute Web Research

For each sub-question, execute searches using the discovered tools via `RUBE_MULTI_EXECUTE_TOOL`.

**Batch independent searches in parallel** (up to 5 per call):

```
RUBE_MULTI_EXECUTE_TOOL:
  session_id: <from Phase 2>
  thought: "Searching for information on sub-questions 1-3"
  current_step: "WEB_SEARCH"
  current_step_metric: "0/N queries"
  sync_response_to_workbench: true  # Large responses expected
  memory: {}
  tools:
    - tool_slug: "<search_tool>"
      arguments:
        query: "<sub-question 1 rephrased as search query>"
    - tool_slug: "<search_tool>"
      arguments:
        query: "<sub-question 2 rephrased as search query>"
    ...
```

**Search query formulation rules:**
- Rephrase sub-questions as effective search queries (remove question words, add context terms)
- Use specific, factual language
- For controversial topics, search for multiple perspectives explicitly
- Include date qualifiers if recency matters (e.g., "2024", "latest")

**After initial searches:**
1. Parse and collect all search results
2. Identify the most relevant URLs from search results
3. For `medium` and `deep` depth: extract full content from the top 3-5 URLs using the URL extraction tool
4. Identify information gaps — sub-questions that weren't well-answered

#### 3a. Share Preliminary Findings and Redirect

After collecting initial search results, present a brief summary and ask the user if the direction looks right. This prevents wasting searches on the wrong angle:

```
AskUserQuestion:
  question: "Here's what I've found so far. Is this the right direction, or should I adjust?"
  header: "Direction"
  multiSelect: false
  options:
    - label: "Good direction — keep going"
      description: "<2-3 sentence summary of what the initial searches revealed>"
    - label: "Shift focus"
      description: "The findings are interesting but I'd rather explore a different angle"
    - label: "Go deeper on one area"
      description: "I want to focus specifically on <the most promising sub-question>"
    - label: "Broader search"
      description: "The results are too narrow — expand the search terms"
```

**If "Shift focus"**: Ask what angle they prefer, reformulate remaining queries.
**If "Go deeper on one area"**: Concentrate remaining search budget on that sub-question.

#### 3b. Prioritize Gaps with User

After identifying information gaps, present them and ask which ones matter most (especially for `medium` and `deep` depth where follow-up searches are planned):

```
AskUserQuestion:
  question: "I found gaps in the following areas. Which are most important to fill?"
  header: "Gaps"
  multiSelect: true
  options:
    - label: "<Gap 1>"
      description: "<What's missing and why it might matter>"
    - label: "<Gap 2>"
      description: "<What's missing and why it might matter>"
    - label: "<Gap 3>"
      description: "<What's missing and why it might matter>"
    - label: "None — move on"
      description: "The current findings are sufficient, skip follow-up searches"
```

Allocate follow-up searches to user-selected gaps only. Skip this question for `shallow` depth.

**For `medium` and `deep` depth — follow-up searches:**
- Generate refined queries targeting user-prioritized gaps
- Execute follow-up searches
- Extract additional URL content as needed

#### 3c. Flag Surprising or Contradictory Findings

If search results contain surprising information, major contradictions between sources, or findings that challenge the initial assumptions, surface them immediately:

```
AskUserQuestion:
  question: "I found something unexpected: <brief description>. How should this affect the research?"
  header: "Discovery"
  multiSelect: false
  options:
    - label: "Investigate further"
      description: "This is important — dedicate follow-up searches to understanding it"
    - label: "Note it and continue"
      description: "Interesting but don't let it derail the main research direction"
    - label: "Discard"
      description: "Not relevant to what I'm looking for"
```

Skip this question if no surprises were found.

**Organize raw findings** into a structured intermediate format:

```
Sub-question 1: <question>
  Sources:
    - [Source Title](URL) — Key finding: <summary>
    - [Source Title](URL) — Key finding: <summary>
  Gaps: <what's still unclear>

Sub-question 2: <question>
  Sources:
    - ...
```

### Phase 4: Deep Analysis with ThinkDeep

Use `mcp__pal__thinkdeep` to perform systematic analysis of the gathered research. This validates findings, identifies contradictions, and builds a coherent narrative.

```
mcp__pal__thinkdeep:
  step: |
    Analyze the following research findings on the topic: "<research topic>"

    Raw findings:
    <organized findings from Phase 3>

    Tasks:
    1. Identify key themes and patterns across all sources
    2. Flag any contradictions or conflicting information between sources
    3. Assess source credibility and potential biases
    4. Identify claims that are well-supported vs. poorly-supported
    5. Note any significant gaps in the available information
    6. Synthesize a preliminary narrative that addresses the original research question
  step_number: 1
  total_steps: 2
  next_step_required: true
  findings: "Initial analysis of <N> sources across <M> sub-questions"
  model: "gpt-5.2"
  thinking_mode: "high"
  focus_areas: ["accuracy", "completeness", "bias", "contradictions"]
  hypothesis: "Preliminary synthesis of research findings on <topic>"
  problem_context: "Deep research analysis requiring source validation and synthesis"
```

Continue with step 2 to refine the analysis:

```
mcp__pal__thinkdeep:
  step: |
    Based on the initial analysis, refine the synthesis:
    1. Resolve identified contradictions with evidence-based reasoning
    2. Rank findings by confidence level (high/medium/low)
    3. Produce a structured outline for the final research report
    4. Identify the 3-5 most important takeaways
  step_number: 2
  total_steps: 2
  next_step_required: false
  findings: "<findings from step 1>"
  model: "gpt-5.2"
  thinking_mode: "max"
  confidence: "medium"
```

#### 4a. Present Analysis Summary and Check Direction

After ThinkDeep analysis, share the key themes and conclusions with the user before moving to multi-model consensus. This is the last checkpoint before the most expensive phase:

```
AskUserQuestion:
  question: "Here's my analysis of the research. Does this capture what you're looking for?"
  header: "Analysis"
  multiSelect: false
  options:
    - label: "Looks right — proceed to consensus"
      description: "<2-3 sentence summary of key conclusions and themes>"
    - label: "Missing an important angle"
      description: "The analysis doesn't address <aspect the user cares about>"
    - label: "Too broad — narrow the focus"
      description: "Focus the final report on the most relevant subset of findings"
    - label: "Skip consensus — report is good enough"
      description: "Don't need multi-model validation — use the current analysis as-is"
```

**If "Missing an important angle"**: Ask what angle is missing, run additional targeted searches if needed, then re-run ThinkDeep step 2 with the new context.

**If "Skip consensus"**: Jump directly to Phase 6 (Generate Research Report) using the ThinkDeep analysis as the final synthesis.

### Phase 5: Multi-Model Consensus Validation

Use `mcp__pal__consensus` to cross-validate the analysis with multiple models. This ensures findings are robust and not biased by any single model's training data.

#### 5a. Discover Available Models

Call `mcp__pal__listmodels` to get available models. Select top N models (from `--models` flag, default 3) by score, **preferring different providers** for maximum diversity.

**Model Selection Criteria:**
1. Sort by score descending
2. Pick from distinct providers (e.g., OpenAI, Google, xAI)
3. Prefer models with high context windows for handling research data
4. Minimum 2 models, maximum 5

#### 5b. Run Consensus

Use `mcp__pal__consensus` to get multi-model validation. The `total_steps` = number of models + 1 (your initial analysis).

**Step 1 — Your initial analysis (the proposal all models will evaluate):**

```
mcp__pal__consensus:
  step: |
    Evaluate the following research synthesis on "<research topic>":

    ## Research Question
    <original question/topic>

    ## Key Findings
    <structured findings from Phase 4>

    ## Preliminary Conclusions
    <conclusions from ThinkDeep analysis>

    ## Sources
    <list of sources with URLs>

    Please evaluate:
    1. Are the conclusions well-supported by the cited sources?
    2. Are there logical gaps or unsupported leaps in the reasoning?
    3. What important perspectives or counterarguments are missing?
    4. How would you rate the overall confidence level (1-10) of each conclusion?
    5. What additional context or nuance should be added?
  step_number: 1
  total_steps: <N_models + 1>
  next_step_required: true
  findings: "Initial synthesis based on <N> web sources and ThinkDeep analysis"
  models:
    - model: "<model_1>"
      stance: "for"
      stance_prompt: "Evaluate the research findings charitably, looking for strengths and well-supported conclusions."
    - model: "<model_2>"
      stance: "against"
      stance_prompt: "Critically evaluate the research findings, looking for weaknesses, gaps, unsupported claims, and missing perspectives."
    - model: "<model_3>"
      stance: "neutral"
      stance_prompt: "Provide a balanced evaluation of the research findings, weighing both strengths and weaknesses objectively."
```

**Steps 2-N — Process each model's response:**

For each model response:
```
mcp__pal__consensus:
  step: "<Summarize the model's key feedback, agreements, and disagreements>"
  step_number: <current>
  total_steps: <N_models + 1>
  next_step_required: <true if more models remain, false on last>
  findings: "<Key insights from this model's evaluation>"
```

#### 5c. Present Model Disagreements to User

If models disagree on any finding or conclusion, **do NOT resolve the disagreement yourself**. Present each significant disagreement to the user:

```
AskUserQuestion:
  question: "Models disagreed on <specific finding or conclusion>. Which perspective should the report emphasize?"
  header: "Tiebreak"
  multiSelect: false
  options:
    - label: "<Model A's position>"
      description: "<Model A> (<stance>) argues: <reasoning>"
    - label: "<Model B's position>"
      description: "<Model B> (<stance>) argues: <reasoning>"
    - label: "Present both sides"
      description: "Include both perspectives in the report without taking a position"
    - label: "Neither — I have my own view"
      description: "I'll provide my preferred framing"
```

Repeat for each significant disagreement (up to 4 per `AskUserQuestion` call). Skip for minor differences that don't affect conclusions.

#### 5d. Incorporate Consensus

After all models respond and user tiebreaks are resolved, synthesize:
- **Areas of agreement** — findings all models endorsed (high confidence)
- **Areas of disagreement** — findings where models differed, resolved per user preference
- **Missing perspectives** — gaps identified by any model
- **Confidence adjustments** — raise or lower confidence based on model feedback

### Phase 6: Generate Research Report

#### 6a. Confirm Report Format and Focus

Before generating the full report, ask the user how they want the output structured:

```
AskUserQuestion:
  question: "Ready to generate the report. Any preferences on format or focus?"
  header: "Report"
  multiSelect: false
  options:
    - label: "Full report (Recommended)"
      description: "Complete structured report with executive summary, findings, analysis, consensus, and sources"
    - label: "Executive summary only"
      description: "Concise 1-2 page summary of key findings and conclusions"
    - label: "Findings + sources only"
      description: "Skip analysis narrative — just give me the findings and their source citations"
    - label: "Custom focus"
      description: "I want the report to emphasize specific sections or findings"
```

**If "Custom focus"**: Ask what they want emphasized, then adjust the report structure accordingly.

Compile all phases into a structured research report.

**Report format:**

```markdown
# Deep Research Report: <Topic>

**Generated**: <date>
**Depth**: <shallow|medium|deep>
**Sources consulted**: <N>
**Models consulted**: <list of models>

---

## Executive Summary

<3-5 paragraph synthesis of the most important findings, written for a general audience>

---

## Research Question

<The original topic/question and how it was decomposed>

---

## Key Findings

### Finding 1: <Title>
**Confidence**: High | Medium | Low
**Consensus**: Agreed | Mixed | Disputed

<Detailed finding with inline source citations>

**Sources**: [Source 1](url), [Source 2](url)

### Finding 2: <Title>
...

---

## Analysis

### Themes and Patterns
<Cross-cutting themes identified across sources>

### Contradictions and Debates
<Where sources or models disagreed, with context for each position>

### Information Gaps
<What remains unclear or under-researched>

---

## Model Consensus

| Model | Stance | Confidence Rating | Key Feedback |
|-------|--------|-------------------|--------------|
| <model_1> | For | X/10 | <summary> |
| <model_2> | Against | X/10 | <summary> |
| <model_3> | Neutral | X/10 | <summary> |

**Agreement Areas**: <where all models agreed>
**Divergent Views**: <where models differed>

---

## Sources

| # | Title | URL | Relevance |
|---|-------|-----|-----------|
| 1 | <title> | <url> | <how it contributed> |
| 2 | <title> | <url> | <how it contributed> |
...

---

## Methodology

This report was generated using:
1. **Web search** via Composio/Rube MCP (<N> searches across <M> sub-questions)
2. **Deep analysis** via PAL MCP ThinkDeep (systematic hypothesis testing)
3. **Multi-model consensus** via PAL MCP Consensus (<N> models with for/against/neutral stances)

---

*Generated by Claude Code Deep Research with Rube MCP + PAL MCP*
```

### Phase 7: Output

If `--output <filepath>` was specified:
- Write the report to the specified file using the `Write` tool
- Confirm the file path to the user

Otherwise:
- Display the full report in the console

In both cases, end with a brief summary:
```
Research complete:
- Topic: <topic>
- Sources: <N> web sources consulted
- Models: <N> models reached consensus
- Confidence: <overall assessment>
- Key takeaway: <1-sentence summary>
```

#### 7a. Post-Delivery Follow-Up

Always ask the user what they want to do next:

```
AskUserQuestion:
  question: "Research report delivered. What would you like to do next?"
  header: "Next"
  multiSelect: false
  options:
    - label: "Done — looks great"
      description: "No further action needed"
    - label: "Dig deeper on a specific finding"
      description: "Run a focused follow-up research session on one area"
    - label: "Challenge the conclusions"
      description: "Use PAL challenge tool to stress-test the key claims"
    - label: "Save to a different format/location"
      description: "Export the report to a file or reformat it"
```

**If "Dig deeper on a specific finding"**: Ask which finding, then re-enter Phase 3 with narrowed sub-questions focused on that finding.

**If "Challenge the conclusions"**: Use `mcp__pal__challenge` to adversarially test the top 3 conclusions, then present the results to the user.

## Error Handling

| Scenario | Action |
|----------|--------|
| No search tools found | Fall back to `WebFetch` for direct URL fetching; warn user about limited search capability |
| Connection not active | Call `RUBE_MANAGE_CONNECTIONS` and present auth link; wait for user to connect |
| Search returns no results | Reformulate query with broader terms; if still empty, note the gap and continue |
| Tool schema missing | Call `RUBE_GET_TOOL_SCHEMAS` to load full schema before execution |
| PAL MCP unavailable | Skip consensus phase; produce report from web research + your own analysis only |
| ThinkDeep fails | Continue with raw findings; note that deep analysis was unavailable |
| Model not available | Fall back to next available model from `listmodels`; minimum 2 for consensus |
| Large response data | Use `sync_response_to_workbench: true` and process via `RUBE_REMOTE_BASH_TOOL` |
| Rate limiting | Space out searches; reduce parallel batch size to 2-3 |

## Example Usage

```bash
# Quick overview of a topic
/deep-research What are the latest developments in quantum computing? --depth shallow

# Balanced research (default depth)
/deep-research Impact of AI regulation on open-source development

# Exhaustive deep dive
/deep-research Compare the effectiveness of different carbon capture technologies --depth deep

# Save to file with more models
/deep-research History and current state of mRNA vaccine technology --output research/mrna_report.md --models 4

# Focused technical research
/deep-research What are the performance tradeoffs between PostgreSQL and CockroachDB for write-heavy workloads?

# Current events research
/deep-research What happened with the latest US antitrust cases against tech companies? --depth deep --output reports/antitrust.md
```

## Notes

- Requires Rube/Composio MCP with at least one web search integration connected (e.g., Exa, Tavily, SerpAPI)
- Requires PAL MCP server configured with at least 2 model providers for consensus
- Models are discovered dynamically via `mcp__pal__listmodels`
- For controversial or politically sensitive topics, the consensus phase is especially valuable — the for/against/neutral stances help surface multiple perspectives
- Search queries are formulated to avoid bias — multiple perspectives are explicitly sought
- All source URLs are preserved and cited in the final report for verifiability
- The `--depth deep` option can make 15-25+ API calls; use judiciously
- If Rube MCP is not available but PAL MCP is, the skill degrades gracefully to analysis-only mode using `WebFetch` for URL content
- The `memory` parameter in `RUBE_MULTI_EXECUTE_TOOL` is used to track discovered source URLs and session state across calls
