# Durable memory

Store only owner-approved, long-lived facts that materially improve future assistance. Prefer short entries with a source date. Do not store credentials, OAuth tokens, private keys, health records, financial account numbers, or full message contents.

## Standing operating decisions

- 2026-08-17: Dream Fleet Local-First Operating Contract is canonical (see AGENTS.md). Codex plans/supervises/accepts; Tower2 DSV4 leads long-context analysis, integration, planning, troubleshooting, execution, test coordination; Tower1 and Tower3 are Qwen3.6-27B Q4 no-think parallel bounded workers (never silently Qwen3.8, never enable thinking). Target >= 90% useful execution locally and >= 90% fresh-token local share; report honest fresh/gross ratios; repair the local path before any disclosed fallback. First response to a substantive task states: local execution plan / Codex supervision / acceptance evidence / ratio target.
