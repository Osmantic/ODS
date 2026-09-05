# Pixel in ODS

Pixel is ODS's preferred conversational agent on the narrow host and license
path described here. It is exposed as the default `pixel/default` model in
Open WebUI and as a dedicated **Pixel** app in the ODS Dashboard toolbar.
Hermes remains installed by default as the portable fallback and rollback
agent. Deprecated OpenClaw and the OpenCode coding UI remain separately
selectable; this integration does not delete either one.

Pixel does not maintain a model allowlist and ODS does not block chat or tool
use behind a "Pixel-ready" verdict. Every model or remote provider that is
callable through the active ODS Switchboard route is callable through Pixel.
ODS still applies its ordinary hardware-fit and inference-readiness checks when
it chooses a fresh install's default model; any agent-quality measurements are
advisory capability evidence, not an access gate. Hermes is the rollback agent
when the Pixel runtime, host, license, or authenticated gateway is unavailable,
not a substitute selected merely because a callable model scored poorly on an
agent probe.

The harness adapts its prompt and Tool Search budget to the active model while
retaining the same mechanical authority boundaries, cancellation, receipts,
verification, and rollback. A small model may need shorter tasks, more focused
recovery turns, or produce less capable results than a larger model, but the UI
must remain usable and honest about those limits. ODS-managed cloud, hybrid,
local, and external Lemonade routes all bind Pixel through the same
authenticated LiteLLM gateway used by other ODS consumers. Gateway readiness
proves that the route is callable; it does not claim that every underlying
model has equal intelligence or tool-use skill.

## Legal and release boundary

Pixel's repository currently uses a proprietary, all-rights-reserved license.
ODS does not grant a right to install or use Pixel. Set
`PIXEL_LICENSE_ACCEPTED=true` only after a separately negotiated written
agreement authorizes the relevant installation. A public ODS release cannot
legally deliver Pixel to every installer until the Pixel copyright holder
publishes a compatible license or grants the required distribution and use
rights.

This technical integration therefore fails closed:

| Request | Qualified host | Written authorization acknowledged | Result |
|---------|----------------|------------------------------------|--------|
| `ENABLE_PIXEL=auto` (default) | Yes | Yes | Pixel is the default agent |
| `ENABLE_PIXEL=auto` | No | Any | Hermes fallback; ODS installation continues |
| `ENABLE_PIXEL=auto` | Yes | No | Hermes fallback; ODS installation continues |
| `--pixel` | Yes | Yes | Pixel is required and installed |
| `--pixel` | No | Any | Installer stops before changing the agent route |
| `--pixel` | Yes | No | Installer stops before changing the agent route |
| `--no-pixel` | Any | Any | Pixel route is disabled; Hermes remains available |

The environment value must be exactly `true`. There is no click-through or
implicit acceptance.

## Host eligibility

Pixel is selected only on:

- Ubuntu 24.04 LTS or Debian 12;
- Linux with `systemd` as PID 1;
- a native Linux host or WSL2 (WSL1 is rejected); and
- an ODS-managed local, cloud, hybrid, Lemonade, or external OpenAI-compatible model route.

ODS supports more platforms than Pixel. macOS, Windows-native, other Linux
distributions continue to install ODS and use Hermes. External Ollama, LM Studio,
and generic OpenAI-compatible endpoints are bound through authenticated LiteLLM;
Pixel uses the exact selected upstream model behind `ods/current`. For a generic
local/LAN endpoint, select `--external-llm-provider openai-compatible` together
with `--external-llm-url` and `--external-llm-model`. This reuse path is for
credential-free upstreams; credentialed remote providers use the Remote GPU
provider workflow. Do not put credentials in an endpoint URL.
These are ODS capability gates, not a reduction of the ODS support matrix.

## Architecture

```text
Browser
  | no Pixel credential
  +--> Open WebUI ------------------------------+
  |                                             |
  +--> Dashboard /pixel -> nginx -> dashboard-api
                                                |
                         narrow generated edge key
                                                v
                                  pixel-edge container
                                  - no published port
                                  - read-only filesystem
                                  - fixed pixel/default model
                                                |
                                  mode-0660 Unix socket
                                                v
                                  pixel-ingress.service
                                  - unprivileged Pixel owner
                                  - no listening TCP port
                                  - request allowlist and bounds
                                                |
                          owner-private gateway token injected here only
                                                v
                           Pixel/OpenClaw gateway on 127.0.0.1:18789
                                                |
            +--------------------+----------------------+--------------------+
            |                    |                      |                    |
            v                    v                      v                    v
 exact-digest ODS plugin   Operations plugin     sandbox coding      ods-gateway/
 eight typed tools        typed request spool    and public web      ods/current
            |                    |                                           |
            v                    v                                           v
 /run/ods-pixel/         isolated root-owned                         authenticated
 ods-status.json         Operations Broker                          LiteLLM loopback
                              |                                          |
                              v                                          v
                   root-only quarantine                     active ODS model/
                              |                              provider route
                              v
                   fixed artifact-promoter
                   socket -> owner workspace
                             |
                             v
                   create-only static snapshot
                   -> site-*.localhost on-host origin
                   -> group-scoped Unix HTTP relay
                   -> authenticated Dashboard remote route
                   -> sandboxed Dashboard panel/new tab
```

The Open WebUI and Dashboard paths converge at `pixel-edge`. The browser never
receives either the edge key or Pixel's operator/gateway token. The edge key
cannot call the loopback gateway directly. With the rest of the owner's home
hidden, the host ingress bind-mounts only Pixel's exact owner-private gateway
config read-only into its private runtime namespace, injects its token only on
the final loopback hop, strips inbound headers, forces `openclaw/default`, and
bounds request, response, stream, and timeout sizes.

The edge and host ingress health checks fail closed unless the next hop is
actually ready. Open WebUI is not allowed to advertise `pixel/default` while
the private ingress is unavailable.

### Operations capability and exact downloads

ODS enables Pixel's `engineering-operator` capability profile but explicitly
keeps email, Calendar, social, Web Courier, and Frontier limbs disabled until
their credentials and qualification paths are configured. The Operations limb
is enabled through Pixel's isolated broker, not by granting the model a host
shell, Docker socket, or privileged credential.

The installer writes an owner-private, mode-`0600` policy at
`$INSTALL_DIR/data/pixel/operations-policy.json`. Pixel copies the reviewed
policy into root-controlled broker custody. The gateway can write typed request
and cancellation records and read sanitized inventory, event, and result
projections, but it cannot read broker policy, approvals, credentials, plans,
leases, private state, or SSH material.

The ODS management marker binds both the onboarding document and the exact
Operations-policy bytes. A policy change therefore forces Pixel to regenerate
and reinstall its broker configuration even when the immutable Pixel release
itself is unchanged. Installation fails unless the resulting root-owned,
mode-`0640` broker policy is byte-for-byte equal to the owner-private source;
an installer success message is never treated as sufficient custody evidence.

The default policy provides seventeen read-only named host actions. Identity
and platform observations use `host.identity`, `host.kernel`,
`host.architecture`, `host.platform`, and `host.os-release`. Broad machine
exploration combines identity, kernel, platform, and operating-system evidence
with `host.uptime`, `host.processes`, `host.services`, `host.cpu`, `host.gpu`,
`host.memory`, `host.storage`, `host.network-addresses`,
`host.network-routes`, `host.listening-ports`, and `host.tailscale`. It does not
require the redundant
`host.architecture` action because both platform and CPU observations already
carry architecture evidence. A narrow architecture-only request still requires
the dedicated action. In a broad inventory that already contains a structurally
validated `host.cpu` receipt, the verifier may satisfy an explicit architecture
facet from that receipt's exact `Architecture:` field instead of discarding the
otherwise complete report; malformed or absent architecture data still fails
closed.

The seventeenth action, `host.network-peer`, activates only when the owner
positively names one private LAN or Tailscale peer and asks for a connectivity
check. It resolves that one name or private address and performs bounded ICMP
and TCP checks against at most eight explicit or standard service ports. It
cannot scan a range, follow a URL, reach a public or loopback address,
authenticate, execute a remote command, guess credentials, or mutate either
machine. A separate owner-requested SSH command still uses the immutable,
externally approved host-command route.

These actions intentionally expose useful host state without exposing a raw
privileged shell. Process observations omit command arguments and environment
values; service observations omit service environments; storage observation
reports capacity rather than file contents; and network observation reports
interfaces, routes, and listening endpoints without process arguments or
credentials. Uptime reports only elapsed host uptime, user count, and the one,
five, and fifteen minute load averages. The actions execute from the broker's
protected state directory, so the broker still does not need access to the
owner's home directory. Raw
host shell remains disabled in Operations; Pixel's sandbox shell remains
available for ordinary workspace work. Workspace file tools continue to
operate only inside Pixel's sandbox; broader owner-file exploration requires a
separately audited typed read bridge and is not implied by these host inventory
actions.

The policy also permits bounded automatic
staging from a finite set of common public artifact hosts:

- `example.com` for deterministic qualification;
- GitHub and `githubusercontent.com`;
- Hugging Face and `hf.co`;
- npm, Node.js, PyPI, and `pythonhosted.org`.

Other public domains do not become silently trusted. The broker compiles them
as approval-required immutable plans. Credential-bearing URLs, non-HTTPS URLs,
raw or private addresses, DNS rebinding, redirects outside reviewed domains,
oversized content, digest mismatches, and emergency-pause or cancellation
events fail closed.

Successful downloads first enter a root-only, non-executable mode-`0600`
quarantine under `/var/lib/pixel-ops-broker/artifacts/<job-id>/`. A submission
receipt is not success. After a matching terminal broker result reports
`succeeded`, Pixel may call the separately bounded
`pixel_ods_download_promote` tool with that exact job ID, filename, requested
HTTPS source, optional expected digest, and a safe relative workspace path.

The root-custodied artifact promoter accepts only Pixel's owner UID over a
mode-`0600` Unix socket. It re-reads the broker result, reopens the quarantined
file without following symlinks, verifies the source, filename, byte count,
SHA-256 digest, and stable file identity, and create-only publishes the same
bytes into Pixel's workspace as an owner-owned, mode-`0600`, non-executable
file. It has no network, shell, Docker, credential, overwrite, or arbitrary
source-file capability. Parent traversal, symlink parents, broker-result drift,
source or digest mismatch, and existing targets fail closed.

ODS accepts an exact-download claim only after the promoter returns a
host-authoritative final receipt containing the workspace path, exact byte
count, SHA-256 digest, bound HTTPS source, `executable=false`, and
`overwritten=false`. A transformed `web_fetch` response, a model-created
workspace substitute, or a broker staging receipt can never satisfy this
contract.

### Cancellation and sandbox execution

The Dashboard cancel path is an execution boundary, not only a UI gesture.
The ODS plugin maps the opaque chat user to one exact active OpenClaw run. Each
`exec` command in that run is launched in its own process group by an
owner-installed wrapper. Cancellation atomically creates one zero-byte marker
named with the run ID's SHA-256; the sandbox sees the owner-private control
directory read-only, terminates only that process group with `TERM` and then
`KILL`, and returns status 130. The gateway run is drained independently, the
marker is removed after a short bounded delay, and the edge closes a successful
cancelled stream with only `[DONE]`. A different concurrent chat and its
process group are not touched.

OpenClaw requires `dangerouslyAllowExternalBindSources=true` for this one
host-to-sandbox bind. ODS does not treat that opt-in as general bind authority:
the installer accepts exactly
`~/.openclaw/.ods-exec-control:/run/pixel-ods-control:ro`, validates the source
owner, type, link count, and exact `0700`/`0500` modes, recreates the Pixel
sandbox after lifecycle changes, and rejects every additional bind. OpenClaw's
security audit will still report the generic dangerous-flag warning. Removing
the flag without removing the bind makes cancellation fail closed; adding a
second source is outside the ODS contract.

The dashboard route also handles explicit requests to open private HTTP(S)
URLs at the edge. It returns one exact local explanation without forwarding the
request to Pixel or the model, while ordinary conversation, public URLs, and
text that merely mentions a private development URL continue through normally.
Private browsing requires a separately reviewed and approved capability; the
public-web tools and shell cannot be used as substitutes.

## Install

From an authorized Ubuntu 24.04 or Debian 12 host:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS/ods
PIXEL_LICENSE_ACCEPTED=true ./install.sh --pixel
```

Omit `--pixel` to use automatic selection. Explicit `--pixel` is recommended
for qualification because it turns an unexpected fallback into a visible
installer failure.

The installer pins Pixel to an immutable full commit. To qualify an
owner-controlled local Pixel checkout, place it under a secure directory you
own and set all three source values:

```bash
PIXEL_LICENSE_ACCEPTED=true \
PIXEL_SOURCE_DIR=/home/me/src \
PIXEL_SOURCE_URL=/home/me/src/Pixel \
PIXEL_SOURCE_REF=<40-character-commit> \
./install.sh --pixel
```

The canonical remote URL is the only remote source accepted. A local source
must be a clean Git checkout below `PIXEL_SOURCE_DIR`; the owner directories
must not be group- or world-writable. Remote Git credential prompts are
disabled and source operations are bounded, so an inaccessible private source
fails instead of hanging the installer. Authorized users without configured
non-interactive Git access should use the local-checkout form above.

## User experience

After a successful install:

1. Open `http://localhost:3000`. New Open WebUI chats default to
   `pixel/default`; the ordinary ODS model remains selectable.
2. Open `http://localhost:3001/pixel`, or choose **Pixel** in the Dashboard
   toolbar, for the dedicated streaming agent UI.
3. Hermes remains at its authenticated proxy URL shown by the installer.
4. OpenCode remains an independent coding UI when enabled.

Pixel follows the active ODS model without a qualification allowlist. Every
callable ODS chat model can be used in the dedicated Pixel UI and through
`pixel/default`. Qualification affects recommendation and the displayed
reliability tier only: a model that has not passed the demanding multi-step
agent replay uses Pixel's **adaptive** route, while the composer remains enabled.
Model intelligence may change the quality, speed, tool judgment, and length of
work it can complete, but Pixel's broker permissions, approvals, receipts, and
cancellation boundary do not change with that label.

Open WebUI search-query generation uses the ordinary ODS model route, not Pixel,
to avoid recursive agent routing. Automatic title, tag, and follow-up
generation is disabled while Pixel is active because those cosmetic jobs would
otherwise compete with the agent for the same local inference slot.
The dedicated Dashboard keeps the current bounded conversation and opaque chat
ID in that browser's local storage so a reload or Dashboard restart resumes the
same Pixel session. It stores no gateway credential. **New chat** replaces that
local pointer with a fresh opaque ID; older OpenClaw session files remain under
the owner's normal Pixel lifecycle and are not exposed to the browser.
For the default no-think mode, the managed Pixel route omits OpenClaw's Qwen
thinking compatibility switch and declares reasoning inactive. The independently
pinned llama.cpp no-think setting then remains authoritative on every model
request. This avoids an OpenClaw 2026.6.33 compatibility path that treats the
literal effort `off` as truthy and can otherwise spend the whole output budget
in hidden reasoning after a tool call. This policy does not broaden the tool
allowlist or make custom tools replay-safe.

### Model swapping

Pixel follows the same model gateway as the rest of ODS. Its private OpenClaw
provider is `ods-gateway`, bound only to authenticated LiteLLM on
`127.0.0.1`. It always requests ODS's stable `ods/current` alias. Every
supported LiteLLM mode publishes that alias, and the Model Switchboard can
move its concrete backend without changing Pixel's persisted route. Provider
credentials, arbitrary URLs, and browser-supplied model IDs cannot rewrite
that route.

Using the Dashboard Models page or `ods model swap <tier>` keeps the stable
alias while transactionally updating Pixel's concrete-model display metadata,
context window, output limit, reasoning capability, and model-family
compatibility policy after the new runtime and downstream routes pass their
proofs. Cloud, hybrid, and external Lemonade modes can change the route behind
the same alias without teaching Pixel a provider-specific endpoint. The Pixel
gateway is restarted and verified before the transaction commits. The public
Open WebUI identity remains `pixel/default` throughout.

The Dashboard Remote Provider page uses that same stable alias. A direct
provider becomes active only after its egress probe, a real LiteLLM completion,
and Pixel reconciliation all succeed. An SSH-backed provider remains visibly
staged until the managed tunnel proof completes. The operator supplies the
provider's context window, output limit, and reasoning capability so broad
model swaps update Pixel's runtime policy rather than inheriting stale local
limits. Repeated route tests verify the live LiteLLM and Pixel consumers; they
do not trust an old readiness marker. Disabling the provider restores the exact
pre-provider ODS mode and Pixel contract while retaining a private, non-secret
saved route profile and the existing secret custody. `ods remote-provider
enable` then performs a new direct or SSH proof before Pixel moves back to that
route; a failed SSH proof automatically pauses it again. Removing the provider
restores the same local contract but also deletes the saved profile and secrets.

If Pixel reconciliation fails, ODS restores and proves both the previous model
runtime and Pixel route. The first upgrade from the legacy direct
`ods-local`/llama.cpp binding is also transactional: the rollback contract
captures the exact prior provider, model limits, reasoning mode, and route.
The LiteLLM key stays only in owner-private mode-`0600` Pixel state and is never
sent to the browser or written to a public config. A stopped or unmanaged Pixel
is never adopted. Model
family changes are supported: an explicitly reasoning-enabled Qwen route gets
the Qwen chat-template compatibility policy and a real non-off effort; that
policy is removed for no-think Qwen routes and when a non-Qwen model is
activated. Pixel's
managed agent route supports OpenClaw's 4096-token minimum, including the
bundled 8K T0 profile. Models below 16K run in a deliberately constrained
adaptive mode, where task complexity and reliability vary with the available
prompt budget; they are not excluded. An advanced custom activation below 4K
is rejected before any model state changes. Below 32K, ODS lowers Pixel's
output ceiling to one quarter of the context; at 32K and above it allows up to
4096 output tokens. ODS derives OpenClaw's compaction reserve from that output
ceiling and enables a half-window reserve floor for 8K-31K adaptive profiles,
where dense tool transcripts can otherwise outrun OpenClaw's character-based
estimate. The retained recent tail is capped at one sixteenth of the selected
context (and 20K tokens globally), so compaction always removes real history
instead of recording a no-op when the runtime's fixed 20K default exceeds the
whole model window.

Generic `EXTERNAL_LLM_URL` reuse remains outside this contract because it
bypasses the managed LiteLLM route. Automatic selection uses Hermes for that
case, and explicit `--pixel` fails visibly instead of implying provider parity.

## Bounded ODS tools

The default ODS integration exposes nine narrowly scoped tools to Pixel. They
remain available to every callable model through the same policy-filtered Tool
Search catalog; model qualification labels describe observed quality and never
act as a capability gate:

- `pixel_ods_status` returns the sanitized overall ODS state, an explicit
  application count, and allowlisted application states.
- `pixel_ods_apps_list` returns the same explicit count and allowlisted
  application inventory in an app-oriented shape. User-facing entries include
  an allowlisted purpose and the configured localhost URL, so Pixel can name
  and link ODS applications without guessing default ports. The count avoids
  asking small local models to infer it from the array.
- `pixel_ods_host_observe` runs one exact, read-only host observation through
  the external Operations Broker and returns its terminal receipt. It has no
  broker-target, command, mutation, approval, or raw-shell input. Its only
  optional dynamic inputs are the sanitized one-peer name/private address and
  bounded port list required by `host.network-peer`.
- `pixel_ods_host_command_propose` submits one owner-requested command to the
  fixed `ods-host` target and waits internally for the broker's immutable plan
  or terminal receipt. Its only input is the exact command; it cannot approve
  the plan, and an `awaiting-approval` receipt proves that no command ran.
- `pixel_ods_evidence_report` and `pixel_ods_evidence_readback` are guard-only
  controls for an owner-requested report tied to verified Operations evidence.
  On compact models the guard may complete that exact report atomically after
  receipt validation: it accepts one owner-named workspace-relative path,
  generates the body mechanically from the validated receipt, rejects unsafe
  parents and multiply-linked destinations, writes mode `0600`, and verifies
  readback through the same file descriptor. It cannot write model-authored
  content or provide a generic host-filesystem capability.
- `pixel_ods_web_extract` uses OpenClaw's strict public-web network guard to find a
  distinctive literal method or section name anywhere in a long public page.
  A bounded fallback accepts two or three keywords only when they co-occur in
  one evidence window. The tool returns only that bounded, explicitly untrusted
  window. It is the targeted fallback when the normal `web_fetch` prefix is
  truncated before the requested detail; local, private, single-label,
  credentialed, and raw-IP destinations remain blocked.
- `pixel_ods_download_promote` can publish one already-successful, exact broker
  download into one new relative path in Pixel's workspace. It cannot fetch,
  transform, overwrite, execute, or select an arbitrary host file.
- `pixel_ods_workspace_preview` publishes one owner-requested static site from
  a workspace-relative directory. The host service accepts only bounded,
  owner-controlled, non-linked HTML/CSS/JavaScript/image/font files, requires
  `index.html`, copies them into a create-only content-addressed snapshot, and
  performs an HTTP readback before returning a receipt. The private ingress
  carries that exact receipt in a structured terminal frame; the Dashboard
  never opens a URL parsed from model prose. The Pixel portal automatically
  shows the snapshot in a side panel with a script-capable iframe. On the ODS
  host, each content-addressed snapshot receives its own `site-*.localhost`
  origin, and the host rejects a request whose origin hostname does not match
  the snapshot path. That path retains ordinary origin-scoped browser storage.
  When the Dashboard itself is opened on another private LAN or Tailscale
  client, it uses `/pixel-preview/<site-id>/` on that same Dashboard authority.
  Nginx authenticates the hop to the internal-only Pixel Edge, Pixel Edge reads
  through a group-scoped Unix socket rather than opening a host port, and the
  returned document receives an enforced CSP sandbox without
  `allow-same-origin`. The remote document can run its scripts and load its own
  immutable local assets, but it cannot inherit Dashboard cookies, DOM, or
  storage authority; the same CSP applies to the new-tab view. Both routes
  block outbound connections, form submissions, popups, top-level navigation,
  downloads, camera, microphone, and geolocation. Starting a
  development server inside Pixel's disposable sandbox is explicitly rejected
  because that port is not the owner's browser-facing host.

  Every creative artifact follows the same model-authored workspace path.
  Open-ended demos, games, task boards, animated SVGs, voxel scenes, named
  designs, and arbitrary sites must be written by the active model before the
  preview adapter can publish them. ODS contains no creative templates or
  generated starter bytes. The adapter accepts only the existing relative
  directory, so safety enforcement, static validation, snapshot isolation, and
  HTTP readback cannot be confused with authorship. Pixel may author either a
  self-contained `index.html` or a richer set of local files; ODS imposes no
  creative-size ceiling and publishes only the exact completed snapshot.

The status tools read only `/run/ods-pixel/ods-status.json`. The plugin does not
receive the Docker socket, Dashboard API key, Open WebUI key, host shell, or ODS
operator credentials. The targeted extractor receives only a public URL and
literal query and delegates transport to OpenClaw's DNS-pinned, redirect-aware
web guard. The projection accepts only its documented schema, service and app
enums, owner, mode, size, timestamp freshness, UTF-8, and fixed path. It rejects
symlinks, replacement races, unknown keys, duplicate apps, stale or future
timestamps, and group/world-writable files.

`web_fetch` and `pixel_ods_web_extract` return transformed, safety-marked page
evidence, not the origin server's exact response bytes. Pixel must not save that
representation as a byte-exact download or attribute its size or digest to the
remote object. For an exact-byte request, the loop guard binds one HTTPS URL and
one safe relative workspace path, then requires `pixel_ops_download_stage`, a
terminal `pixel_ops_job_wait`, and `pixel_ods_download_promote` in that order. It
rejects substitute writes and blocks a success claim without the promoter's
matching final receipt. If staging or promotion is unavailable, the request
fails closed without creating a substitute artifact; ordinary page research
remains available.

Adding an ODS action is a security-boundary change. It requires a new explicit
tool contract, policy and authorization design, adversarial tests, and fresh
install/rollback qualification; do not broaden the projection reader into a
generic shell, HTTP, Docker, or filesystem tool.

OpenClaw's security audit also reports its generic `models.small_params`
critical when the active local model is at or below 300B and public web tools
are enabled. The qualified ODS posture is a personal assistant for one trusted
operator, with sandbox mode `all`, bounded tool loops, public-only destinations,
and page content explicitly treated as untrusted evidence. It is not a
multi-tenant or hostile-input deployment. Operators who expose Pixel to
untrusted users must deny `group:web` and `browser` for that model (losing web
research) or qualify a stronger model and threat model first; do not describe
the generic audit finding as green or suppressed.

## Installer ownership and upgrades

ODS writes `~/.config/ods/pixel-managed.json` with mode `0600`. The marker is
created before Pixel is changed and moves from `installing` to `ready` only
after Pixel verification, systemd activation, and private-ingress health pass.
The marker records that no active Pixel release or runtime attestation existed
before ODS created it. After Pixel verification it binds the verified contract,
live config, exact active release identity and install-manifest hashes, release
version, and validated live sandbox image ID while remaining `installing`, so
an interrupted ingress setup can safely verify and reuse the active release on
retry without claiming readiness. The ready marker also binds the exact Pixel
source revision and a domain-separated SHA-256 of the deterministic ODS
onboarding contract, including the approved ODS plugin tree digest, plus a
canonical hash of the verified live OpenClaw configuration. When all bindings
match exactly, a rerun skips Pixel's
same-release apply transaction, verifies the exact source, and reinstalls the
ODS ingress. If only the ODS extension contract changed while the exact
verified Pixel source and newly planned canonical runtime configuration still
match the live configuration, ODS refreshes OpenClaw's persisted plugin
registry, verifies the exact plugin root and seven-tool descriptor in both the
persisted and current registry views, then restarts and verifies the gateway.
Pixel source drift or runtime-configuration drift takes the ordinary
configure/plan/apply path and remains fail closed.

The managed runtime preserves the upstream default workspace-bootstrap ceilings
(`bootstrapMaxChars=32000` and `bootstrapTotalMaxChars=96000`). At 32K context
and above, Pixel uses a `14000`-character per-file and `36000`-character total
ceiling so the shipped `AGENTS.md` and `TOOLS.md` contracts remain available in
full while unrelated workspace material stays bounded. Below 32K, injecting
those verbose files would overflow a fresh turn before the model could call a
tool. ODS therefore uses OpenClaw's supported `contextInjection=never` mode for
that agent route and supplies a concise safety/execution core plus only the
route-specific contract needed by the owner's current request. Tool availability,
sandboxing, approvals, and broker authority do not change with context size.

ODS explicitly enables OpenClaw's local-model lean surface for Pixel. The
stable `ods/current` alias prevents OpenClaw from inferring that the provider is
local, so without this setting even a lightweight laptop model receives every
direct tool schema. Lean mode retains the same capabilities behind the
`tool_search`, `tool_describe`, and `tool_call` structured controls while
reducing the first-turn schema burden. Pixel Edge also adds a
short, trusted delivery instruction next to each current owner message. This
prevents small models from selecting OpenClaw's asynchronous `NO_REPLY`
sentinel in interactive chat; it does not fabricate an answer or retry a
possibly side-effecting tool request.

ODS will not adopt or overwrite an ambient Pixel/OpenClaw deployment. If it
finds an existing OpenClaw configuration, Pixel gateway environment, Pixel
onboarding record, active release, runtime attestation, or gateway systemd unit
without its management marker, the installer stops and leaves that deployment
untouched.

## Configuration reference

| Variable | Default / owner | Meaning |
|----------|-----------------|---------|
| `ENABLE_PIXEL` | `auto` | `auto`, exact `true`, or exact `false` selection |
| `PIXEL_LICENSE_ACCEPTED` | unset/false; operator | Exact acknowledgement after written authorization |
| `PIXEL_SOURCE_URL` | canonical Pixel GitHub URL | Canonical remote or validated local checkout |
| `PIXEL_SOURCE_REF` | ODS-pinned full SHA | Immutable Pixel source revision |
| `PIXEL_SOURCE_DIR` | empty | Secure owner-controlled root for a local checkout |
| `PIXEL_OPENWEBUI_KEY` | generated; installer | Narrow Open WebUI/Dashboard-to-edge key; secret |
| `PIXEL_INGRESS_RUNTIME_DIR` | `/run/ods-pixel` | Host directory containing only the socket/projection |
| `PIXEL_PREVIEW_RUNTIME_DIR` | `/run/ods-pixel-preview` | Host runtime directory containing the group-scoped immutable-preview relay socket |
| `PIXEL_INGRESS_GID` | generated; installer | Numeric `ods-pixel` group used by the edge container |
| `PIXEL_PREVIEW_PORT` | `9437` | Dedicated loopback-only static preview service; every snapshot uses an isolated `site-*.localhost` origin and the port must not collide with an ODS application port |

Do not copy generated secrets into issues, logs, support bundles, or PRs.

## Health and operations

```bash
systemctl status openclaw-gateway.service pixel-ingress.service pixel-ops-broker.service \
  pixel-workspace-preview.service
sudo -u "$USER" curl --unix-socket /run/ods-pixel/pixel-ingress.sock \
  http://localhost/health
docker inspect --format '{{.State.Health.Status}}' ods-pixel-edge
docker compose ps
```

Expected state is four active system services, `{"status":"ok"}` from the
private socket, a current Operations inventory projection, and a healthy
`ods-pixel-edge`. The socket is intentionally not reachable over a host TCP
port.

Useful logs:

```bash
journalctl -u openclaw-gateway.service -u pixel-ingress.service \
  -u pixel-ops-broker.service --since today
docker logs ods-pixel-edge
docker logs ods-dashboard-api
```

Errors are sanitized across both proxies. Inspect service logs for diagnosis;
the UI intentionally does not reflect gateway bodies, tokens, filesystem
paths, or internal exception text.

## Rollback

To restore Hermes as the default agent route:

```bash
./install.sh --no-pixel --hermes
```

Then verify Open WebUI selects the ordinary ODS model, the Dashboard remains
healthy, and the authenticated Hermes URL works. This removes the Pixel edge
Compose layer, model registration, environment, and default route. When the
private ODS management marker securely binds the active deployment to this
exact install, rollback also stops and removes the managed host gateway and
private ingress, then stops the isolated Operations Broker. It verifies the
broker unit, environment, executable, private policy, state-tree identities,
and dedicated system user/group before removing those privileged artifacts and
their quarantined downloads, plans, receipts, and authority state. Symlinks,
hardlinks, special files, foreign ownership, unexpected group membership, or
byte drift fail closed before deletion. Rollback also removes the active
release link and runtime attestation, and moves the fully verified release tree
into Pixel's private
`retired-ods-releases/` archive. An ambient, legacy, incompletely bound, or
drifted Pixel/OpenClaw deployment is left untouched. Re-enable only after the
qualification predicate and written authorization are still valid; ODS then
recreates the live deployment from the configured immutable Pixel source:

```bash
PIXEL_LICENSE_ACCEPTED=true ./install.sh --pixel
```

A full `ods-uninstall.sh` removes the Pixel host deployment only when the
private ODS management marker securely binds it to that exact install. It
stops the ingress before the gateway, validates every user and root deletion
target before mutation, and leaves an ambient, legacy, incompletely bound, or
drifted Pixel/OpenClaw deployment untouched. For a fully bound ODS-created
deployment, uninstall holds Pixel's deployment lock, verifies the installed
release manifest, retires only Pixel's validated sandbox containers, and
removes the exact live sandbox tag, active-release link, and runtime
attestation. It also removes the exact byte-matched workspace preview service
and a recursively revalidated preview snapshot tree; linked, foreign-owned,
writable, or special-file-bearing preview state fails closed before mutation.
The same bounded teardown removes only a byte-matched Operations
Broker installation and a recursively validated broker state root; it never
adopts or recursively deletes a partial ready, drifted, linked, mounted,
foreign-owned, or special-file-bearing tree. It moves the byte-verified release
tree out of Pixel's active
`releases/<version>` namespace into a private
`retired-ods-releases/<version>-<identity>.<nonce>/release` archive. The exact
retired bytes, candidate image tag, deployment lock, browser/bootstrap caches,
workspace, and user backups therefore remain available for recovery without a
path-bound release blocking a fresh install of the same Pixel version. An
interrupted deactivation resumes from its private staged or archive state. This
behavior is the same with `--keep-data`; that flag additionally preserves the
ODS `data/` tree. The bounded deactivation prevents a retired ODS install from
blocking a later fresh install at a different path without treating ambient
Pixel state as ODS-owned.

## Qualification gate

A candidate is not fresh-install ready until all of these pass on the exact PR
head:

- Pixel host ingress and projection Node tests;
- Pixel edge proxy Python tests;
- capability, license, immutable-source, and host-installer Bash tests;
- resolved Docker Compose validation with no published Pixel port or operator
  token and a real health dependency from Open WebUI;
- Dashboard API tests, Dashboard component tests, and production build;
- extension manifest validation and repository regression checks;
- a clean supported-host install with PID1 systemd;
- a real Open WebUI `pixel/default` chat;
- a real Dashboard `/pixel` streaming chat;
- a real static website build whose host-readback receipt opens the interactive
  Dashboard side panel, with click behavior verified and a model-authored
  localhost URL proven unable to open the panel;
- the diverse live site, app, game, visualization, animated-SVG, and voxel-art
  scenarios in `docs/pixel/visual-capability-qualification.md`, including
  responsive rendering, exact interaction evidence, iterative refinement,
  malformed-output recovery, cancellation, privacy, and concurrent isolation;
- a fresh first turn with no workspace-bootstrap truncation warning and a
  visible `Working` state while a tool turn is active;
- cancellation of a real long-running sandbox command, proving a clean
  `[DONE]` stream, status 130, marker cleanup, no surviving descendant, and a
  successful fresh command afterward;
- concurrent-chat cancellation isolation, proving the cancelled command stops
  while the other chat completes unchanged;
- a real turn invoking `pixel_ods_status` with sanitized ODS results;
- a real exact-byte download through stage, terminal wait, and create-only
  promotion, independently matching the remote byte count and SHA-256 digest,
  plus live rejection of overwrite, traversal, and symlink-parent attempts;
- a cross-family model swap and a context-only change, with Pixel using the
  newly active identity and invoking both bounded ODS tools after each change;
- live adaptive use at the bundled 8K T0 profile, plus rejection of a
  managed-Pixel activation below OpenClaw's 4K minimum with the previous runtime,
  persisted configuration, and gateway process unchanged;
- `--no-pixel --hermes` rollback with ordinary chat and Hermes verified;
- reinstallation/reactivation from the same clean, exact source; and
- OpenClaw's deep security audit, with every remaining finding recorded and
  reconciled to this documented single-operator threat model.

Record the ODS and Pixel commit SHAs, resolved Compose config, service states,
test logs, install log, and sanitized chat/tool evidence. A green unit suite
alone is not proof of live usability.

## Maintainer change map

| Concern | Source of truth |
|---------|-----------------|
| Host/license/source capability gates | `installers/lib/pixel-integration.sh` |
| Host installation, adoption guard, systemd | `installers/lib/pixel-host-install.sh` |
| Open WebUI and Dashboard edge | `extensions/services/pixel-edge/` |
| Host ingress and bounded ODS plugin | `extensions/services/pixel-agent/` |
| Exact-download promotion boundary | `extensions/services/pixel-agent/host/artifact_promoter.py`, `extensions/services/pixel-agent/plugin/download-promote.mjs` |
| Static workspace preview boundary | `extensions/services/pixel-agent/host/workspace_preview.py`, `extensions/services/pixel-agent/plugin/workspace-preview.mjs` |
| Dashboard API/UI | `extensions/services/dashboard-api/routers/pixel.py`, `extensions/services/dashboard/src/pages/Pixel.jsx` |
| Feature selection and Compose inclusion | `installers/phases/03-features.sh`, `installers/phases/11-services.sh` |
| Generated secrets and pinned source | `installers/phases/06-directories.sh` |
| Health and operator handoff | `installers/phases/12-health.sh`, `installers/phases/13-summary.sh` |
| Focused integration tests | `tests/test-pixel-*.sh` and each service's `tests/` directory |
