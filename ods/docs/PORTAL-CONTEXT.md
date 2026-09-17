# Conversation context in Portal

Portal keeps the visible conversation separate from the context supplied to the
model. `/compact` (also `/compactar`) requests native OpenClaw summarization in
the current session. It does not open a new chat, clear the draft, delete the
visible transcript, or train the model.

OpenClaw's automatic compaction remains the single automatic controller. Its
context window, output reservation, recent-message allowance, and compaction
settings determine when it runs. The dashboard does not start a competing
summarizer based on a stale percentage or a fixed character count. Context usage
is a runtime observation, not an estimate derived from the input box.

Measurements are bound to the native session and the confirmed model route.
Changing the model, its context capacity, or its remote destination invalidates
the previous measurement. A credential-free route fingerprint distinguishes two
remote providers serving the same model name. A transient status failure keeps
the last confirmed identity, but cannot authorize a model switch. An unobserved
new route reports usage as unavailable until the runtime supplies a measurement;
it must not display the old model's count or invent zero usage.
The effective context budget includes the native agent's configured cap (or its
inherited default), bounded by the model's declared capacity. A previous session
capacity or the plugin's prompt metadata cannot override a smaller native cap.
Changing that budget invalidates the persisted measurement; restoring the old
budget does not restore its old usage count.

Conversation activity is separate from global runtime availability. Another
chat being active does not make an idle conversation appear to be working. A
manual compaction may still need to wait for the shared runtime's admission
lease; that does not erase its history or restart its previous task.

## Delivery and recovery

- The dashboard sends a versioned history snapshot with each identified turn.
  The snapshot contains only user/assistant text, including the new request.
- The ingress archives that snapshot and acknowledges delivery only after a
  terminal response is confirmed. Subsequent requests send new conversation
  material instead of replaying the already stored context into OpenClaw.
- A missing native session is rehydrated as inert historical reference under
  a maintenance lease, then compacted before executing the new request. Past
  requests are never executed as part of rehydration.
- The agent can use `pixel_ods_history` to search or page through its own archived
  conversation. This tool cannot select another chat. Returned excerpts retain
  their status as historical data; they are not fresh approvals or tool receipts.
- Manual compact requests have durable identities. Closing the tab does not
  cancel the native job. Reloading checks its status without repeating it.
- Active work and model transitions are coordinated with compaction. A transport
  timeout is an unconfirmed outcome, not proof that the operation stopped or
  succeeded. Preserve its identity and check status before retrying.
- A confirmed gateway restart ends the old compaction process without proving
  its result. The interface reports that interruption, releases only the old
  request's block, and keeps uncertain conversation turns subject to explicit
  recovery. Reaching the receipt limit fails before admission instead of leaving
  the conversation waiting for a job that was never started.
- Agent teams retain their exact model-facing transcript separately from the
  shortened text used for planning and from labels displayed in the interface.

The archive is bounded to 2,000 messages and 4 MiB of UTF-8 message content per
conversation. HTTP envelopes have an independent 8 MiB limit. Storage exhaustion
is reported rather than silently deleting earlier turns. A smaller active model
context does not imply that the archived conversation was deleted.

## Deployment

Ship the dashboard, dashboard API, edge proxy, ingress, and OpenClaw plugin
together. The ingress history directory must be persistent and private to its
service user; `PIXEL_CHAT_STATE_DIR` selects it. The Linux/systemd installer
provisions that state directory. The Node ingress also has a macOS application
support default, but the current macOS installer does not provision native Pixel
host services; that installation path remains a separate project limitation.
OpenClaw's native session and compaction receipts stay in its persistent state
directory. Do not store these paths in a container's disposable writable layer.

The integration uses the existing HTTP/Unix-socket and OpenClaw SDK boundaries;
there are no WSL commands in the request path. Windows installations continue to
use the project's WSL2 backend and Linux uses its existing host services. The
portable modules do not constitute validation of a native macOS installation.
This feature does not change the maturity or requirements of those installation
backends.

The qualified OpenClaw patch for `sessions.compact` rejects a busy session instead
of aborting a run. Unsupported native runtime versions must fail qualification;
never silently replace summarization with line truncation (`--max-lines`).

Manual compaction currently supports providers with stable runtime routes. The
managed `ods-policy` provider creates routes for individual turns; compacting
outside that lease is rejected with `unsupported-model`. Supporting that path
requires a dedicated compaction lease, not reusing an expired route or bypassing
its model-access policy. Native automatic compaction within a normal turn keeps
the turn's existing provider lease.

Summaries can omit details, particularly with small models. Preserving the
archive and execution records allows recovery of exact facts, but does not make
all models equally capable or remove the model's context and hardware limits.

## Verification

The native SDK qualification in
`extensions/services/pixel-agent/tests/runtime_auto_compaction.integration.mjs`
checks that a short incremental input still triggers automatic compaction when
the persisted history fills 8K, 32K, or 64K contexts. Run it with
`OPENCLAW_PACKAGE_DIR` pointing to the qualified installed OpenClaw package.
The separate hydration qualification uses an isolated temporary session.

The integration was also exercised with the local Qwen 3.5 4B runtime: store
three facts, continue the same conversation, compact it, repeat the same compact
request identity, then recall the facts. Native compaction completed once and
the next answer preserved all three facts. A small conversation may grow after
summarization overhead; the UI does not invent a reduction or a token measurement
when the runtime has not reported one.
