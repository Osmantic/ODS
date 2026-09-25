# Portal agent teams

Type `/agents` or select **Agents** in Prompt commands. The command becomes a
composer mode: describe the task normally, without a numeric argument. A
Coordinator uses the selected model to choose 1–6 workers. Explicit team-size
preferences can be expressed in the request. The API accepts only a bounded
integer from that response; malformed plans fall back visibly to Builder and
Reviewer. Independent review uses at least two workers.

Every worker has a distinct retained Portal session. The dashboard API schedules
one worker at a time across teams, passes actual prior reports as untrusted
evidence, and preserves the selected model and normal broker permissions.
Builders perform the task and necessary checks. Explorer, Planner, Reviewer,
Verifier and Reporter inspect evidence with read/search tools; they cannot
modify files, run shell commands, publish, operate services or spawn agents.
Coordinator tools are disabled. These restrictions are enforced by the plugin,
including deferred tool calls, rather than relying on model instructions.

Colored Portal icons open each assignment, actual response, tool activity,
clarification questions and terminal state. These are observable results, not
private model reasoning. Reloading the browser preserves server-side execution
and history. A failed or unverified worker stops subsequent workers. Stop needs
an acknowledgement; a process interruption is shown as unconfirmed and is never
automatically replayed. Clarification answers resume that same worker session.

Each worker checks inference readiness before dispatch, with three bounded
checks for temporary unavailability. A transport failure or empty answer in a
read-only worker gets one automatic recovery attempt with a new request ID.
Builders are never automatically replayed. A failed read-only worker can also
be retried explicitly, up to twice, preserving earlier completed work. Readiness
checks and recovery do not restart other applications or change the model.

State lives in the private `ODS_DATA_DIR/pixel-agent-teams` directory. Start
requests are owner-scoped and idempotent. The current bounds are six workers,
four queued/working teams, 128 retained team records, four clarification rounds
per worker and 512 KiB per record. Full history is preserved when a bound is
reached. Deployment needs both the dashboard/API and updated Pixel plugin and
ingress: older ingress versions do not supply the terminal outcome receipt.

Implementation uses the existing HTTP runtime and portable Python/React APIs;
no shell or OS-specific process launching is added by team orchestration.
Validated locally on Windows with Docker/WSL and the configured Qwen 3.5 4B
model, plus focused transport, scheduling, guard and UI tests. Native macOS,
native Linux, every model and every hardware configuration are not certified.
Team orchestration cannot remove a model's reasoning errors or supply missing
runtime capabilities.
