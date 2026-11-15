# Pixel ingress timing diagnostics

Set `ODS_PIXEL_TIMING=1` in the ingress process environment to emit one bounded
JSON receipt to stderr for each request that enters `forwardChat` and reaches
its terminal cleanup. The default is off. The receipt does not change response
bytes, model settings, continuation policy, verification, or cancellation.

For a systemd installation, use a temporary service drop-in containing
`[Service]` and `Environment=ODS_PIXEL_TIMING=1`, then reload systemd and restart
only `pixel-ingress.service` after active requests finish. Remove that exact
drop-in and restart the service to disable it. For the macOS container, apply
the variable to `pixel-native-ingress` through an explicitly included Compose
override; recreate only that service and remove the override to disable it.
These are operator diagnostics, not a permanent product default. Preserve the
baseline source hash and service configuration for rollback before deployment.

The receipt has schema version 1, kind `ods-pixel-agent-timing`, a random request
correlation, the latest validated completion run ID (or null), streaming mode,
outcome, clock validity, total milliseconds, and allowlisted stage durations.
It contains no prompt, response, tool content, user/session identifier, URL,
credential, or arbitrary exception text. A replacement completion without a
valid run ID clears attribution rather than inheriting an earlier identity.

Stages are preparation, gateway headers, gateway body, read-only continuation,
decision continuation, subagent delivery, verification, completion hook and
delivery. They measure elapsed time in the ingress wrapper with a monotonic
clock. They do not establish engine queue time, prefill, token latency, or
browser rendering time. The gateway headers stage can include the whole agent
turn because the ingress requests a verified terminal completion upstream.
Stages need not sum to the total: parsing and other wrapper work remain outside
the named stages. A missing stage is unobserved, not a zero duration.

`delivered` means the normal response was passed to `res.end`; it does not prove
that a browser received or rendered it. `error` means this normal delivery path
was not reached. `aborted` means the transport controller was aborted at cleanup;
it does not establish who initiated cancellation. History replay and other
routes that bypass `forwardChat` do not emit this receipt. Setup failures before
its cleanup block also have no receipt. Count missing receipts explicitly when
joining with client-side run evidence.

Invalid or regressing clocks produce null timings. Diagnostic sink failures do
not replace chat results. Receipts are capped at 4096 bytes and may be dropped
if stderr is already backlogged. Compare diagnostics on/off overhead before
using the measurements for a performance claim. Keep the same setting in both
arms of a controlled engine or serving experiment.
