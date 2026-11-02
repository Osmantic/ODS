# ODS Remote GPU Backend Plan

Status: execution plan

Source feature: PR #1638

Target: current `main`, delivered as a dependency-ordered PR stack

## 1. Decision

ODS will support a remote GPU as a first-class provider without creating a
second model-routing authority.

Every manifest consumer declared with `llm.route: gateway` continues to call
LiteLLM with the stable model name `ods/current`. In remote-provider mode,
LiteLLM maps that name to a validated remote OpenAI-compatible provider. A
documented `route: direct` gateway peer or observability service may keep a
non-recursive direct upstream only when bypass-inventory tests prove its route.
The existing local Model Switchboard remains the sole owner of local runtime
activation and local model state.

Two remote capability levels are deliberately distinct:

1. `openai-compatible`: inference-only. ODS can discover and select model IDs
   advertised by `/v1/models`, but it cannot promise download, load, unload, or
   delete operations.
2. `ods-peer`: inference plus authenticated model lifecycle. The remote ODS
   node performs download, activation, rollback, and deletion through its own
   host agent and Model Switchboard.

Capability level is discovered and attested by the provider handshake, not
trusted from user input. The Dashboard must display that attested level and
must never render lifecycle actions the provider cannot perform.

## 2. Why PR #1638 Is Replaced

PR #1638 established the value of a Windows laptop using a remote workstation,
but it predates the Model Switchboard and cannot be safely rebased as written.

The replacement keeps its useful intent and contributor attribution while
removing these behaviors:

- no hand-written replacement of LiteLLM, Hermes, or application config files;
- no direct mutation of every consumer route;
- no manual `.compose-flags` tokenization;
- no Windows-only scheduled-task control plane;
- no unauthenticated remote-provider assumption;
- no model-health-driven SSH process churn;
- no partial configuration with manual-only rollback;
- no full `.env` copies in log directories;
- no inference that a generic OpenAI API can manage remote model artifacts.

The small Windows portability fixes in #1638 are independent and must travel in
a separate fix PR if current `main` still needs them.

## 3. Invariants

The following are merge blockers:

1. Every `llm.route: gateway` consumer uses LiteLLM and `ods/current`.
   `route: direct` exemptions require a documented, non-recursive remote route
   and bypass-inventory coverage, including Privacy Shield and Token Spy.
2. Remote provider selection does not rewrite consumer model IDs after initial
   convergence.
3. Remote routing is active only when `ODS_MODE=cloud` and
   `REMOTE_LLM_ENABLED=true`. `ODS_MODE` selects the runtime family and
   `REMOTE_LLM_ENABLED` selects the remote profile within cloud mode. The
   provider transaction sets both together, stores the prior complete routing
   state, and restores it on disable. Invalid combinations fail validation.
4. In cloud mode LiteLLM always selects generated `cloud.yaml`;
   `ODS_MODEL_SWITCHBOARD` never selects local-runtime `switchboard.yaml`.
5. PR 1A extends the canonical renderer to every generated routing surface and
   proves no installer, CLI, host-agent, or application helper emits a
   competing copy. Persisted state that cannot be file-rendered receives one
   idempotent convergence adapter.
6. Configuration follows `stage -> validate -> commit -> restart -> prove`.
   Failure automatically restores the previous proven configuration.
7. Provider transport health, API health, model availability, and application
   health are separate states with separate diagnostics.
8. Authentication is mandatory for every remote ODS peer. A generic provider
   may omit API auth only after explicit acknowledgement on an attested private
   transport. Network placement alone does not establish provider identity.
9. Secrets are never returned by APIs, written to generated public config,
   included in support bundles, or copied into backup/log directories.
10. New installs remain local by default. No remote service, port, key, or
   privileged container is enabled without an explicit user action.
11. Disabling or removing a provider restores the previous proven mode and
    leaves downloaded local models untouched.
12. An ODS peer token grants model scopes only. It cannot invoke shell,
    extension, update, auth, or arbitrary host-agent operations.
13. No release claim is valid without immutable product and harness SHAs.

## 4. Public Configuration Contract

The first provider slice adds validated keys to `.env.schema.json`:

| Key | Meaning |
|---|---|
| `REMOTE_LLM_ENABLED` | Explicit remote-provider switch |
| `REMOTE_LLM_TRANSPORT` | `direct` or `ssh` |
| `REMOTE_LLM_BASE_URL` | Provider API root, including `/v1` normalization |
| `REMOTE_LLM_MODEL` | Concrete model ID advertised by the provider |
| `REMOTE_LLM_TLS_CA_FILE` | Optional private CA path |
| `REMOTE_LLM_SSH_HOST` | Explicit SSH host |
| `REMOTE_LLM_SSH_USER` | Explicit SSH user |
| `REMOTE_LLM_SSH_PORT` | Explicit SSH server port |
| `REMOTE_LLM_SSH_INFERENCE_HOST` | Inference address from the SSH server |
| `REMOTE_LLM_SSH_INFERENCE_PORT` | Inference port from the SSH server |
| `REMOTE_LLM_SSH_CONTROL_HOST` | ODS peer-control address from the SSH server |
| `REMOTE_LLM_SSH_CONTROL_PORT` | ODS peer-control port from the SSH server |
| `REMOTE_ODS_PEER_URL` | Paired ODS control-plane root |

Requirements:

- URL, model, host, path, and task values reject control characters and
  newline injection.
- Ports are integers in `1..65535`.
- Direct URLs must not contain embedded user information.
- Direct URLs are interpreted from LiteLLM's container network namespace, so
  loopback addresses are rejected.
- API keys may be omitted only after explicit acknowledgement on a
  host-agent-attested encrypted private transport.
- Renderer input is typed. YAML and dotenv are emitted through structured
  serializers rather than interpolation.
- `LLM_API_URL` and `HERMES_LLM_BASE_URL` continue to point to LiteLLM inside
  the ODS network. They never point individual consumers at the remote host.

### 4.1 Endpoint security

Provider URL validation is a network security boundary:

- allow `https` by default and `http` only behind verified Tailscale routing or
  the internal SSH transport;
- reject fragments, embedded credentials, control characters, encoded or
  ambiguous hosts, unexpected base paths, and every other URL scheme;
- disable redirects by default; any enabled redirect is fully revalidated and
  an HTTPS-to-HTTP downgrade is always rejected;
- resolve all addresses before connection and reject metadata, link-local,
  multicast, broadcast, Docker administrative, and host administrative
  destinations;
- validate every resolved address, then revalidate after DNS changes;
- never trust a MagicDNS suffix or forwarded Tailscale identity header alone;
- accept Tailscale HTTP only after confirming the destination is a current
  tailnet peer and pinning the approved tailnet addresses;
- enforce resolution, route, redirect, TLS, and address policy inside the
  egress service that opens the actual provider connection, not only in a host
  preflight; the service connects only to receipt-pinned addresses and
  revalidates any address change before use;
- persist the approved endpoint identity and resolved transport class in the
  provider receipt.

### 4.2 Secret custody

`.env` stores non-secret provider metadata only. API keys, peer tokens, SSH
identities, and private CA material are write-only API inputs and are never
ordinary settings fields.

- Windows uses DPAPI scoped to the ODS service/user identity.
- macOS uses Keychain.
- Linux uses an available secret service or an ODS-owned `0600` secret file.
- Dedicated egress services receive secrets through read-only secret mounts.
- LiteLLM and dashboard-api call credential-free internal egress endpoints;
  plaintext secrets exist only in the memory of the process that must inject
  authentication and never in arguments, environment variables, diagnostics,
  or general container inspection.
- The host agent excludes provider secrets from settings responses,
  configuration and update backups, support bundles, and logs.
- Secrets never appear in generated YAML, command arguments, environment
  diagnostics, browser state, or CLI history.
- CLI secret entry uses a protected prompt or stdin.
- Rotation, expiry, revocation, lost-credential recovery, and uninstall cleanup
  are part of the provider lifecycle.

## 5. Runtime Architecture

### 5.1 Inference route

```text
ODS applications
      |
      | model=ods/current
      v
LiteLLM
      |
      | concrete remote model, no provider credential
      v
remote-provider-egress
      |
      | runtime DNS/TLS policy + outbound auth
      v
direct HTTPS/Tailscale endpoint or inference SSH transport
      |
      v
remote OpenAI-compatible runtime
```

`config/litellm/cloud.yaml` is generated with aliases for `ods/current`,
`default`, and the selected concrete model. Cloud mode always selects this
configuration even when `ODS_MODEL_SWITCHBOARD=enabled`; the switchboard YAML
is local-runtime-only.

### 5.2 Transport

All inference routes pass through `remote-provider-egress`. It is the component
that resolves the provider, pins approved addresses, enforces redirect and TLS
policy, injects provider authentication, and opens the connection. LiteLLM has
no provider credential and no direct egress route to the provider.

`direct` is preferred for:

- HTTPS endpoints;
- host-agent-attested Tailscale endpoints protected by tailnet identity, route
  verification, and ACLs;
- no plain LAN HTTP route.

Private CAs are imported through the write-only secret API, mounted read-only
for LiteLLM, and verified with the standard hostname. No TLS server-name
override is promised until the pinned LiteLLM HTTP client proves that behavior.

`ssh` uses Compose-managed transport services, not host supervisors. A generic
provider needs one inference transport. An ODS peer uses at most two isolated
transports: inference is reachable only by `remote-provider-egress`, and peer
control is reachable only by `remote-peer-egress` and dashboard-api.

Each transport:

- has no host port;
- listens only on the internal ODS network;
- mounts one dedicated identity and `known_hosts` file read-only;
- uses a digest-pinned minimal image, non-root UID, read-only root filesystem,
  `cap_drop: ALL`, `no-new-privileges`, seccomp, resource limits, and tmpfs-only
  writable state;
- runs without Docker socket, host networking, privileged mode, SSH agent,
  user SSH config, remote shell, agent forwarding, X11 forwarding,
  `ProxyCommand`, `LocalCommand`, or arbitrary SSH options;
- shares a dedicated internal network only with its assigned egress service;
- uses `ExitOnForwardFailure`, strict host checking, batch mode, keepalives,
  bounded exponential backoff, and clean signal handling;
- executes structured arguments with explicit host, user, port, identity, and
  known-host fields rather than accepting an uncontrolled SSH alias;
- constrains the forwarded destination to the approved provider endpoint;
- reports SSH/TCP health without inspecting model identity;
- exposes no secret or SSH command line through the Dashboard.

Model discovery and completion probes run above the transport and do not
restart a healthy tunnel.

### 5.3 ODS peer control plane

The peer protocol is additive to inference and is not required for a generic
provider.

Pairing creates a random token whose hash is stored on the remote node. Tokens
carry only these scopes:

- `models:read`
- `models:download`
- `models:activate`
- `models:delete`
- `operations:read`

The remote API delegates to existing model lifecycle transactions. It does not
duplicate download or activation logic.

Proposed versioned endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/peers/v1/pairing-codes` | Owner creates one scoped, expiring pairing code |
| `POST` | `/api/peers/v1/pair` | Exchange one-time pairing code for scoped token |
| `DELETE` | `/api/peers/v1/tokens/{id}` | Revoke a peer |
| `GET` | `/api/peers/v1/capabilities` | Report protocol and lifecycle capabilities |
| `GET` | `/api/peers/v1/models` | Sanitized inventory and active route |
| `POST` | `/api/peers/v1/models/download` | Start a catalog-backed download |
| `POST` | `/api/peers/v1/models/{id}/activate` | Start remote Switchboard activation |
| `DELETE` | `/api/peers/v1/models/{id}` | Delete a non-active model |
| `GET` | `/api/peers/v1/operations/{id}` | Read bounded async operation status |

Rules:

- pairing requires an authenticated owner session on the remote node;
- pairing-code creation requires owner reauthentication and CSRF protection;
- one-time codes contain at least 128 bits of cryptographic entropy, expire
  within five minutes, are consumed atomically once, and are attempt-limited;
- a code binds the intended peer, requested owner-approved scopes, remote node
  identity, and a client nonce/public key;
- a successful exchange returns immutable peer `instanceId`, protocol version,
  inference identity, scopes, and certificate/key fingerprint;
- stored pairing binds inference and control-plane routes to that same peer
  identity;
- tokens are shown once, stored hashed remotely, and redacted locally;
- pairing always uses authenticated encryption: HTTPS, verified Tailscale
  routing, or the pinned SSH transport;
- a token is a 256-bit opaque value with token ID, peer ID, audience, scope set,
  issuance, expiry, and revocation timestamps; hashes are compared in constant
  time;
- lifecycle-capable tokens are sender-constrained on every supported platform:
  the client signs method, path, body hash, timestamp, and nonce; the server
  enforces a short clock window and durable replay cache; mutation is disabled
  if sender proof is unavailable;
- Tailscale identity is transport evidence and does not replace the scoped
  token;
- delete refuses the active model and models used by an in-flight operation;
- every mutation requires an idempotency key bound to token, route, and body
  hash; accepted results are stored durably so retries cannot duplicate work;
- rate limits persist across restart and have separate per-account, per-token,
  per-source, and global budgets for pairing, reads, polling, and mutation;
- operation responses contain no filesystem paths or provider secrets;
- operation reads are limited to the creating peer or an authenticated owner;
- downloads accept catalog IDs only, never URLs or filesystem paths;
- model IDs are canonicalized and must remain inside configured model roots;
- disk budgets, model-size limits, allowlists, and operation concurrency are
  enforced server-side;
- delete rechecks active, pinned, default, and in-flight status while holding
  the lifecycle lock;
- peer routes expose typed model operations only, never generic host-agent
  delegation;
- dashboard-api validates peer credentials and calls a fixed allowlist of typed
  model operations; peer credentials never authenticate directly to host-agent;
- pairing-code creation, token listing/revocation, scope changes, and audit
  access require an owner session, owner reauthentication, and CSRF protection;
  peer credentials never authorize peer administration;
- client route changes only after the remote node returns concrete model
  identity and a completed inference proof.

## 6. Transaction Contract

Add dedicated host-agent endpoints for provider `configure`, `disable`,
`remove`, `status`, and operation recovery. Dashboard-api authenticates the
owner and proxies these typed operations. The host agent alone owns filesystem
mutation, Compose reconciliation, receipts, rollback, and crash recovery.

Provider operations contend with model download, activation, updates, repair,
and installer mutation through both the in-process lifecycle owner and the
existing platform-wide installer lock. Multi-file updates are journaled
sequences of individually atomic writes, not a claimed cross-file atomic
operation.

Configure:

1. Validate typed input.
2. Resolve and probe transport.
3. Authenticate and fetch `/v1/models`.
4. Verify the selected model is advertised.
5. Stage `.env` and rendered config in a private temporary directory.
6. Run schema validation and `docker compose config`.
7. Stage versioned secret writes without changing the active secret references.
8. Write a durable transaction journal with transaction ID, phase, previous
   receipt, active and staged secret references, file hashes, stack identity,
   and recovery action.
9. Snapshot the previous proven provider state without copying unrelated
   secrets.
10. Flush and atomically commit the staged configuration and secret references.
11. Reconcile the Compose stack through the canonical resolver.
12. Prove LiteLLM `/v1/models` and a real `ods/current` completion.
13. Probe every discovered installed LLM consumer.
14. Commit the schema-versioned provider receipt, close the journal, and only
    then delete superseded secret versions.

The journal exposes `staging`, `committing`, `proving`, `rolling-back`,
`restored`, and `degraded` states. Startup recovery resumes or reverses an
interrupted transaction. Any failure after step 8 restores the previous files,
stack, and route, then proves the restored route before returning a failure.
Rollback is never reported successful without inference and consumer proof.
Rollback restores the prior secret references before proving the prior route.

Remote activation plus client route adoption is a saga, not a cross-node atomic
transaction. The receipt records the remote operation and both proven states so
recovery can reconcile a remote model change that completed after the client
disconnected.

Disable/remove follows the same transaction in reverse. Remove additionally
revokes the peer token where reachable and deletes only transport-owned state.
If revocation is unreachable, removal records `revocation-pending`, warns the
owner, and retries instead of claiming full removal.

### 6.1 Audit record

Audit events use an allowlist: timestamp, actor/token ID, peer ID, action,
sanitized resource ID, operation ID, outcome, and verified source identity.
They exclude authorization headers, pairing codes, request bodies, upstream
error bodies, endpoint query strings, secret paths, environment dumps, and SSH
command lines.

## 7. Dashboard and CLI Contract

The Models view gains a Remote GPU action and a restrained status surface.

Setup fields:

- provider type;
- direct or SSH transport;
- endpoint or SSH destination;
- API key;
- discovered model selector;
- connection test;
- optional ODS peer pairing.

Status distinguishes:

- transport connected;
- provider authenticated;
- selected model available;
- inference proven;
- ODS peer paired;
- lifecycle permissions;
- last proof time and sanitized error.

Actions:

- configure;
- test;
- change selected remote model;
- pair/unpair ODS peer;
- disable;
- remove.

Download, activate, and delete controls appear only for a paired ODS peer with
the required scope.

Equivalent CLI:

```text
ods remote configure
ods remote test
ods remote status
ods remote models
ods remote use <model>
ods remote download <catalog-id>
ods remote delete <model>
ods remote disable
ods remote remove
```

Windows PowerShell, Linux Bash, and macOS Bash frontends call the same
dashboard-api/host-agent contract. They do not independently edit files.

## 8. PR Series

Every slice stays within the Model Switchboard's 20-production-file review
limit. Behavioral harness coverage lands with each behavioral PR instead of
waiting for the rollout PR.

### PR 0A: audited plan

Scope:

- add this plan and documentation index entry;
- preserve Lightheartdevs attribution.

Exit:

- plan has one architecture and one security review;
- no runtime behavior changes in the plan commit.

### PR 0B: Windows portability fixes

Scope:

- extract #1638's CRLF `jq.exe` normalization;
- pass native-Windows Python test paths as arguments instead of interpolating
  them into heredocs;
- add focused Windows Git Bash regressions;
- make no remote-provider behavior change.

Exit:

- both failures reproduce on current main and pass at the exact PR SHA;
- full env validation and Linux preflight suites remain green on POSIX;
- Lightheartdevs is credited as co-author.

### PR 1A: routing state and renderer convergence

Primary files:

- `.env.example`
- `.env.schema.json`
- `scripts/render-runtime-configs.py`
- `scripts/resolve-compose-stack.sh`
- `extensions/services/litellm/compose.yaml`
- `config/litellm/cloud.yaml`

Deliver:

- explicit `ODS_MODE`/remote-enable state machine;
- canonical `ods/current` remote alias;
- cloud-plus-switchboard selection fix;
- renderer ownership for every generated gateway consumer;
- one idempotent adapter for persisted application state;
- documented non-recursive behavior for direct-route exemptions.

Tests:

- invalid state combinations;
- renderer golden files;
- compose matrix on every platform;
- source-ownership and bypass-inventory contracts;
- local/cloud/hybrid/Lemonade no-regression.

### PR 1B: provider transaction, secrets, and direct HTTPS

Primary files:

- host-agent typed provider endpoints and journal;
- platform secret-store adapters;
- `remote-provider-egress` with connection-time URL, DNS, TLS, redirect, and
  authentication enforcement;
- renderer/provider service integration;
- doctor and support-bundle redaction.

Deliver:

- direct authenticated HTTPS and attested Tailscale inference;
- configure, disable, remove, status, and startup recovery;
- staged configuration with verified rollback;
- schema-versioned receipts and allowlisted audit records.

Tests:

- hostile model/URL/key values;
- auth required/rejected;
- TLS failure and private-HTTP policy;
- SSRF, DNS rebinding, redirect-to-metadata, TLS downgrade, hostname mismatch,
  CA rotation, and spoofed Tailscale identity;
- model missing;
- transaction interruption at every boundary;
- automatic rollback and degraded-state proof;
- secret scans across config, logs, backups, process state, and support bundles.

### PR 1C: dashboard-api proxy and recovery status

Deliver:

- owner-authenticated proxy to the typed host-agent provider operations;
- read-only sanitized provider, operation, receipt, and recovery status;
- no Dashboard mutation UI;
- API and harness coverage for direct remote inference through every discovered
  gateway consumer.

Tests:

- owner authentication and CSRF;
- concurrent operation conflicts;
- operation polling bounds;
- no generic host-agent proxy;
- no secret or upstream error-body disclosure.

### PR 2: universal private transport

Primary files:

- new inference and peer-control transport services under
  `extensions/services/remote-llm-transport/`;
- `remote-peer-egress` for sender-constrained control requests;
- manifest, Compose, entrypoint, health contract, and tests;
- provider renderer/resolver additions;
- doctor and support-bundle diagnostics.

Deliver:

- internal-only SSH sidecar;
- dedicated key and pinned host trust;
- preflight and bounded reconnect;
- clean disable/uninstall.

Tests:

- wrong host key;
- missing/unreadable identity;
- SSH auth rejection;
- occupied remote port;
- provider loading while SSH stays healthy;
- remote outage;
- tunnel process crash;
- DNS change;
- tailnet logout, ACL denial, peer identity change, and IPv4/IPv6 route change;
- key rotation;
- non-root UID, read-only filesystem, dropped capabilities, isolated networks,
  and no-new-privileges;
- disabled user SSH config, `ProxyCommand`, and arbitrary option injection;
- shell metacharacters in every configured field;
- identity/known-host symlink and permission rejection;
- attempted forwarding to an unapproved destination;
- repeated restart and remove.

### PR 3: Dashboard and CLI lifecycle

Primary files:

- dashboard Models/Settings components and API client;
- dashboard-api provider routes;
- `ods`, `ods.ps1`, and macOS CLI routing;
- docs and user-facing diagnostics.

Deliver:

- configure, test, status, select, disable, and remove;
- capability-aware action visibility;
- accessible progress and errors;
- equivalent workflows on all supported OSes.

Tests:

- frontend unit and Playwright behavior;
- API authentication and CSRF contracts;
- CLI parity;
- refresh/restart persistence;
- no secret in browser payloads, logs, or screenshots.

### PR 4: authenticated ODS peer lifecycle

Primary files:

- dashboard-api peer auth/router/service/tests;
- host-agent scoped delegation;
- model operation projection;
- Dashboard and CLI lifecycle actions;
- pairing and revocation docs.

Deliver:

- owner-approved pairing;
- remote inventory, download, activate, delete, and operation status;
- proof-backed route update;
- revocation and audit records.

Tests:

- expired/replayed pairing code;
- pairing brute force and simultaneous redemption;
- missing/wrong scopes;
- token revocation;
- cross-peer revocation refusal and peer-token denial on every administrative
  endpoint;
- wrong audience, sender signature, timestamp, nonce replay, and scope
  escalation;
- mutation rate limits;
- duplicate idempotency key with the same and a changed body;
- concurrent operations;
- client timeout after accepted mutation;
- delete-active refusal;
- disk-full download, encoded traversal, symlink escape, and
  delete/activate races;
- activation failure and remote rollback;
- peer offline during operation;
- revocation during mutation and operation-ID enumeration;
- hostile model IDs;
- no arbitrary host-agent access.

### PR 5: permanent fleet gate and rollout

Deliver:

- harness remote-provider setup and teardown;
- direct and SSH lanes;
- generic inference-only and paired ODS lanes;
- per-consumer proof after every route/model change;
- immutable run receipts and release-confidence integration;
- docs, operations guide, and rollout flag.

The default remains off until the release gate below passes.

## 9. Validation Program

### 9.1 Fast iteration

Every product PR runs:

- schema, generated-config, and Compose contracts;
- dashboard-api unit/integration suite;
- Dashboard unit, lint, and production build;
- PowerShell parse and Windows contract tests;
- Linux/macOS shell tests;
- security negative tests;
- full existing model-switchboard transaction tests;
- `git diff --check`.

### 9.2 Two-node integration

Use disposable ODS client/provider pairs to prove:

1. Generic OpenAI provider, direct authenticated HTTPS.
2. Generic OpenAI provider through SSH transport.
3. ODS peer direct over Tailscale/private HTTPS.
4. ODS peer through SSH transport.
5. Local-to-remote, remote-model-A-to-B, and remote-to-local transitions.
6. Failure at each transaction boundary restores the prior proven route.
7. Process kill or reboot at each journal phase recovers without a false-green
   receipt.
8. Corrupted receipt, failed rollback proof, unreachable revocation, and
   client/remote state divergence remain visibly degraded until repaired.

### 9.3 Application proof

After every configure, model change, restart, and restore, discover installed
consumers and require functional round trips through:

- Hermes and ODS Talk;
- Open WebUI with authenticated model and chat calls;
- Perplexica;
- OpenCode;
- OpenClaw where installed;
- every additional discovered LLM consumer.

HTTP liveness, model listing, or configuration inspection alone is not a pass.

### 9.4 Fleet matrix

At one frozen product SHA and one frozen harness SHA:

| Client | Provider | Transport | Capability | Models | Applications | Required artifact |
|---|---|---|---|---:|---|---|
| `windows-laptop` | `tower2` Linux/NVIDIA | attested Tailscale direct | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, discovered apps | Playwright, consumer JSON, peer audit |
| `strixy` | `strix-halo` Windows/AMD | SSH sidecar | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, discovered apps | Playwright, transport JSON, peer audit |
| `tower2` | `dgx-gpu01` Linux/NVIDIA ARM | SSH sidecar | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, OpenClaw if installed | Playwright, transport JSON, peer audit |
| `spark` | `strix-halo` Windows/AMD | attested Tailscale direct | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, discovered apps | Playwright, consumer JSON, peer audit |
| `m5-mbp` | `tower2` Linux/NVIDIA | SSH sidecar | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, discovered apps | Playwright, transport JSON, peer audit |
| `m5-mbp` | `strix-halo` Windows/AMD | attested Tailscale direct | ODS peer lifecycle | 6 | Hermes/Talk, Open WebUI, Perplexica, OpenCode, discovered apps | Playwright, consumer JSON, peer audit |

If a provider lacks six viable catalog models, the release is blocked until
six are researched, added with capability metadata, and proven. Models should
not overlap between lanes where the hardware permits a wider sample.

Each lane proves:

- fresh install and upgrade on both the client and provider;
- provider-exclusive scheduling for every model-mutating lane;
- remote setup entirely through the user-facing UI;
- six viable remote models;
- download, activate, use through all applications, restore, and delete;
- restart between cycles;
- the exact transport declared in the lane table;
- network interruption and recovery;
- client sleep/reboot where applicable;
- provider restart;
- key rejection and recovery;
- disable and local-route restoration;
- `ods update`, `doctor`, `repair`, reinstall, and uninstall;
- no regression to a fresh default local installation.

Generic providers run separate Windows, Linux, and macOS inference-only lanes
over authenticated HTTPS and SSH. Those lanes require the same application
proof but must not expose download, activation, or delete controls. Browser
applications require Playwright interaction; API probes are accepted only for
non-browser consumers such as OpenCode.

### 9.5 Release stamp

Release-ready requires:

- all required hosts green at one immutable SHA pair;
- two consecutive complete runs;
- no hand-edited gate input;
- no undisclosed skipped application;
- no unproven host;
- no validator relaxation without a logged false-red reproduction and negative
  self-test audit;
- product PR equals its pushed branch and harness equals its pushed branch;
- post-merge rerun on `main`.

## 10. Rollout and Rollback

Feature states:

1. backend/API present, hidden;
2. opt-in CLI/API;
3. opt-in Dashboard;
4. paired ODS lifecycle opt-in;
5. generally available after the fleet stamp.

Emergency rollback disables `REMOTE_LLM_ENABLED`, runs the canonical renderer,
restores the previous proven provider receipt, and recreates the resolved stack.
It never deletes local or remote model artifacts.

## 11. Execution Ledger

Each implementation checkpoint records:

- product branch and SHA;
- dependent PR SHAs;
- harness branch and SHA;
- tests run and exact result;
- affected hosts;
- product, harness, or environment classification for every red;
- hand repairs, if any;
- remaining unproven requirements.

No checkpoint may be described as complete while its acceptance evidence is on
an older SHA.

## 12. Definition of Done

This feature is complete only when:

- remote inference is a canonical provider mode, not a configuration island;
- every gateway consumer follows `ods/current`, and every declared direct
  exemption passes its documented non-recursive remote-provider contract;
- direct and SSH transports recover without route corruption;
- generic providers are honestly inference-only;
- paired ODS nodes securely support remote model lifecycle;
- configure, switch, disable, remove, update, repair, reinstall, and uninstall
  are transactional and proven on Windows, Linux, and macOS;
- secrets remain absent from API responses, logs, backups, support bundles, and
  screenshots;
- fresh local installs remain unchanged;
- the permanent fleet harness enforces these behaviors;
- the replacement PR stack is independently reviewed, merged, and green on
  post-merge `main`;
- PR #1638 is closed as superseded with Lightheartdevs credited for the original
  remote-workstation feature.
