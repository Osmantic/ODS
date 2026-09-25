# Deployment runbook

## 1. Establish the host

Use a dedicated non-root Linux account on Ubuntu 24.04 LTS or Debian 12. Clone a
tagged Pixel release, verify its published SHA-256 checksum if using the handoff
archive, then run Pixel Doctor before bootstrap for a local-only readiness summary:

```bash
./pixel doctor
./pixel bootstrap
./pixel bootstrap --apply
```

Doctor reports broad capacity tiers, the presence and broad context tier of generated model
configuration, and a conservative model/context starting point—not an exact benchmark or
fit guarantee. An unreadable generated configuration fails closed as unavailable. Doctor
makes no network or provider call and does not expose a hostname, serial, device name, model
identifier, provider URL, path, process output, or exact hardware value.
Use `./pixel doctor --json` only when the strict content-free contract is more useful than
the plain-language view.

Bootstrap verifies Docker access, the manifest-selected Node requirement and OpenClaw
release, pinned
SearXNG/llama.cpp OpenClaw plugins, the Pixel sandbox image, and—when enabled—the
version-locked Web Courier wheels and Chromium runtime. Its mutating mode is explicit
and idempotent. Web Courier dependencies are cached beneath `PIXEL_INSTALL_DIR`; apply
installs its release environment offline from that reviewed wheel set.
Reference container images and the sandbox base image are digest-locked in
`RELEASE-MANIFEST.json`; update them only as a tested release-contract change.
Bootstrap builds the non-root sandbox user with the deployment owner's numeric UID and
binds that UID into the image labels, so private workspace mounts remain writable without
granting access to a different host account. Preflight and verification reject a stale
image built for another owner.

## 2. Generate client configuration

For credential-free first-run settings, start the local page and follow its reviewed
`configure` and `plan` actions:

```bash
./pixel ui
```

The printed address is loopback-only. The page cannot receive credentials or activate
the deployment. When Frontier is enabled it can choose eligible ChatGPT plan access or
separately billed API access plus a managed local budget, but authentication remains a
trusted-terminal step. Its optional maintenance/containment actions are disabled until an owner
installs the private policy described in `CONTROL-SURFACE.md`; that policy is included in
signed encrypted private-state backups. A fresh deployment requiring a provider credential or advanced signed-pack paths
must still provision those through the private JSON/ignored environment workflow below.
Existing advanced private fields are preserved when public settings are later changed in
the page. See [CONTROL-SURFACE.md](CONTROL-SURFACE.md).

Copy `onboarding.example.json` outside Git or keep the resulting file untracked. Choose
an infrastructure `deploymentProfile` and modular `capabilityProfile`, then fill the
client identity, IANA timezone, absolute paths, Google account, and existing model and
search endpoints. Individual limb flags override the capability profile. Then:

```bash
./pixel configure --answers /secure/path/onboarding.json
```

Configure preserves a mode-`0600` canonical copy at
`${XDG_CONFIG_HOME:-$HOME/.config}/pixel-deployment/onboarding.json`; this private copy
is included in signed backups and is the preferred input for later upgrades. It may
contain client-private values and must never be committed.

Interactive `./pixel configure` is also supported. Re-running requires `--force`.
Generated files live in ignored `.generated/`; secrets belong only in ignored `.env`
or external credential paths.
For an existing deployment, `modelApiKey` may be `preserve-existing`; the renderer then
retains the configured provider key without placing it in onboarding or generated
deployment records. A fresh deployment must set its key through the ignored `.env` or a
supported OpenClaw secret provider before planning.
`OPENCLAW_HOME` in Pixel's answers denotes the OpenClaw state directory; generated
runtime files map it to OpenClaw's native `OPENCLAW_STATE_DIR` and
`OPENCLAW_CONFIG_PATH` variables.

`./pixel backup /secure/location age1CLIENTBACKUPRECIPIENT` captures user configuration/workspace plus
the root-owned Source and Operations Broker private state, OAuth token, SSH identity,
host pins, policy, plans, approvals, leases, and audit data. The command creates or uses
a deployment-local Ed25519 backup-signing key, writes a detached SSH signature and
checksum beside the mode-`0600` archive, and streams the archive directly to the
supplied `age` recipient without a plaintext tarball. Keep the decryption identity
offline from Pixel. Preserve a trusted copy of `backup-allowed-signers` separately from
the archive and apply the client's retention controls.

Deep Work is opt-in backup scope because its private roots are installed separately from the
base assistant. Set `PIXEL_DEEP_WORK_BACKUP_ENABLED=1` and keep
`PIXEL_KNOWLEDGE_VAULT_CREDENTIAL` outside the captured state, configuration, and vault roots.
The reference credential path is
`/etc/pixel-work-credentials/pixel-knowledge-vault-key`. Backup stops and restores the exact
active `pixel-work-*` units so ledgers and encrypted vault state are captured at one quiet
boundary; the external vault key is never archived.

For a new vault, first create owner-private parent directories for the vault and external
credential, then review and apply the exact setup:

```bash
./pixel work-knowledge setup-review \
  --vault /var/lib/pixel-knowledge/vault \
  --vault-id knowledgevault-012345abcdef \
  --credential /etc/pixel-work-credentials/pixel-knowledge-vault-key
./pixel work-knowledge setup-apply \
  --vault /var/lib/pixel-knowledge/vault \
  --vault-id knowledgevault-012345abcdef \
  --credential /etc/pixel-work-credentials/pixel-knowledge-vault-key \
  --confirm-review-sha256 HASH-FROM-REVIEW
```

Run this as the intended unprivileged service identity after the platform's administrator has
installed the private directories with that ownership. The command never prints or overwrites
the key and can resume an exact durable setup stage after interruption.

Validate and rehearse every recovery set before accepting it:

```bash
./pixel restore /secure/location/pixel-private-TIMESTAMP.tar.gz.age \
  --identity /offline/age-identity --validate-only
./pixel restore /secure/location/pixel-private-TIMESTAMP.tar.gz.age \
  --identity /offline/age-identity --rehearse /new/empty/rehearsal-root
```

An actual replacement requires both `--replace --confirm`, stops affected services,
creates a fresh signed/encrypted pre-restore safety backup, stages only audited roots,
and automatically reinstates the pre-restore state if verification fails. Use
`--signers /trusted/backup-allowed-signers` when restoring onto a clean host.

When the archive contains a knowledge vault, an actual restore also requires
`--knowledge-vault-id`, `--restored-knowledge-key`, and `--current-knowledge-key`. Pixel
reconciles the still-live authoritative deletion ledger into separate historical staging,
removes resurrected ciphertext, rotates the staged vault to the current key, and deep-audits
it before the atomic swap. Validation and isolated rehearsal remain keyless and read-only.

For the `reference` profile, inspect `deploy/compose.yaml`, optionally set a GGUF model
file and `PIXEL_ENABLE_REFERENCE_MODEL=1`, and start services:

```bash
./pixel services render
./pixel services up --confirm
```

## 3. Authorize and isolate source credentials when enabled

After creating the client-owned OAuth application, stage consent and immediately move
the credential into the dedicated broker identity:

```bash
./pixel authorize
./pixel source-broker --confirm
```

Skip this section when email, Calendar, and social are all disabled. The second command installs the hardened broker service and timer, performs a live
refresh, verifies that the gateway owner can read projections but not the OAuth token,
then removes the staging token and client file. Do not restart Pixel between these two
commands or point the gateway at the staging credential.

## 4. Install Operations authority when enabled

Copy `deploy/ops-broker/policy.example.json` to a client-private path, replace the
placeholder identities and roots, and keep every unused target disabled. After
configure, install the isolated broker and generate its deployment-specific key:

```bash
./pixel ops-broker --confirm
./pixel ops-keygen --confirm
```

Migrate an existing v1 policy to a separate v2 file, explicitly classify every target,
and map the reusable action pack in private onboarding:

```bash
./pixel ops-policy-migrate /secure/client/policy-v1.json \
  /secure/client/policy-v2.json --environment TARGET_ID=production \
  --update-onboarding /secure/path/onboarding.json --confirm
./pixel ops-action-pack /secure/path/onboarding.json \
  "$PWD/deploy/ops-broker/action-packs.example.json" \
  example-worker TARGET_ID --confirm
```

Enroll each Linux target from an already trusted operator alias, install its private
action configuration, then update/reinstall the private policy. Never enroll a target
with a changed or unverified SSH identity.

```bash
./pixel ops-target TARGET_ID OPERATOR_ALIAS EXPECTED_HOSTNAME --confirm
./pixel ops-target-actions OPERATOR_ALIAS /secure/client/actions.json \
  /secure/client/managed.json --confirm
./pixel configure --answers /secure/path/onboarding.json --force
./pixel ops-broker --confirm
```

After an upgrade, refresh helper binaries without rotating keys or host pins:

```bash
./pixel ops-target-refresh TARGET_ID OPERATOR_ALIAS EXPECTED_HOSTNAME --confirm
```

See [OPERATIONS-LIMB.md](OPERATIONS-LIMB.md) and
[OPERATIONS-AUTONOMY.md](OPERATIONS-AUTONOMY.md) for policy, runner, approval,
authority, action-pack, and acceptance procedures.

## 4b. Install Frontier review when enabled

For the managed path, select an access mode and Starter, Balanced, or Expanded in
`./pixel ui`, then prepare the configuration. New page-based setups default to eligible
ChatGPT plan access and Starter. The page performs no login and accepts no key. Complete
the selected authentication in a trusted terminal before installing the broker.

For advanced policy control, copy `deploy/frontier-broker/policy.example.json` to a client-private path. Review the
two enabled task classes, classifications, never-egress categories, provider/model,
local-attempt limits, forced-review reasons, cache/quality circuits, authority default,
token/job/failure/cost budgets, and retention. For eligible ChatGPT subscription access,
start from `policy.chatgpt.example.json` and provide a separate saved Codex `auth.json`;
for separately billed API use, provide an OpenAI API key as one mode-`0600` line. Keep
either deployment-owned credential outside Git and reference both paths from private
onboarding. Set `frontierAuthMode` to the same authentication mode and
`frontierBudgetProfile` to `custom`; configure rejects conflicts rather than silently
overriding the private policy. The primary Pixel model URL must remain private.

ChatGPT plan usage and API billing are separate boundaries. Pixel's rolling request,
token, failure, and optional cost limits are additional local safeguards; they do not
replace ChatGPT workspace allowances, API Platform billing, or provider-side spend
controls.

```bash
./pixel configure --answers /secure/path/onboarding.json --force
./pixel frontier-broker --confirm
./pixel frontier-authority show
```

The installer checks the Codex CLI contract and then proves that the gateway identity
can publish requests and content-free finalization receipts/read projections but cannot
read the credential, policy, plans, approvals, archive, private cache, integration
archive, authority ledgers, or provider runtime. See
[FRONTIER-LIMB.md](FRONTIER-LIMB.md). Skip this section when Frontier is disabled.

For an existing private schema-v2 custom Frontier policy, the local page can also draft
new rolling limits. The page stores a private 15-minute proposal and displays its exact
terminal handoff; it cannot apply or activate the change. Review the old/new values and
potential-usage warning, then run the displayed command:

```bash
./pixel frontier-budget apply --proposal-id frontier-budget-... \
  --proposal-hash SHA256 --confirm
```

If the page was started with custom `--state` or `--onboarding` locations, place those
global options before `apply`. The terminal rechecks the hidden canonical policy path,
source-policy hash, authentication and billing mode, expiry, and proposal hash. It writes
an owner-only exact backup and changes only the source policy's `budgets` object; it does
not configure, plan, activate, restart, authenticate, approve, or contact a provider.
Run configure, plan, and deployment apply separately after reviewing the source-policy
change. If the terminal is interrupted after its durable application claim, repeat the
same exact command. Pixel will finish only that claimed change after recognizing either
the exact prior or exact proposed policy bytes. Keep the onboarding and policy parent
directories mode `0700` and both JSON files mode `0600` on POSIX hosts.

Release qualification is a separate, explicit terminal workflow. After installation,
use `./pixel frontier-live-qualify authorization` and `prepare`, inspect the displayed
fixed synthetic plan, then run its exact `confirm ... --transmit` command only when the
deployment owner has authorized one provider use. Preparation never calls the provider.
API mode requires a fresh metered custom policy and a separate Platform billing ceiling;
ChatGPT mode uses the eligible plan/credits boundary and never claims API billing. Keep
the short-lived consent file private and do not substitute production data. Follow
[FRONTIER-LIVE-QUALIFICATION.md](FRONTIER-LIVE-QUALIFICATION.md).

## 4c. Authenticated portal adapter (not yet a supported production path)

The productization branch contains a narrow Cloudflare Access origin adapter for the
future primary Pixel workspace. Do not point a Tunnel at `./pixel ui` and do not expose
port 43117. The current release manifest records this path as unqualified pending
supported-host service confinement, live policy evidence, and
deployment rehearsal.

The intended topology is Cloudflare Access and Tunnel to a loopback adapter, then an
allowlisted second loopback hop to Pixel control. Use two Access applications/policies:
the normal workspace has its own audience, while consequence-bearing routes have a
different audience and independent MFA required at every login. The origin validates the
signed Access assertion and exact audience; network location or header presence alone is
not authentication. Email OTP may gate the workspace, but the approval application must
add the owner's configured independent MFA method. Keep both application audiences, the
allowed-email list, tunnel credentials, and the adapter review token in owner-private
deployment state outside Git.

The workspace Access application must include `GET /api/v1/approvals`,
`GET /api/v1/permissions`, `POST /api/v1/actions/request`, and
`POST /api/v1/actions/cancel`. Create the approval application on the exact
`pixel.lightheartcloud.com/approve/*` path, enable its Access **Cookie Path** setting, and
require independent MFA on every login. It protects `GET /approve/session`,
`GET /approve/session.js`, `POST /approve/execute`, `POST /approve/onboarding`, and
`POST /approve/permissions` under
its distinct audience. The adapter maps those narrow public routes to fixed private
control endpoints; it does not expose the private endpoint names through the workspace
application. The
browser opens the approval session with a fresh 256-bit challenge and executes nothing if
the popup is blocked, closed, mismatched, or times out. Do not collapse the two audiences
or exempt these paths for convenience.

`control/access-adapter.example.json` documents the exact private inputs. On POSIX, copy
it to an owner-only directory, replace every placeholder, and set both it and the separate
43-character review-token file to mode `0600`. Start the control upstream only with the
matching token file:

```bash
./pixel ui --adapter-mode --review-token-file /run/pixel-portal/review-token
node control/access-adapter.mjs --config /secure/pixel/access-adapter.json
```

Both processes refuse non-loopback listeners. The adapter has no generic proxy route and
does not forward Access assertions, browser cookies, identity claims, credentials, or
arbitrary headers to Pixel control. Treat these commands as an engineering rehearsal,
not as authorization to change the live Cloudflare application or tunnel.

## 5. Plan, review, and apply

```bash
./pixel plan
./pixel apply --confirm
```

Plan runs host, endpoint, plugin, image, schema, and secret checks; renders
`dist/openclaw.json`; records its SHA-256 hash; derives a clean Git commit/tree and
matching compatibility-baseline identity; and writes a separate immutable runtime-source
manifest. A dirty or non-Git source is recorded as unavailable rather than assigned a
false identity. Compatibility evidence matches Pixel, OpenClaw, and every pinned plugin
version. Apply refuses changed inputs,
installs an immutable versioned plugin/Web Courier/broker release, merges workspace templates,
updates the three managed navigation files with rollback copies, backs up affected
state, migrates the gateway into a privilege-isolated system service and installs the Courier system service,
and verifies the live result—including a
loopback-rejection courier probe. The installed release has its own byte manifest.
Successful verification atomically writes an owner-private, content-free runtime receipt
binding that installed manifest, current source, active generated/OpenClaw configuration,
profiles, connector checks, and hashed model identity. The receipt never claims model
capability; that requires a separate real-backend qualification. A failed verify removes
the prior receipt and automatically restores the previous config, managed navigation
files, services, and release link.

Installing or rolling back the Courier unit under `/etc/systemd/system` requires
`sudo`; the service itself runs as the deployment owner without capabilities and sees
only its read-only runtime plus the writable Pixel workspace/log paths.

## 6. Google Workspace and Calendar actuators

Complete the per-client procedure in [CLIENT-ONBOARDING.md](CLIENT-ONBOARDING.md).
The broker requests only `gmail.readonly` and `calendar.events`. Pixel's plugin has no
Google credential or direct Google network implementation. Gmail has no mutation
actuator. The default `in:inbox` and `in:sent` queries are exhaustively paginated up to
the documented safety ceiling. Inbox records contain bounded sanitized summaries; Sent
records contain metadata only and their bodies are never requested, including dual-label
messages. Email tools return bounded pages plus query, page count, result estimate, and
truncation coverage. Completed projections reuse immutable sanitized records and fetch
only new or safety-transitioned message details, allowing a one-minute refresh cadence.
A folder-wide missing result is conclusive only when the caller has reached
`hasMore=false` and coverage is fresh and complete. With `calendarDirectEnabled`, a
private create with no attendees or a time-only ETag-bound reschedule is applied by the
bounded actuator. For a consequential proposal, the operator may still inspect and apply it with:

Reviewed proposals may explicitly set `sendUpdates` to `all` when the owner wants Google
to send invitations, update notices, or cancellations. The default is `none`. Any
notification-producing action remains outside bounded-direct execution; private
attendee-free creates and time-only reschedules remain useful direct actions without
notifications.

```bash
./pixel source-show calendar-...
./pixel source-approve calendar-... PROPOSAL_SHA256 --confirm
```

`source-show` copies the consequential proposal into a broker-owned, gateway-read-only snapshot and
prints its SHA-256. The one-shot actuator accepts only that hash-bound snapshot,
uses only its explicitly reviewed attendee-notification setting, and writes the proposal
hash into its bounded result. A create
precommits a deterministic provider event ID before the write. If the provider response
is lost or otherwise indeterminate, do not approve or submit it again. Reconcile that
exact identity instead:

```bash
./pixel source-reconcile calendar-...
```

Reconciliation performs a read only. It records success only when the observed event ID,
approved fields, and private proposal-hash marker all match. A missing or mismatched event
remains unknown or fails closed; it is never reported as success and never retried.

## 7. Acceptance

Run `./pixel verify`, then complete every item in
[ACCEPTANCE-CHECKLIST.md](ACCEPTANCE-CHECKLIST.md). Do not call a deployment complete
until the client owns its Google project, has tested recovery, and has accepted the
calendar direct/confirmation policy.

## Uninstall boundary

This kit intentionally has no one-command destructive uninstall. Disable the user
service first, back up private state, and have the client explicitly approve each
directory to remove. Source releases, OAuth material, workspace memory, and OpenClaw
runtime state have different retention requirements.

## Optional rootless owner mesh

A separate, additive rootless deployment runs one owner-trusted Pixel per tower
under `pixel-mesh-gateway.service` without touching the hardened system deployment
above. It is a peer-to-peer fleet tool, not the client product path. See
[deploy/mesh/README.md](deploy/mesh/README.md) for architecture, install, update,
rollback, status checks, trust boundary, runtime generation, and secret exclusions.

## Optional narrow release operator (password-free activation/rollback/verify)

Routine 4.x `apply`, `rollback`, and `verify` privileged unit/systemctl/probe steps can be
routed through a dedicated forced-command transport identity instead of password-backed
`sudo`. The owner account keeps no generic NOPASSWD sudo. This is strictly opt-in: without
it, the existing password-backed `sudo` path is unchanged.

See [deploy/release-operator/README.md](deploy/release-operator/README.md) for the exact
operator verbs, security boundary, one-time root provisioning, rollback semantics, and the
note that legacy 3.2.2 migration remains a separate one-time human-authenticated `sudo`
transaction outside this operator's surface.
