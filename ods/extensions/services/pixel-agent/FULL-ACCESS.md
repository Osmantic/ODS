# Full Access integration status

This candidate connects authenticated Dashboard Settings to a root-owned access
coordinator, the existing owner configuration controller, and durable admission
gates in both Pixel Edge and the gateway plugin. It is source implementation,
not evidence of an installed or operationally accepted Full Access deployment.

The implemented adapter is Linux/systemd, including a systemd-enabled WSL guest.
Native Windows and macOS/launchd return unavailable. The gateway hook/probe
adapter is pinned to the inspected OpenClaw 2026.6.33 contracts; other versions
require qualification before transitions become available.

The Settings request includes a strict mode, the last inspected revision and an
explicit confirmation boolean. The API never accepts a command, path, UID,
credential or arbitrary service name. The owner must confirm the risk before
enabling Full Access. Existing model lifecycle operations exclude API transitions.

`ods-pixel-access.service` runs the coordinator from root-protected
`/usr/local/libexec/ods-pixel-access`, with isolated Python and cwd `/`. A private
Unix socket checks peer credentials; the owner-facing host agent is a bounded
client. The installer records the exact installed OpenClaw executable and owner
in root-owned `/etc/ods/pixel-access.json`. Owner-editable JSON remains data.
Neither the coordinator nor its owner subprocess imports checkout/cwd/PYTHONPATH
code. Runtime checks reject owner-writable program ancestors. A historical root
host-agent override must use isolated Python (`-I`) and a protected root-owned
program tree before Full Access can be enabled; the normal host-agent unit runs
as the install owner and delegates privilege only to this coordinator.

Owner identity comes from the gateway systemd User and passwd entry, checked
against the installed owner and private ODS management marker. Configuration is
the owner's `~/.openclaw/openclaw.json`. The existing five-field baseline remains
unchanged. The staged installed OpenClaw validator runs as that owner, with its
HOME/PATH and private output. Schema errors never expose CLI diagnostics.

`host/pixel_access_mode.py` wraps the existing pure configuration helper. Enabling
requires explicit confirmation, a live activity check, configuration validation,
and a caller-supplied service restart with health verification. Restoring the
baseline also requires activity and restart checks. Both operations preserve
unrelated configuration fields. Failed transitions retain recovery evidence or
restore the prior bytes; replaced rollback files and concurrent configuration
changes are detected. These checks do not provide an atomic compare-and-swap
against another process running as the same OS user.

The native gate observes `before_agent_run`/`agent_end` across all agent IDs,
including native and cron sessions, and tracks tool calls plus detached exec
sessions. These hooks ignore conversation bodies. The installer's existing
`hooks.allowConversationAccess=true` is required by OpenClaw even for this
metadata-only use; its absence makes transitions unavailable. A live second
process cannot overwrite the gateway's gate. Standalone local CLI agents sharing
that state are consequently refused while the gateway process owns it.

Edge's private persistent volume is initialized only when empty. Existing or
partial state is never reset. Both gates must be held before config replacement;
an initial busy inspection rejects the change before journaling or settings
mutation. Admission racing that inspection makes lease acquisition fail while
the journal preserves any partial hold; it never stops the active work.
The root journal keeps lease tokens through crashes. Failure leaves admission
held for recovery. A stopped gateway is considered idle only with the same
durable native lease, MainPID zero, a stopped/failed unit and an empty cgroup.
No active service is killed to establish idle. Native requests arriving during a
transition receive a retry response; they are not a durable queue.

Full Access changes `ProtectSystem` and `ProtectHome` only in one recognized
mode-specific drop-in. Restoring removes only that exact file and verifies the
original effective unit settings. UID permissions, NoNewPrivileges, capability
limits, private /tmp and explicit readonly program mounts remain. Existing owner
group permissions also remain, including any administrative authority they carry;
this is not a promise that an owner with Docker/admin access lacks root-equivalent
capabilities. No new root UID or sudo permission is granted by the mode switch.

`get_status()` in the pure controller still returns `runtime_verified: false`.
The bridge reports an effective mode only after restart/health and actual calls
through installed core exec/write constructors under the loaded agent/model
policy. The probe checks cancellation and an owner-writable fixture outside both
home and workspace (`/var/lib/ods-pixel-access-probes/<uid>`). It accepts host
access in Full Access and requires host-boundary denial in safer mode. Proof is
bound to the service PID, current config hash and unit settings; a restart or
config drift makes the UI show Not verified. Configuration alone never supplies
the effective label.

Before live acceptance, install the reviewed root-owned coordinator, rebuild Edge
with its persistent volume, and activate the reviewed plugin through the normal
custody path. Preserve any already provisioned transition volume/override. First
inspect status and run Verify safer mode. Exercise an active native/cron request
and an Edge chat while attempting a change; neither may be stopped or reset.
Then confirm Full Access, verify an owner-writable path outside home/workspace,
cancel a running command, and restore safer mode. Recheck original unit settings,
the five-field baseline, model/memory settings, readonly program mounts, pending
receipt recovery, stale revisions and a failed restart/probe. All configured,
synthetic-test, installed and actual-operation evidence must remain separate.
