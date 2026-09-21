# Native Pixel transport integration

## Reuse Of Native OpenClaw

The pinned OpenClaw runtime already runs natively on macOS. Its bundled
`docs/platforms/macos.md` describes a per-user LaunchAgent; the native launchd
renderer uses `ProcessType=Interactive`, `/dev/null` for stdin and umask `077`.
The ODS gateway renderer now follows these three defaults. This is a candidate
definition change, not a performance measurement or an update of live services.

ODS deliberately retains its protected LaunchDaemon definition, running the
gateway as the installation owner, and its controlled stop/restart contract.
Running `openclaw gateway install` alongside it would create a second supervisor
without the Pixel access controller's binding. Do not install that second job.
The macOS companion app and its desktop permissions are not required for the
base Portal workflow. Reuse the pinned runtime, official config validator,
plugin loader and Pixel renderer; keep only ODS-specific lifecycle and policy
integration here. Do not copy bundled private OpenClaw JS modules into ODS.

Reference: https://docs.openclaw.ai/platforms/macos

## Qualification Status (2026-09-20)

`lib/pixel-native-layout.py` now provisions the initial owner-private home from
a staged candidate. The selected home must match the candidate workspace and
execution-control bind exactly. Existing homes, unsafe workspace links and
non-socket Docker endpoints are rejected. It copies the upstream workspace,
installs the actual cancellation wrapper, creates private state/log/temp/Docker
directories, and writes an unloaded gateway template. The template profile
denies all access; only the protected installer generates the operative policy.

The generated environment uses the selected Node and Docker paths and pins
state and Operations paths explicitly. Native history uses `docker-exec` to
reach the existing ingress Unix socket inside Docker; it does not create a
host socket or TCP listener. Layout inputs now require `--ingress-image` as an
immutable image ID, `--compose-project`, and `--ingress-gid`, matching the native
Compose deployment. The Operations broker account/ACLs still need provisioning.

The history adapter discovers exactly one running Compose ingress, verifies
its full container ID, image ID, project/service labels and non-root user, then
executes a fixed bounded Node client by immutable container ID. Queries travel
on stdin, never as executable source or a caller-selected URL. Only the existing
`/v1/chat/history` endpoint is used, with the current conversation's opaque user.
Two requests may be in flight per gateway module, with one five-second client
deadline and a 16 KiB response ceiling. Linux/WSL keeps its direct Unix transport.

An opt-in real test covers the existing ingress/ledger in a disposable Docker
container without network, and repeats the read under the native sandboxed
Seatbelt policy on macOS. No model, production history or published port is used:

```sh
ODS_TEST_HISTORY_DOCKER_LIVE=1 \
ODS_TEST_PIXEL_DOCKER=/absolute/path/to/docker \
ODS_TEST_PIXEL_DOCKER_SOCKET=/absolute/path/to/docker.sock \
ODS_TEST_HISTORY_IMAGE=sha256:QUALIFIED_LOCAL_NODE_IMAGE_ID \
node --test ../../extensions/services/pixel-agent/tests/history_docker_live.test.mjs
```

The Docker image must already exist locally and include Node. The test uses
`--pull never` and removes its uniquely named fixture container on completion.

The opt-in real preparation test covers renderer, bundle, new home, both
Seatbelt policy plans and initial-install planning. It also creates a file and
cancels a process through the copied execution wrapper in that temporary home.
Run the previous renderer qualification with `ODS_TEST_NATIVE_BUNDLE_LIVE=1`,
`ODS_TEST_NATIVE_LAYOUT_LIVE=1`, `ODS_TEST_PIXEL_DOCKER` set to an absolute Docker
CLI path and `ODS_TEST_PIXEL_DOCKER_SOCKET` set to its actual Unix socket path.
Select `-k layout` to avoid repeating the smaller qualification variants.
This remains preparation and plan-only evidence, not production activation.

The protected service installer now distinguishes a first installation using
`--initial-install` from migration of an existing owner gateway. Initial mode
requires an explicit unloaded `--gateway-plist` staging template and selected
runtime bundle. Before staging and again before activation it verifies the
template, rejects an existing owner LaunchAgent definition or loaded job, and
checks both gateway/access loopback ports. It never disables, starts or restores
the template as a user service. Migration behavior is unchanged.

On initial activation failure, confirmed shutdown produces `initial-stopped`;
unconfirmed cleanup produces `recovery-required`. Both preserve the admission
hold and disabled candidate services. Do not delete journals or enable jobs
to bypass these states. Automatic initial recovery and generation/provisioning
of the template's filesystem and auxiliary-service dependencies remain gates.

Two opt-in real launchd tests qualified this activation/cleanup engine with
uniquely named temporary GUI jobs. Their readiness check is process-only;
they do not prove root LaunchDaemon custody, Pixel HTTP readiness, access-mode
proof, or a complete clean installation. Run them with:

```sh
ODS_TEST_LAUNCHD_LIVE=1 python3 -m pytest -q \
  ../../tests/test_macos_launchd_stop_live.py -k initial_activation
```

On the development Apple Silicon Mac, an actual protected runtime update
completed. launchd startup now waits for the final Node process identity rather
than rejecting the temporary launcher executable. Post-update access recovery
passed both sandboxed and full-access tool proofs after administrative repairs
to the owner access receipt and root service baseline.

A Portal request with Qwen 3.5 9B created a project directory, wrote and read
an HTML file, and published its preview. Browser interaction confirmed the
counter changed from 0 to 1. This is local workflow evidence, not clean-install
qualification or a claim of support for every Mac.

The general macOS installer does not yet provision this native Pixel deployment.
`lib/pixel-native-bootstrap.py` now stages an owner-run OpenClaw runtime from
an explicitly selected clean Pixel commit. It checks the pinned package URL,
SHA256 and SHA512, requires native Apple Silicon Node at the release's minimum
version, installs nested dependencies and probes the resulting executable.
It also acquires the pinned Discord, SearXNG and llama-cpp packages, verifies
their package versions and plugin identities, and requires all three to load
from their exact staged paths in an isolated OpenClaw home before publication.
Pixel's source, Operations and Frontier broker plugins are copied from the
selected source, have dependencies installed with their committed npm lockfiles,
and must pass the same isolated load proof. The real six-plugin bootstrap passed
on the development Mac without invoking a broker or changing live services.
It requires explicit license authorization from its caller, refuses root, and
does not activate services or replace an existing destination. Node/npm must
already be provisioned by the parent installer. It does not yet configure the
agent. `lib/pixel-native-config.py` now stages an initial configuration through
the selected Pixel commit's actual `configure.mjs` and `render-config.mjs`,
instead of duplicating their policy. It clones only tracked source into a
temporary checkout, parses generated assignments without shell evaluation,
preserves Operations/ODS tools, copies the customized workspace and emits no
active-service claim or systemd units. Existing gateway configurations are
refused: this is initial staging, not an update path. Output is owner-private.
The required `--runtime` directory supplies the prepared packages. Their
versions and manifest IDs must match the selected release, and plugin paths
cannot escape that directory. The generated candidate explicitly loads its
required staged plugins, passes OpenClaw `config validate`, and must report
those exact plugin roots as loaded before publication.
The qualified sandbox image ID replaces the upstream image tag.

Native candidates also apply `installers/lib/pixel-runtime-budget.py`, the same
overlay called by the Linux/WSL installer. It preserves Operations tools and
configures context-dependent prompt/tool-result limits, cancellation mounts,
web tools and Pixel plugin settings before OpenClaw schema/load validation.
An explicit OpenClaw home prevents the isolated validator HOME from becoming
the execution-control bind source. The overlay only produces an unpublished
candidate; neither caller skips its final configuration validation.

The opt-in real renderer/loader test is available with already prepared,
licensed source/runtime inputs. It neither installs services nor starts a model:

```sh
ODS_TEST_NATIVE_CONFIG_LIVE=1 \
ODS_TEST_PIXEL_SOURCE=/absolute/path/to/selected-pixel-source \
ODS_TEST_PIXEL_RUNTIME=/absolute/path/to/prepared-runtime \
ODS_TEST_PIXEL_NODE=/absolute/path/to/node \
python3 -m pytest -q ../../tests/test_macos_pixel_native_config_live.py
```

It exercises the official renderer, shared ODS overlay, OpenClaw validator and
plugin loader, including a home path with spaces. Its synthetic image identity
is for rendering only, not evidence of Docker sandbox availability.

Add `ODS_TEST_NATIVE_BUNDLE_LIVE=1` to include the larger runtime-copy test.
It packages precisely the configured plugin paths, validates the relocated
configuration with the copied Node and OpenClaw, requires unique loaded plugin
identities, checks the bundle bytes again, and exercises the protected
installer's content-checked path mapping without publishing or starting jobs.

After candidate preparation, the corresponding installer operation is:

```sh
python3 ./lib/pixel-native-config.py bundle \
  --source /absolute/path/to/selected-pixel-source \
  --source-ref EXACT_SELECTED_COMMIT \
  --candidate /absolute/path/to/staged-candidate \
  --node /absolute/path/to/node \
  --runtime /absolute/path/to/prepared-runtime \
  --destination /absolute/path/to/new-runtime-bundle
```

It returns the selected bundle digest only after the relocated loader passes.
Failed qualification leaves no published bundle. The original candidate stays
unchanged, so the protected installer's existing byte-for-byte plugin mapping
can bind it to the root-owned destination. This is owner-run staging, not
service activation or qualification of Operations broker transport.

Real configure/render execution passed with the shared onboarding generator,
a real installed OpenClaw executable, a home path containing spaces and a
synthetic empty Operations policy. This proves rendering only. It does not
qualify an Operations broker, a live gateway, or the native service and Docker
transport dependencies. Candidate schema validation and plugin registration
now passed using the real prepared runtime; they must be verified again after
protected runtime publication. Those remaining gates
remain required before activation and installer integration.

Initial Operations policies now canonicalize local default working directories,
allowed/writable roots, local action working directories and download staging
paths before Pixel renders and hashes them. Remote SSH roots and executable
arguments are unchanged. A shared local/remote action that would require a
different working directory is refused rather than rewriting the remote path.
Existing live policies and approvals are not migrated by this initial path.
This matters on macOS because `/var` resolves to `/private/var`; the unchanged
broker correctly rejects a real working directory outside its literal roots.

The pinned upstream broker suite passed on macOS with canonical temporary paths:
54 tests and 17 subtests. Without canonical paths six local-execution tests
failed. The candidate generator's canonicalized policy also passed a real
temporary, owner-run broker hostname request. These results do not qualify a
dedicated broker identity, launchd/Seatbelt isolation, gateway directory access,
or the complete ODS Operations action catalog on macOS.

`pixel-native-ops-state.py` now provisions a new Operations spool under a
verified root-owned, ACL-free parent. It refuses existing state (including
links), root/shared/login-capable broker identities and gateway membership in
the broker group. Requests/cancel keep the upstream gateway-owner/broker-group
contract; only inventory, results and events receive gateway read ACLs.
Private plans, approvals, authority, credentials and downloaded artifacts are
not reader projections. ACL inheritance handles atomic inventory/result inode
replacement without a polling chmod job. Staging failures publish no state.

An opt-in root macOS test (`test_macos_pixel_ops_state_live.py`) exercised the
unchanged Pixel JavaScript request publisher and Python atomic JSON writer
under distinct identities. Requests were readable by the broker; replaced
projections remained readable but not writable/deletable by the gateway;
private records and root listing were denied. The synthetic fixture uses an
existing nonlogin account only for testing, creates no accounts or services,
and removes its temporary state. Run with `ODS_TEST_OPS_ACL_LIVE=1`,
`ODS_TEST_PIXEL_SOURCE`, `ODS_TEST_PIXEL_NODE` and the invoking owner's
`SUDO_UID`. Dedicated production-account provisioning, broker launchd/Seatbelt
policy, service activation and installer integration remain required. This
helper does not qualify a deployed Operations service or update existing state.

Dedicated identity provisioning is now implemented in
`pixel-native-ops-account.py`. It selects an unused UID/GID, creates the hidden
nonlogin `_ods_pixel_ops` account/group, and binds their GeneratedUIDs to an
atomic root-owned intent under `/private/var/lib/ods-pixel-access`. Replays only
fill missing attributes on the matching records; changed or foreign records,
ID collisions and unexpected memberships are refused. No gateway membership is
added. The service home is recorded, not created by account provisioning.

Actual account creation and replay passed on the development Mac, followed by
the real ACL test using that dedicated account (`ODS_TEST_OPS_BROKER_USER`).
The first verification exposed the native `IsHidden` DirectoryService namespace;
its reader now handles that field explicitly and refuses ambiguous aliases.
The root-run account plus ACL suite passed 26 tests. The account remains
installed but has no running broker; production launchd/Seatbelt provisioning,
protected broker/policy publication and installer orchestration remain gates.

`pixel-native-ops-service.py` renders the dedicated-user LaunchDaemon and a
Seatbelt filesystem profile using the shared native boundary implementation.
Only enabled local targets contribute allowed/writable roots; SSH target roots
are not interpreted as host paths. The daemon uses an empty initial environment,
Python isolated mode, private umask, bounded restart throttling and the original
Pixel broker. Code/state overlap and writes into the protected program root are
refused. Root-custody verification/publication is the installer's responsibility;
rendering alone is not installation or source authorization.

The opt-in `ODS_TEST_OPS_SERVICE_LIVE=1` test passed with the actual dedicated
account and a disposable root-owned program/policy. It bootstrapped a system
LaunchDaemon, submitted a real upstream JS request, executed hostname through
the unchanged Python broker and read the result as the gateway owner. After
SIGTERM, launchd restarted the broker and processed another request. That action
was denied a write outside its Seatbelt roots even though the same identity
could write that file without confinement. The fixture job/state were removed;
no broker processes remained. Service tests: 8 passed; related owner-run tests:
68 passed, 2 opt-in skips. Production publication, the complete ODS action
catalog and general installation/update integration remain unqualified.

The service module's `publish` function now uses the protected installer's
existing preflight/exact-write routines. It requires an explicitly approved
broker SHA256, bounded source/policy snapshots and the actual dedicated account;
verifies Python root custody; preflights every destination before writes; and
publishes broker/profile/policy without overwriting differing existing files.
It validates the policy through the protected broker under the dedicated UID,
empty supplementary groups, isolated Python and Seatbelt before writing the
daemon definition. It does not bootstrap the job. The caller must hold its
deployment lock and obtain the approved digest from the selected source commit.

The real service test now uses this publication path (including identical
replay) rather than manually creating broker/policy/profile/plist files. All
17 service/publication checks passed, including startup, request execution,
restart and boundary denial. The system `/usr/bin/python3` launcher has multiple
hard links and the CLT executable alias is a symlink: preparation must select
the canonical actual Python executable, which is checked without weakening the
no-link root-custody contract. No production service was enabled. Source
selection/orchestration, activation recovery and the full action catalog remain
required before release.

Native host-helper contracts now select fixed Darwin endpoints rather than
Linux `/run` paths: the extension manager and download promoter use
`/private/var/run`, canonical broker state and `_ods_pixel_ops`. The shared
Operations policy writer emits matching manager paths plus native installed
helper/catalog destinations. Linux values remain unchanged. The download plugin
selects the same native promoter endpoint; exact-download receipt verification
accepts the canonical `/private/var` broker artifact path only on Darwin, while
retaining the equivalent legacy `/var` spelling. Arbitrary directories remain
rejected. These changes do not provision the helper daemons or grant permissions.

Native helper tests passed 31 cases plus 14 subtests, including actual shared
policy generation. Related Node tools passed 517 cases, covering the complete
canonical-path download receipt/promotion flow. Linux helper regression passed
30 cases with one native-only skip; its host-installer suite passed 294 checks.
Root-confined helper deployment and real daemon-to-daemon promotion still need
qualification before treating the full Operations catalog as available.

`pixel-native-promoter-service.py` now renders a root download-promoter daemon
and narrow Seatbelt profile: read-only broker results/artifacts, write access
only to the selected workspace/runtime, path-filtered Unix networking, no IP
network rule, and no process-fork. Root custody/publication and production
activation remain separate gates. No privileged service is started by rendering.

The opt-in root `ODS_TEST_PROMOTER_LIVE=1` fixture passed actual owner-authenticated
Unix RPC and exact-byte create-only publication through the unchanged promoter
engine. It rebinds only test paths before invoking the original listener, rather
than modifying the production fixed-path contract. Ownership/mode 0600 were
verified; overwrite, outbound TCP, private approval reads, quarantine writes and
child creation were denied. The process was waited and temporary state removed.
This qualifies the profile/engine, not a production LaunchDaemon deployment.

The shared Operations service helper now discovers the final Apple Python
process executable through `proc_pidpath` and verifies root custody. Even the
canonical framework bin/python3 launcher can spawn Python.app; selecting that
final executable lets the promoter retain its no-child-process boundary.
Both live service suites passed together: 31 checks, including the real broker
publication/restart test and privileged promotion/confinement test. General
installer wiring and helper publication/activation remain incomplete.

The promoter module now publishes approved `artifact_promoter.py`, `unix_peer.py`
and `pixel_macos_custody.py` snapshots, profile and daemon definition using the
existing protected exact-write API. It verifies all three approved hashes, source syntax without executing it,
the nonroot workspace account and Python root custody; all destinations are
preflighted before any write, and the plist is last. Identical publication may
be replayed, while conflicting bytes/modes/owners fail. The caller must hold
the deployment lock and bind hashes to its selected ODS source revision.
The real root fixture now uses this publisher twice instead of manual source/
profile writes, then repeats owner RPC, exact-byte delivery and confinement
probes. The native listener now recreates its fixed runtime directory through
the root-custody helper (including ACL-free ancestors) before binding, then
applies root/owner-group mode 0710 and owner-only socket permissions. Logs live
in a separately provisioned persistent root directory; launchd starts from `/`
instead of depending on an already-created runtime working directory.

All 15 promoter checks passed, including two real starts separated by removal
of the socket directory, followed by a health RPC and unchanged delivered file.
Linux helper regression passed 30 tests with one native-only skip. Production
daemon activation/readiness/recovery and general installer orchestration remain
incomplete; this is not a complete machine-reboot qualification.

The extension manager now has a native daemon/profile renderer in
`pixel-native-manager-service.py`. It runs as the ODS owner with the normal
primary group, not the broker's private group; only the configured loopback
dashboard port and manager Unix socket are allowed. The ODS env file and broker
results are read-only, runtime/log writes are scoped, and child creation is
denied. Darwin requires `localhost:PORT`, not a numeric host literal, in this
Seatbelt network filter; the unchanged client still connects to 127.0.0.1.

`provision_manager_runtime` creates a new socket-only owner directory with
broker search permission and inherited socket read/write access. It does not
grant directory listing/mutation or change account memberships. Logs and
credentials must never be placed in this directory. A real distinct-user test
passed broker connection plus outsider, listing and mutation denial.

The opt-in `ODS_TEST_MANAGER_LIVE=1` fixture ran the unchanged manager against
a synthetic loopback dashboard API under the generated profile. Broker RPC
returned inventory, the synthetic key remained private, owner lifecycle RPC
was rejected, and another loopback port was denied. Only fixture socket paths
were rebound; no production endpoint or credential was used. Seven manager
checks passed; related owner regression passed 45 plus 3 subtests with one
live-test skip. Publication, runtime recreation/launch ordering, mutation
workflows and general installation remain unqualified.

Manager publication now checks the exact approved helper source set/hashes,
syntax, final Python custody and an owner-private readable environment file.
The credential is not read/copied into the published bundle. All destination
files are preflighted, persistent logs are root-provisioned, and the daemon plist
is written last through the exact-write API. Different existing files are
refused; identical publication can be repeated under the caller's lock.

The real manager test now uses publication/replay and additionally executes a
real broker action through manager RPC to the synthetic dashboard API. Only
the fixture socket path is rebound in a trusted test wrapper. Both programs
use their generated profiles and distinct identities; the broker result contains
the inventory but not the key. Root suite: 13 passed, 1 owner-fixture skip.
Owner regression: 52 passed, 1 live skip, 3 subtests. Production startup ordering,
runtime recreation, full extension mutation flows and installer orchestration
remain required. Set ODS_TEST_PIXEL_SOURCE for the added real broker fixture.

The shared Operations policy generator now selects native fixed-command
observations for OS release, CPU, memory, processes, services, storage,
interfaces, routes and listening ports. `system_observe.py` uses only fixed
root-owned macOS executables, bounded output/timeouts, process names without
arguments, and listening endpoints rather than established peers. launchd
listing covers the caller's bootstrap domain, not every possible user domain.
The existing Linux action commands and authority rules remain unchanged.
All nine native observers and the 24-action policy generation passed on the
development Mac; Linux host-install regression passed 294 checks. This does
not qualify the extension-manager transport or the dedicated broker service.

The bootstrap's separate `sandbox` command builds the selected Pixel source's Docker
image for Linux ARM64 with the actual owner's UID. Existing image tags are not
overwritten; architecture, user, release and source labels must match. A bounded
probe uses the immutable image ID, no network, no capabilities, a read-only root
and resource limits. Its only host bind is a new private temporary workspace;
the command verifies file write/read from both container and host, then removes
the probe container, including after a client timeout. It does not change the
currently configured agent's image.

The real sandbox build and an existing-image replay passed on the development
Mac, including the host workspace round trip. Image identity:
`sha256:f0daf1f5a2784c757d849898ac321509f0129e644cf4210d43a43a560daddc92`.
This proves the candidate image and Docker mount, not the complete agent tool
policy or the general macOS installer.

A real acquisition on the development Mac succeeded for Pixel source
`49235bd8eecef42bb6deccf85c212bc23eeb80af` and OpenClaw `2026.6.33`.
The bundle builder accepted its nested runtime with the reviewed stream fix;
the copied Node/OpenClaw pair then passed `--version` from `/` with a minimal
environment. A subsequent acquisition with all three pinned plugin packages
also passed native loading. A four-plugin bundle (including `pixel-ods`) passed
loading from its relocated paths using its own Node and an isolated home.
This is staging/relocation evidence, not a launched gateway test.
The shared onboarding writer is now `installers/lib/pixel-onboarding.py`.
The Linux/WSL host installer calls this same writer; native provisioning should
reuse it rather than maintain a separate model, tool or output-budget contract.
Direct and shell-wrapper output parity is tested, including owner paths with
spaces, preserved model budgets and rejected unsafe or invalid inputs. This
extraction does not yet implement native source acquisition or activation.
After activating an upgrade, the development installer releases its controller
lock and runs the protected access reconciler as the installation owner. It
reports success only for a verified result in the expected access mode. A
failed proof reports `native-runtime-access-reproof-required`; it does not
silently mark the Pixel workflow ready or force-clear an access transition.
Initial protected installation and the recovery CLI now require this same
post-activation proof. Recovery reads the current root-protected controller
settings after restoration and lock release, so it verifies the restored
runtime's access mode rather than the discarded candidate's mode. A failed
proof returns an error without reporting successful recovery. The recovery
proof adapter passed against the live installation; a new complete install
and a complete upgrade/recovery still require qualification.
Owner receipt relocation is connected to activation, rollback and interrupted
recovery, running as the owner only after all service trees are proved stopped.
The source/target configuration hashes come from the saved upgrade context;
the original restoration baseline is preserved. Service-baseline replacement
is journaled by the development installer. These combined paths still need
real upgrade/rollback qualification without administrative repairs. Do not
enable the native fragment as a generally supported installer path until these
gates, cold startup and installation under another owner/path pass. The current
macOS installer requires Apple Silicon; Intel support is not established.

`pixel-native.compose.yaml.disabled` is a staged installer component, not an
installer entry point or a declaration that macOS host-access parity is ready.
It does not start a gateway, accept a license, or grant host access.

Compose ordering is the ODS base stack, the shared
`extensions/services/pixel-edge/compose.yaml.disabled`, then this native
fragment. Resolve paths relative to the ODS installation directory. Compose
must support `!override`; validate the merged configuration before changing
running services. The shared Edge fragment retains Portal routing, preview
authentication, transition persistence, and Open WebUI defaults.

The installer must supply:

- `PIXEL_NATIVE_UID` and `PIXEL_INGRESS_GID`: the verified native runtime owner's
  numeric identity, not a fixed 501/20 pair.
- `PIXEL_NATIVE_CONFIG_PATH`: an existing private gateway configuration file
  readable by that identity. Missing bind sources must not create directories.
- `PIXEL_NATIVE_INGRESS_IMAGE`: the qualified image containing the Node runtime.
  Its gateway entry point and inherited health check are explicitly replaced.
- `PIXEL_NATIVE_GATEWAY_PORT`: the native loopback listener, default 18789.
- `PIXEL_NATIVE_ACCESS_PORT`: the owner control relay's loopback listener,
  default 18790. It must match the migration planner's `--access-port` and
  differ from the gateway port. The shared Edge identity remains
  `ods-pixel-edge`; experimental differently named containers are not adopted
  merely by adding a network alias or bypassing the host agent's checks.
- `PIXEL_NATIVE_WORKSPACE`: the existing canonical private workspace, mounted
  read-only by the preview broker. `PIXEL_PREVIEW_PORT` defaults to 9437 and must
  be free on host loopback. The broker uses the same port inside Docker so its
  verified receipt identifies the actual published browser origin.
- Shared Edge keys and preview runtime prerequisites. Compose interpolates the
  shared Linux fragment before applying overrides, so its
  `PIXEL_INGRESS_RUNTIME_DIR` variable must also be set even though that mount
  is replaced by a Docker volume. The same applies to
  `PIXEL_PREVIEW_RUNTIME_DIR`. Neither variable is the native socket path.

Native preview uses the shared snapshot engine in a dedicated non-root image.
The broker receives no Docker socket and can only read the configured workspace
and write its snapshot/runtime volumes. Its HTTP port is published on host
loopback only. Edge receives the preview runtime volume read-only.
The native plugin selects `workspacePreviewTransport: "docker-desktop"` in
installer-owned plugin configuration. This invokes only the fixed
`ods-pixel-workspace-preview` container's bounded `request` command; model input
is JSON on stdin, never a container name, command, or host path. Linux defaults
to the existing Unix-socket transport. Killing the Docker client stops waiting,
not necessarily a publication already accepted by the broker; no receipt is
returned after cancellation. The gateway's Docker authority still requires the
independent qualification described below.

The tool must pass both OpenClaw policy gates. In addition to the global
`tools.allow`, add only `pixel_ods_workspace_preview` to the Pixel agent's
`tools.sandbox.tools.alsoAllow`. Preserve existing deny rules. The default
sandbox allowlist does not include plugin tools; enabling the transport alone
can pass direct plugin tests while leaving the tool unavailable in a real chat.
Do not use `group:plugins`, disable sandboxing, or enable elevated execution to
make preview available. Validate the effective agent policy and a real Portal
tool call after updating configuration.

The initializer owns only the runtime volume's top-level directory. It has no
network or host bind mounts and does not recursively change existing history.
Ingress runs unprivileged and writes history/status and its Unix socket there;
Edge mounts it read-only. Neither service receives the Docker daemon socket.
The native gateway's separate Docker authority remains a security qualification
requirement, not something this transport fragment resolves.

## Native Control Transport

The staged native Edge fragment sends only access-mode and model-control
requests to `host.docker.internal` on the selected access port. Chat, preview,
history and the durable Edge transition gate retain their existing transports.
The new owner-run `com.ods.pixel-access-relay` LaunchDaemon binds only host
loopback and reuses the shared HTTP-to-UDS handlers. Its fixed credential is the
private owner key installed by the controller, not the model/chat key. It exposes
no arbitrary path, executable, controller operation or destination. Origin-bearing
browser requests are rejected. HTTP redirects and proxy environment inheritance
are disabled on Edge's control connection; failures never fall back to another
transport or replay a mutation. The root controller remains the authority.

The controller's reverse path to the Edge transition gate uses Docker Desktop
as the bound gateway owner, not root. Both container inspection and the bounded
`docker exec` client drop supplementary groups/GID/UID before exec and select
Docker's environment from the protected gateway definition. Owner identity is
checked against the account database on each call. Failure does not retry as
root or search a root Docker context. Linux retains its existing execution path.
A disposable Linux root fixture verifies the real child credentials for both
operations; production root-to-owner execution on macOS still needs qualification.

The relay uses the protected bundle's Node, root-owned source/profile/plist,
an empty inherited environment and a Seatbelt profile with no owner workspace,
Docker socket or writable runtime grant. Network access is permitted by this
profile; it is not a general network isolation policy. The migration stages the
relay disabled with the gateway/controller, starts it last, and requires an
authenticated available controller response with stable relay identity. Rollback
stops it before restoring the previous gateway. This is source integration,
not yet a completed privileged deployment or production model-switch proof.

The opt-in transport check uses an already installed Python-capable Docker image:

```sh
ODS_TEST_DOCKER_IMAGE=ods-pixel-edge:native-qualification node --test ods/extensions/services/pixel-agent/tests/access_mode_http.test.mjs
```

It starts only ephemeral loopback fixtures with synthetic keys and removes its
disposable non-root container. A real Docker Desktop client must be rejected
with the chat key and accepted with the owner key. Separate socket tests verify
the shared controller protocol and no replay after a lost reply. These tests
do not start the root controller or qualify its execution/permission boundaries.
The full Edge suite now passes with pytest on macOS and Linux (149 tests);
unittest alone omits five pytest-style special-file cases. On macOS, use a
canonical temporary directory such as `TMPDIR=/private/tmp`. Test containers
must not inherit a production `PIXEL_TRANSITION_STATE_DIR`: each gate fixture
selects its own state and distinct credentials where needed.

Do not enable this fragment until native deployment custody, gateway lifecycle,
preview/control-plane services and licensing are qualified. Existing local
qualification containers and volumes have different names: migration must
preserve their history and transition state before replacing them. Do not run
`down --volumes` during installation, upgrade or rollback.

Run the configuration contract tests without starting any service:

```sh
python3 -m unittest discover -s ods/tests -p test_pixel_native_compose.py -v
```

These tests validate Compose merging, identity/path/port substitution, retained
Edge contracts and required parameters. They do not prove runtime connectivity,
preview availability, restart recovery or full macOS security equivalence.

## Native process identity

`LaunchdGatewayService.process_identity()` requires an approved deployment
specification (`uid`, `gid`, absolute `executable`). It reads the kernel's
process creation timestamp, effective/real/saved credentials and executable
path through `libproc`, checks for changes during the read, and rechecks the
service PID. Native admission discovery compares this entire identity before
and after HTTP discovery, so reuse of a numeric PID cannot authorize a request.
Missing specification, short API replies, process exit or identity changes
fail closed. Linux retains its existing systemd PID contract.

This does not prove the executable's file custody or signature, absence of
descendants, sandbox policy, script identity or host-access equivalence. The
Darwin bridge now requires a root-owned system LaunchDaemon and an explicit
installation binding. Its protected deployment must ship
`pixel_macos_process.py` alongside the service and custody adapters and provide
the approved specification. A user LaunchAgent does not qualify.

Model/settings/provider transaction identity and restart now dispatch through
the selected service adapter. The receipt shape remains `{pid, started, boot}`.
Linux retains `ExecMainStartTimestampMonotonic` and `/proc`'s boot ID. Darwin
uses the verified process birth time in epoch microseconds plus
`kern.bootsessionuuid`, with identity checked around the boot query. Receipts
are platform-local; their timestamps must not be compared across platforms.
Existing restart rules still require a different PID, the same boot UUID and
a later birth time. A backwards clock adjustment therefore fails closed on
macOS rather than manufacturing a successful restart. Provider environment
updates now use reversible launchd plist images. Native discovery and owner
workers select the config and runtime home from the bound plist's `env -i`
arguments; shared settings/model/provider coordinators use that same config.
Linux retains its existing home/config paths and service environment.

The staged migration planner in `lib/pixel-macos-access-install.py` supports
separate source and installation directories. Its default dry-run reads the
existing native gateway, validates its command/port and validator, and emits
only nonsecret target metadata. It preserves the gateway's Node and entrypoint
when constructing the protected launcher. It does not restart a service.
For example, from the repository root:

```sh
python3 ods/installers/macos/lib/pixel-macos-access-install.py \
  --source "$PWD/ods" --install-dir "$HOME/ods" --owner "$USER" \
  --openclaw-bin "$HOME/ods/data/pixel-native/runtime/node_modules/.bin/openclaw"
```

The commit path requires both `--runtime-bundle` and `--bundle-digest`, selecting
the qualified package described below. An unbundled dry-run remains available
for diagnosis, but cannot publish files or change services. Promoting a root
service that still executes the user-writable Node/runtime is not supported.

The privileged activation path is opt-in through `--activate-upgrade` and
requires a fully qualified runtime replacement plus root custody. The normal
upgrade command remains a dry run; `--install` is reserved for initial
deployment and cannot activate an existing runtime. Before using the commit
flag, make sure the recovery command below is available and keep a backup of
the ODS data. Dry-run success or unit tests do not prove reboot recovery,
immutable runtime custody, full access-mode equivalence or complete descendant
teardown. Do not treat this migration prototype as the general macOS installer
yet.

The privileged path snapshots all controller/profile/configuration inputs and
preflights every destination before writing files or changing jobs. It rejects
drift, unsafe ownership/modes and symlinked ancestors before creating missing
directories. All three fixed system targets must return launchctl's missing-job
status (113); an existing deployment needs an upgrade transaction, and generic
command errors do not prove absence. Writes recheck custody, but this is not
a race-free filesystem transaction. The source plist's exact bytes and process
identity are verified before staging; intentionally disabled jobs are not
silently enabled. A private installation journal precedes staging. The three
system jobs are disabled before their files are published, preventing staged
RunAtLoad plists from starting a second gateway after interruption.

The supervised activation path disables the old GUI job, uses the shared stop
adapter and its captured-process exit check, then bootstraps each new service
once. It observes startup without retrying bootstrap and checks gateway health
with stable process identity plus an available controller status over its root
socket. On failure it disables and stops the new jobs before restoring the
unchanged original source and checking its health. The original plist is kept
for recovery, but its job remains disabled after successful handover.

Fault-injection tests cover each activation stage, bootstrap timeouts that took
effect, failed stopping, changed source verification, readiness failure and
staging I/O failure. They simulate launchd and do not prove a real privileged
migration. A failed cleanup retains `recovery-required`; staging failures leave
the original gateway running and new jobs disabled. Any prior installation
journal requires review rather than blind reinstallation. Crash/power-loss
resume, automatic rollback of published files/disabled-state overrides, active
request draining, runtime custody, root deployment qualification and a
privileged end-to-end upgrade still need completion before general
installation.

The commit path now acquires the existing Edge admission gate before gateway
handover. It requires the Compose-owned Edge to be idle and persists the exact
container, revision and private token before requesting the hold. An ambiguous
acquire retains that record and never starts activation or acquires with a new
token. Successful activation or verified restoration releases the same hold;
incomplete recovery retains it. This rejects active conversations instead of
terminating them. Native admissions outside Edge, crash-resume coordination and
full privileged migration still need end-to-end qualification.

The stop adapter retains its captured process birth tuples across repeated
checks. Production stores that witness in the existing root transaction
directory, bound to the provider transaction token, launchd target, boot UUID
and deployment definition. A fresh request can restore it while the job is
intentionally unloaded. Only launchctl's missing-service status (113) plus
kernel-confirmed process exit/PID replacement qualifies; permission errors or
unreadable process records do not. The witness is invalidated durably before
bootstrap, since it cannot prove that a later gateway instance is stopped.
Recovery from an ambiguous bootstrap interruption still needs qualification.

The currently deployed user LaunchAgent still uses its fixed qualification
profile. The staged installer now renders separate sandboxed/full-access
Seatbelt profiles from explicit native paths, protects the runtime, plugins,
controller and deployment files in both modes, and pins both profiles by SHA256
in the root controller receipt. Initial deployment selects sandboxed bytes;
the previous arbitrary user profile is not adopted as a permission contract.

The native bridge selects only approved root-custodied bytes through an atomic
replacement. It verifies the active path against the launchd argument and
records process identity before selecting a profile. A failed or interrupted
restart retains that record and requires a new process before proof. Sandbox
restoration also handles an already restored config with pending activation.
The root baseline pins both profiles; the verified boundary records the actual
selected mode, configuration and live core-tool proof. Drift is not repaired
automatically, and selection alone never claims an effective permission mode.

Real macOS kernel tests cover profile switching, writes outside the workspace,
runtime immutability and the fact that an old process retains its old profile.
Atomic storage tests use disposable user-owned fixtures with mocked root custody;
they do not qualify privileged deployment. The source's native access-mode
adapter still requires production runtime custody, complete privilege controls,
activation rollback and a real ODS core-tool proof in both modes before enabling
the controls in the installed Portal. No root daemon was activated by these tests.

For an opt-in real lifecycle check using an already acquired qualified
OpenClaw runtime (no package download and no production service restart):

```sh
python3 ods/tests/test-macos-pixel-lifecycle.py --node /absolute/path/to/node --openclaw-entrypoint /absolute/path/to/openclaw/openclaw.mjs
```

Run from the source repository as the regular login user. The test creates a
random-label LaunchAgent with private disposable state, no plugins/tools, and
an ephemeral loopback port. It pins Node through OpenClaw's supported
`OPENCLAW_WRAPPER` interface, checks the actual kernel executable identity,
restarts, stops, checks the captured process identities twice, bootstraps the
same plist, checks health/new identity, and removes its own job. This checks
the observed process tree, not an exhaustive race-free descendant boundary.
OpenClaw's default installer may select a
system Node instead of the runtime used to invoke the CLI. The check requires
version 2026.6.33 unless an explicitly qualified version is supplied with
`--expected-version`. It does not prove reboot recovery or production custody.

The activation engine also has an opt-in real handover check:

```sh
python3 ods/tests/test-macos-pixel-handover.py --node /absolute/path/to/node --openclaw-entrypoint /absolute/path/to/openclaw/openclaw.mjs
```

It uses disposable GUI jobs with the qualified OpenClaw runtime, private config,
no plugins/tools and an ephemeral loopback port. A controller-readiness failure
is deliberately injected after the candidate gateway starts. The test checks
actual launchd teardown, restoration of the original gateway with a new process
identity and health, disabled-job state, then the successful handover branch.
Temporary jobs are stopped and files removed on completion; cleanup uncertainty
retains the fixture directory. Bootstrap takes its domain from the fixed service
target, so the same activation engine supports these GUI fixtures while the
production installer still constructs only the approved system targets.

Local macOS qualification passed both branches with OpenClaw 2026.6.33. The
controller is a real sleep process with a readiness fixture, not the root access
daemon. This proves the observed native lifecycle/rollback behavior, not root
controller readiness, complete descendant isolation, permission parity or crash
recovery. No production service is restarted by this check.

The handover check accepts `--plugin /absolute/path/to/pixel-plugin` and
`--report /new/path/report.json`. With the plugin enabled it declares a private
Pixel agent (all tools denied), creates its private admission home, verifies
unauthenticated requests receive HTTP 401, and checks authenticated admission
against the actual gateway PID before and after rollback/handover. Reports are
written only after successful cleanup and never overwrite an existing report.
Failed checks retain their private diagnostic directory after stopping jobs.

The real packaged-plugin check exposed two fixture prerequisites and one
installer startup issue: a declared Pixel agent, the private `HOME/.openclaw`
parent, and transient launchd spawn states. The migration planner now rejects
missing/unsafe admission homes before staging. Startup observes transient
`runtime-unavailable-or-busy` states without another bootstrap; custody errors
and malformed service identity remain terminal.

## Packaged Runtime

`lib/pixel-runtime-bundle.py` stages an explicitly selected Node executable,
qualified OpenClaw package including its dependencies, and selected plugin
directories. It does not install or execute them. It rejects external/absolute
symlinks, special files, privileged modes, changed source bytes and an existing
destination. File modes are normalized; source ACLs/xattrs are not imported.
The canonical manifest pins every entry and internal link with SHA256 and a
qualified OpenClaw version. Verification checks added, removed and modified
entries and actual modes, not only the entrypoint. The bundle root stays private
for staging; production publishing must supply independent root custody.

```sh
python3 ods/installers/macos/lib/pixel-runtime-bundle.py \
  --node /absolute/path/to/node --runtime /absolute/path/to/openclaw \
  --plugin /absolute/path/to/pixel-plugin --destination /new/absolute/bundle
python3 ods/installers/macos/lib/pixel-runtime-bundle.py --verify /absolute/bundle
```

Local OpenClaw 2026.6.33 bundle qualification covered 32,941 entries and the
Pixel plugin. The relocated Node/runtime/plugin passed the real GUI handover
and authenticated admission check. This is content integrity and local runtime
evidence, not upstream provenance, all-Mac support or a root deployment proof.
The active installation still uses its original runtime. Privileged macOS
publication qualification, provider custody descriptors
and integration into the general installer remain required before switching
production to the bundle.

The bundle helper now provides `--publish /staged/bundle --expected-digest HEX`
as a separate root-only operation. It publishes into the fixed
`/usr/local/libexec/ods-pixel-runtimes/<manifest-sha256>` directory without
starting a service or changing configuration. Missing parent directories are
created through individually verified directory descriptors; existing modes,
ownership and ACLs are never repaired to make them pass. Publication is locked,
staged in a private sibling directory, and checks copied content plus ownership,
hardlinks and ACLs of the entire tree before publishing. Approved internal
relative symlinks are preserved, with their targets checked independently.
Existing versions must pass both custody and content verification before reuse.

Publication unit tests exercise real copying/locking with mocked root custody
on macOS, plus actual macOS ACL rejection. An isolated Linux root test exercises
real ownership and internal links with only the Darwin ACL API substituted.
These tests do not prove privileged macOS publication.

The opt-in migration helper `pixel-macos-access-install.py` accepts paired
`--runtime-bundle` and `--bundle-digest` arguments. Its dry run checks the
selected bundle and configured plugin bytes, then plans a separate private
configuration with remapped plugin paths. All other configuration values are
preserved. Plugin installation descriptors are rejected until their mapping is
qualified. The original runtime and configuration remain available for rollback.
Installation publishes the bundle before writing the candidate configuration;
it rechecks both selections immediately before stopping the original gateway.
The candidate uses the packaged Node and entrypoint, with distinct old/new
process identities. Drift aborts activation instead of overwriting user changes.

The actual local installation passed planning with the packaged runtime, and
fixtures cover publication order, configuration preservation and selection
failure before stopping the original. No privileged macOS activation was done.
Provider/runtime receipts, privileged qualification and integration with the
general macOS installer remain required before enabling this deployment.

## Native model bundle qualification

### Buffered Stream Progress Candidate

The opt-in `pixel-runtime-bundle.py --stream-progress-fix` stages a narrow
OpenClaw 2026.6.33 correction before computing the bundle manifest. It accepts
only the reviewed SHA256 of `dist/selection-BEwSQKM-.js`; different upstream
bytes fail closed. Both idle and diagnostic observers move before tool-call
repair/buffering wrappers. Tool normalization and output are preserved.
`ods-runtime-patches.json`, including original and patched hashes, is covered
by the same content manifest. The input runtime and published bundles are not
modified in place.

Component qualification against the staged runtime demonstrated that buffered
text updates real diagnostic activity, idle input still ages, output events
match, and cancellation closes model activity. This is not Portal qualification.
The local candidate digest is
`2d442ac9c0d074e3f2e441b5a182c0b05828d428a5a50a2d122c7961044121bf`.
The initial migration installer deliberately rejects existing system daemons
and differing installed files. Do not rerun it to upgrade an active deployment;
a transactional runtime-upgrade path and real game/preview validation remain
required before activating this candidate.

### Local artifact regression

The temporary native bootstrap diverged from shared Pixel model configuration:
it used only 2,048 output tokens and omitted Qwen's explicit
`params.chat_template_kwargs.enable_thinking=false`. Declaring `reasoning=false`
and selecting llama.cpp's `--reasoning-format none` does not itself disable
template thinking. Native configuration must preserve the shared installer's
model-specific parameters and output-budget defaults, including during updates.

The local configuration now matches the shared 8,192-token default for its
65,536-token context and sends the explicit no-thinking parameter. A real Portal
request created `Playground/snake-game/index.html`, published it through the
isolated preview service and completed with two successful tool calls. Browser
checks covered start, movement, collision and restart without console errors.
This qualifies that local request, not arbitrary generated code or all Macs.
It took 244 seconds under memory pressure, so generation latency remains open.

A subsequent real Portal round trip on the unchanged 9B configuration created
`index.html`, `assets/style.css` and `assets/app.js` in a fresh nested workspace
directory, then changed the button label in the same chat and republished.
Creation completed in 91.6 seconds (four successful tool calls); the follow-up
completed in 78.0 seconds (two successful calls). Both retained results reported
`passed`, `DONE` and no failures. Independent browser clicks verified the counter
before and after the change; CSS/JS hashes remained unchanged. The model used
`write` for the HTML replacement, so this is not evidence for the `edit` tool,
read-before-edit enforcement, reboot persistence or general migration parity.
No artifact source was written or repaired by the test harness. The owner chose
to retain 9B and prioritize reliable creation over further latency experiments.

A local fixed-workload A/B/A subsequently varied only context-checkpoint count
(8, 2, 8), preserving the model and 65,536-token context. With 5,523 prompt tokens
and 512 generated tokens, elapsed times were 41.836, 40.933 and 45.171 seconds;
generation rates were 18.329, 19.326 and 16.819 tokens/second. Memory pressure
rose during all three samples. This short no-tool test did not establish a
substantial sustained gain, so the original configuration was restored exactly.
Checkpoint reduction is not a qualified universal performance default.

For a separately qualified runtime with hybrid-model checkpoint support, the
native installer and `ods` restart accept these optional `.env` settings:

```dotenv
LLAMA_ARG_CHECKPOINT_EVERY_NT=1024
LLAMA_ARG_CTX_CHECKPOINTS=8
LLAMA_ARG_CACHE_RAM=512
```

These are an example qualification profile, not universal defaults. Unset
values preserve existing behavior. The selected executable must advertise
each requested option in `--help`; invalid/unsupported settings fail before
the normal native-model replacement step. Existing registered model profiles
retain their own qualified argument lists instead of mixing in these settings.
This does not automatically upgrade the pinned runtime, backport a cache fix,
or make an unqualified runtime safe to distribute. Smaller RAM budgets and
checkpoint limits must still be tested with the selected model and workloads.

Native idle unloading is separately opt-in with
`LLAMA_ARG_SLEEP_IDLE_SECONDS=120` (1..86400 seconds, or `-1` to disable).
The same pre-stop capability validation applies. This uses llama.cpp's own
idle timer, not a process-killing watchdog: active inference stays running,
and a new inference request reloads a sleeping model. Sleep releases the
model and KV/prefix cache, so the first request after sleep is cold and can
be slower. `/health`, `/props`, and `/models` do not wake the model; metrics
scrapes and other tasks can prevent sleep or wake it. Qualify monitoring
traffic as well as chat before enabling this in an installation. This is
workstation memory relief, not a claim of faster inference or a default for
all Macs.

For newer runtimes, `LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT=1024` selects the
explicit `--checkpoint-min-step` capability. This is a minimum spacing, not
the legacy periodic-checkpoint contract. Do not combine it with
`LLAMA_ARG_CHECKPOINT_EVERY_NT`; validation rejects both before stopping the
working model. The legacy option is not silently translated.

Upstream [sleep/metrics fix #27376](https://github.com/ggml-org/llama.cpp/pull/27376)
is present in official `b10964` (the nightly linked by `v0.4.1`). Its ARM64
archive SHA256 is
`033c845c1df9bf945ff37bb193238b40910b2244be3e1e637b2ceb5878f1a6f5`.
It passed the local ARM64 loader audit and an M5 qualification with the 9B
Q4_K_M model, 65536 context, q8_0 KV, eight checkpoints and 512 MiB prompt
cache. This does not change the default installer runtime or establish support
for every Mac. Preserve the previous bundle and service definition on updates.

An explicit live test can qualify monitoring and wake without restarting the
service. Start with the model awake but no active inference, and keep other
inference clients idle during the test:

```sh
python3 ods/tests/test-macos-native-idle.py --url http://127.0.0.1:8081 --idle-seconds 120 --wake
```

It continuously requests metrics, requires an observed awake-to-sleep change,
checks that further monitoring neither wakes the model nor resets its token
counter, then optionally sends one short completion. It does not prove Portal
tool behavior, cancellation or memory capacity; qualify those independently.

The model router must observe `http.disconnect` even on ASGI 2.4. Waiting only
for an error on a future response write can retain inference during silent
prefill or buffered output. Router tests cover both ASGI 2.3 and 2.4; a Portal
cancellation receipt still needs native inference/queue checks before claiming
that compute has stopped.

Before distributing a locally compiled flat ARM64 llama.cpp bundle, check its
loader metadata on macOS:

```sh
python3 installers/macos/lib/native-runtime-audit.py --bundle /path/to/bundle --minimum-macos 14.0
```

This rejects external Homebrew/build-directory dependencies, escaping symlinks,
missing companion libraries and deployment targets newer than requested. It
does not execute the binaries or qualify their CPU instructions, code signing,
Metal correctness, TLS features or behavior on older hardware. It is a release
qualification helper, not yet an automatic installer gate. Builds still need
explicit baseline CPU settings and real supported-hardware testing.

### Workspace Inheritance And Preview Qualification

Pixel may inherit `agents.defaults.workspace` without an agent-specific
`agents.list[id=pixel].workspace`. The tool evidence tracker must resolve the
agent override first, then the default. Otherwise an absolute `write`/`read`
can succeed while a relative `pixel_ods_workspace_preview` is incorrectly
rejected for missing file evidence. This is not an inference-memory failure.

The plugin source now implements this fallback, with regression coverage in
`workspace_path_contract.test.mjs`. An already installed, immutable plugin
bundle does not acquire the fix from a source edit. Do not edit files inside
that bundle or bypass its manifest checks. A supported configuration workaround
for the older bundle is to declare the existing default workspace explicitly
on the Pixel agent, preserving the exact directory and all access settings.
Keep a private configuration backup, wait for active requests to finish, and
revalidate access through the controller after reloading. Do not overwrite an
existing, different agent-specific workspace.

A local full-access Portal test on 2026-09-18 with the explicit configuration
created an HTML counter using an absolute path, read it, and published it with
a relative directory in one request: three calls, zero failures or blocks,
129.1 seconds, and a verified publication receipt. Browser testing confirmed
the counter increments, but found horizontal overflow at 390px. Publication
success alone therefore does not establish generated-page quality. Validate
both publication and actual browser interactions at desktop/mobile sizes,
then ask Pixel to edit and republish to qualify that workflow separately.

The follow-up edit/republication test also passed: three calls, zero failures
or blocks, 307.4 seconds. The new snapshot returned HTTP 200 at both 1440x900
and 390x844; its marker remained visible, the counter advanced from zero to
two, no page errors occurred, and horizontal overflow was absent. Access was
still verified full-access afterward, with no pending transition or stream.
This checks one concrete counter task, not arbitrary generated applications
or uniform inference latency.

A separate local sandboxed run on 2026-09-19 created, read and published a
counter through the Portal in 91.6 seconds (three calls, no failures or blocks).
The Docker sandbox had a read-only root, no privileged mode, no network, and
the workspace mounted writable at `/workspace`. Browser checks passed at
1440x900 and 390x844: marker visible, counter zero to two, no page errors or
horizontal overflow. This qualifies that concrete sandbox publication flow,
not a complete isolation audit or behavior on other Macs.

### GUI App Launch Action

`pixel_macos_apps.py` implements an owner-session app launch action. The
authenticated host-agent endpoint is `POST /v1/pixel/apps/open`, accepting only
`{"bundleId":"com.apple.calculator"}`-shaped requests, without shell commands,
URLs, documents or arguments. `config/pixel-approved-apps.json` maps approved
bundle IDs to absolute application paths; missing approval refuses the action.
It checks the app's bundle ID, the console owner and verified full-access.
The host lifecycle lock serializes the action with host-managed mode changes.
The installed CLI `bin/ods-open-app BUNDLE_ID` reads the owner credential
privately and calls the fixed loopback host-agent endpoint on port 7711.

This is a launch request receipt, not proof of window visibility or UI control.
It does not grant AppleEvents, Accessibility, screen recording or arbitrary
GUI automation. The local allowlist initially contains only Calculator.
The owner configuration is not a root-protected approval boundary against
an already full-access process; do not describe it as such. Direct controller
mode transitions and app-launch races still require broader qualification.
Installation/upgrade must ship the module and CLI together with the handler;
automatic installer packaging and Portal-native tool registration are pending.

### Startup Reproof Qualification

On 2026-09-19 an idle native gateway was forcibly terminated after checking
that no Portal stream or model transition was active. The installed launchd
job restarted it automatically. Explicitly restarting the owner host-agent
then exercised its installed startup helper against the new gateway process:
the helper completed, and the public access status returned verified
full-access with no pending transition. No manual access-mode request or
journal deletion was used for that reproof.

This is a process-restart qualification, not a cold Mac reboot. In particular,
the host-agent was explicitly restarted: startup-only reconciliation does not
yet establish unattended recovery when just the gateway crashes while the
host-agent remains running.

The subsequent host-agent revision adds a 30-second access monitor using the
same protected helper and lifecycle lock. It continues after successful
checks or read-only busy/unavailable preflight, and stops on pending recovery,
custody failure, timeout or uncertain mutation rather than replaying changes.
The installed revision passed a second idle gateway crash test: launchd
restarted the gateway, the unchanged host-agent process triggered reproof,
and access returned to verified full-access with no pending transition and
no blocked Edge admission. No manual host restart or mode request followed
the simulated crash. Startup/reconcile tests cover 26 cases and seven
subtests. Cold reboot and interruption during active tools or transactions
remain separate qualification requirements.

The local Portal cancellation check on 2026-09-19 observed native inference
active before cancelling the exact chat/request identity. The API acknowledged
cancellation in 0.12 seconds; the stream ended and retained state was
`cancelled`. Within 4.03 seconds native slots were idle and Edge reported zero
streams with admission unblocked. Access remained verified full-access with
no pending model/access transition. This exercised cancellation during prefill,
not interruption of tool execution, file writes or an update transaction.

This local workaround is not completion of the versioned runtime upgrade,
complete reboot recovery or installer validation on
other supported Macs. Changing the source configuration also invalidates old
upgrade plans; generate and qualify a fresh plan before activation.

## Native Runtime Update And Recovery Qualification

The development installer now has a `recover-runtime` subcommand. This is
not an enabled automatic updater: activation with `--current-bundle-digest`
and `--install` remains rejected until integrated interruption qualification
is complete. Do not use initial installation as an update workaround.

Read-only planning for an existing protected installation uses:

```sh
python3 ./lib/pixel-macos-access-install.py \
  --source /absolute/path/to/ODS/ods \
  --install-dir /absolute/path/to/ods \
  --owner OWNER \
  --openclaw-bin /usr/local/libexec/ods-pixel-access/openclaw-gateway-launcher \
  --runtime-bundle /absolute/path/to/qualified-candidate \
  --current-bundle-digest CURRENT_SHA256 \
  --bundle-digest CANDIDATE_SHA256 \
  --upgrade-kind workspace-root
```

Both digests must be independently selected full SHA256 values from verified
bundles. `workspace-root` qualifies only the reviewed workspace fallback;
`stream-progress` is the other supported, separately reviewed change. Neither
choice permits arbitrary replacement code. A successful plan does not establish
that activation, rollback, access proof or cold reboot has passed.

For a matching interrupted transaction created by this installer, the explicit
operator recovery entry is:

```sh
sudo python3 ./lib/pixel-macos-access-install.py recover-runtime \
  --owner OWNER \
  --current-bundle-digest CURRENT_SHA256 \
  --bundle-digest CANDIDATE_SHA256
```

Run from this directory using the reviewed installer checkout. This entry does
not require the original writable staging directory. It requires its private
context and transaction records, not a manually constructed substitute. It
checks the recorded deployment against fixed destination and permission
contracts, qualifies runtime/configuration bytes, and holds the controller's
exclusive lock. Pending access/policy transitions are not force-cleared.

A pending journal selects restoration. A terminal archive instead selects
verification and completion of admission release without replaying deployment.
Output `restored` identifies the original bundle; `active` identifies the
candidate already committed before interruption. A missing or divergent record,
unknown process witness or revision conflict remains an error. Preserve the
records and report the error; do not delete them to make recovery proceed.

After a verified restoration, archive that attempt before retrying the same
candidate. The root-only command preserves the full private history, checks
the restored files and live services twice, and removes only that attempt's
terminal records under the controller lock:

```sh
sudo /usr/bin/python3 ./lib/pixel-macos-access-install.py retire-runtime \
  --owner OWNER \
  --current-bundle-digest CURRENT_SHA256 \
  --bundle-digest CANDIDATE_SHA256
```

The resulting `history` filename is inside `/private/var/lib/ods-pixel-access`.
Keep it private: it contains recovery configuration. If cleanup was interrupted,
repeat with `--history-digest HISTORY_SHA256`, taking the digest from the
preserved `runtime-upgrade-history-HISTORY_SHA256.json` filename. The command
checks its digest and protected custody and refuses pending transitions or
deployment drift. It does not activate the candidate; activation is a separate
explicit `--activate-upgrade` operation.

Current qualification includes actual SIGKILL at file-replacement checkpoints,
real filesystem restoration, real isolated Edge HTTP release/retry across an
application restart, and three disposable GUI launchd jobs restored in dependency
order. These tests deliberately do not modify an installed ODS. GUI job tests
adapt root file custody for temporary owner files; they are not proof of root
LaunchDaemon deployment, a cold reboot, or the complete production transaction.

The opt-in launchd qualification can be run on a logged-in Mac with:

```sh
ODS_TEST_LAUNCHD_LIVE=1 python3 -m pytest -q ../../tests/test_macos_launchd_stop_live.py
```

It creates uniquely named temporary jobs and removes them on completion. The
remaining release gates are full protected-service recovery, the interruption
window after candidate plist publication but before a candidate stop witness,
end-to-end installer packaging, and cold restart with delayed Docker readiness.
Native desktop application automation is outside this base-compatibility scope.

### Service Source Bundle

`pixel-native-config.py services` prepares broker, manager and promoter source
snapshots together with the candidate Operations policy. It requires the exact
selected Pixel revision and a staged candidate. Its private output contains
`services.json` with per-file hashes and a gateway configuration hash, but never
copies the credential-bearing gateway configuration. No service is started.

The privileged orchestration must approve the returned manifest digest and bind
it to the selected source revision and candidate configuration before calling
`verified_services`. That reader accepts those three expected identities from
the transaction, checks the complete fixed source set, rejects linked or changed
files, and returns verified bytes for the individual service publishers. A
manifest located beside the source is not authorization to install that source.
Reading this bundle does not itself publish or activate any service.

Qualification: 36 focused configuration/bundle tests passed, including changed
source, manifest, revision and configuration rejection. The selected real Pixel
renderer and service bundle verification passed together. The separate runtime
copy and layout cases were not enabled in that run. General installer wiring,
multi-service activation/recovery and cold-start qualification remain open.

`pixel-native-services.py` now provides joint publication of the approved source
bundle through the existing manager, promoter and Operations publishers. It
checks the whole bundle and renders every service before the first write, then
publishes without loading jobs. Its caller must hold the deployment lock, supply
approved identities and keep admission closed. Identical interrupted publication
can be replayed; differing existing files are refused, not replaced. The
manager socket directory is provisioned once under `/private/var/lib/ods-pixel-manager`
so its ACLs persist across boot. The owner-run server replaces its own stale
socket without recreating the directory. The combined privileged path still
needs live qualification before main installer wiring.
Regression across orchestration and individual publishers: 54 passed, 3 live
privileged cases skipped. This run does not prove integrated activation.

The persistent manager transport was subsequently qualified with a real abrupt
process stop and restart: the stale socket remained, the new confined process
replaced it, and the dedicated broker reconnected successfully with no ACL
reprovisioning. Manager suite: 13 passed, one owner-only case skipped under root;
related owner regression: 43 passed, one live case skipped. This is a process
recovery test, not proof of a complete reboot with Docker and the model ready.

Joint privileged publication and identical replay now pass with the real three
publishers and selected broker source (6 tests). The fixture uses temporary
program, policy, state and definition paths, synthetic approval identities and
does not load launchd jobs; source acquisition and activation are not proven by
that test. Standard service log directories may be provisioned by publication.
The shared native policy now selects the published manager path under
`/usr/local/libexec/ods-pixel-services/manager/extension_manager.py`. A generated
policy test compares all six extension commands with the rendered manager job's
program and socket, preventing the previously mismatched legacy path.

Service bundles also carry `helpers/extension_search.py`, `helpers/system_observe.py`
and the extension catalog generated through the shared Linux/WSL catalog writer.
The joint publisher writes these approved snapshots as root-owned read-only
files, and native policy paths point to their published locations. This closes
the missing-helper packaging gap, not the complete action-execution gate.
Focused bundle/publication/path checks: 22 passed, one privileged skip. Real
candidate generation and bundle verification: one passed, two optional skips.
Joint privileged publication/replay including helpers: 6 passed.

Execution qualification found and fixed two catalog integration failures:
publication now uses root ownership, broker group and mode 0640 as required by
the catalog reader, and the reader selects the fixed native catalog path on
Darwin while preserving the Linux path elsewhere. Arbitrary catalog paths remain
rejected. The joint live test now submits search and OS-observation requests to
the original confined broker using the published helpers, and verifies successful
receipts. Its search harness rebinds only the catalog path to the disposable
fixture. Six root tests and 22 owner checks passed (one live case skipped in the
owner run). This does not qualify all actions or automatic service activation.

### Combined Owner Preparation

`pixel-native-prepare.py` provides one owner-run preparation entry point for an
acquired runtime and selected source: configuration, service bundle, runtime
bundle and native layout in order. Existing homes are refused. Home creation
occurs after both bundles finish. Private `preparation.json` records the phase,
digests or failed phase without exception text. Failed preparations are retained
for diagnosis, not silently reused. The final status is `prepared` with
`requiresActivation: true`, never ready or authorization for root publication.
The combined path passed with the actual selected runtime and plugins in a
temporary home (49 seconds), verifying both bundles. Fixture image identities
do not prove sandbox/ingress availability. Related regression: 47 passed, one
privileged skip. Acquisition, main installer invocation, protected activation
and integrated recovery remain release gates.

The preparation entry point can now acquire the runtime when `--runtime` is
omitted (requires `--npm`), and build/qualify the sandbox when `--sandbox-image`
is omitted. Either operation requires explicit `--license-authorized` before
creating preparation state or performing downloads/builds. It uses the existing
pinned bootstrap, not a second installer implementation, and records acquisition
or sandbox qualification failures separately. Supplied runtime/image inputs keep
the previously qualified path. Ordering/license/failure coverage passed; a full
fresh-download combined run has not been qualified by those unit tests.

The full acquisition preparation path was subsequently run on this Mac with no
prepared-runtime input. Pinned runtime acquisition, sandbox build/reuse with
real no-network execution proof, candidate rendering, shared catalog generation,
service packaging, runtime packaging and fresh home layout completed together
in 77 seconds. Both bundle digests and the candidate's sandbox image binding
were verified. The test used an existing selected Pixel source checkout and
Node/npm installations; it does not prove their acquisition on a clean Mac.
Ingress still used a fixture identity and no production job was loaded. Linux
host-installer regression also passed 294 checks. Protected activation, main
installer integration, actual ingress and reboot remain unqualified.

The service orchestration now has a new-install activation sequence using the
existing launchd adapters. It verifies definitions and absence of every target,
refuses intentionally disabled/existing jobs, then starts manager, promoter and
Operations in order with process identity and caller-supplied readiness checks.
The trusted installer must hold its deployment lock and admission, construct
approved adapters, and persist checkpoints in its root-owned journal. Success
still requires the gateway proof. Failure disables attempted jobs and requests
verified stops in reverse order; uncertain stops remain explicitly recovery
required, never labelled restored. Twelve orchestration/publication tests passed
with one privileged skip. The integrated activation path has not run live yet.

The integrated service activation was subsequently exercised through real
launchd jobs. This exposed missing owner-writable manager log files, an immediate
PID check racing startup, and the generic process verifier rejecting the root
promoter. Logs are now pre-created privately without truncation, startup has a
bounded wait, and root process verification requires an explicit opt-in limited
to the promoter adapter. Gateway process verification remains nonroot by default.

The promoter also refused `/private/var/run` because this Mac's directory is
group-writable by daemon. Native promoter sockets now live under the protected
`/private/var/lib/ods-pixel-artifact-promoter`, with plugin and service paths
updated together. System directory permissions were not changed.

Six privileged joint tests passed with manager API and promoter health checks
and all three real process identities. The fixture uses a synthetic dashboard,
temporary spool and a promoter harness rebinding only the spool constants; it
does not prove actual Portal integration. All three jobs were stopped and their
absence verified. Related regression: 68 passed, two live skips, 28 subtests.

The running broker readiness fixture now submits an OS-observation request and
requires a successful result, rather than accepting a PID alone. Joint launchd
activation passed again (6 tests, 4 seconds). Failed-start cleanup now attempts
bootout of a verified loaded definition when a full process-tree stop cannot be
confirmed, preventing a scheduled spawn from being left registered. Such a case
still remains recovery-required; unload is not proof of descendant termination.
Seven activation failure/ordering tests passed. Actual installer admission and
Portal integration are still separate unqualified requirements.

Native Compose now has an opt-in live qualification using an isolated project,
unique container names, fresh volumes and a synthetic HTTP gateway on the Mac.
The actual shared Edge plus native override build/start ingress, preview and
Edge; all health checks passed and authenticated `/v1/models` returned
`pixel/default`. Direct ingress health proved Docker-to-host connectivity.
The live check passed in 34 seconds; Compose resolution passed four tests and
four subtests. The first fixture key was too short and was corrected to the
required format. Cleanup removes only that project's containers and volumes.
This does not test a real model turn, preview publication, the actual Portal,
or installer enablement of the currently disabled fragment.

The live Compose qualification now publishes an HTML project from a nested
workspace directory through the actual preview control protocol. It requires
the verified entry hash, reads the page over the host loopback published port
with the expected site Host header, and confirms that an unpublished source
edit does not alter the immutable snapshot. The complete check passed in
35 seconds. An earlier run received temporary ingress unavailability with the
single-threaded synthetic gateway; the fixture now serves concurrent checks.
This remains a transport/snapshot qualification, not a model-created project or
a browser interaction through the production Portal.

### Owner source acquisition and preview executable

The preparation CLI can now omit `--source`: with explicit
`--license-authorized`, it acquires the official Pixel repository, checks out
the caller-selected exact commit and validates its release manifest before
runtime acquisition. Existing destinations are refused. A failed clone or
verification leaves no published source checkout and records the preparation
phase without command stderr or credentials. The bootstrap helper also accepts
an authorized local repository, matching the shared Linux source workflow.
No new release pin is introduced and no existing checkout is reset.

Source acquisition is covered with real local Git repositories, including
uncommitted input changes, an absent commit and invalid release metadata.
Preparation/bootstrap/configuration/layout unit tests passed: 96. This removes
one manual input to preparation; `install-macos.sh` integration and protected
activation are still pending.

The native layout exports `PIXEL_PREVIEW_DOCKER` using its detected canonical
Docker executable. Preview uses that value with fixed arguments and no shell,
rejecting malformed paths. Existing deployments without the variable retain
the legacy Docker Desktop path. Preview/cancellation tests passed: 13. Container
image/project binding and end-to-end installer qualification remain separate.

The opt-in real native layout qualification also passed (one test, four
deselected, 60 seconds), including the canonical preview executable in the
generated environment and the existing protected initial-plan checks. This
used the already acquired runtime and source, not a clean network install.

### Installer service readiness

`pixel-native-services.py` now provides production readiness callbacks for the
published service set. Manager inventory runs as the dedicated broker identity,
promoter health as the owner, both with isolated Python and no supplementary
groups. The callbacks enforce bounded startup waits and require a successful
protocol response. Operations readiness submits only `host.os` through the
actual spool, then requires the matching successful job and available output.
Spool directories and receipt files are opened without following symlinks and
checked against the provisioned identities. The probe never grants approvals
or performs an extension mutation; the read-only job remains in broker history.

The earlier joint fixture accepted manager CLI exit code zero, which does not
prove the API inventory succeeded. The stricter check exposed its invalid fake
API credential and missing JSON content type. The fixture now uses a valid
synthetic key, requires the expected authenticated catalog request and calls the
production readiness callbacks. Earlier manager API readiness claims based on
exit status alone are superseded by this check.

Joint privileged qualification passed: six tests in five seconds, including
real launchd processes and the running broker request. All three temporary jobs
were independently confirmed absent afterward. Related regression: 81 passed,
four opt-in skips. Negative cases cover failed inventory despite exit zero,
wrong protocol kind, invalid JSON, nonzero exit and timeout. Main-installer
transaction wiring and actual Portal qualification remain outstanding.

### Initial protected-installer service integration

The protected gateway installer now accepts the complete initial service
selection through `--services-bundle`, `--services-digest` and
`--pixel-source-ref`, only with `--initial-install`. Planning verifies the
service manifest against the exact source gateway configuration bytes. Native
layout now preserves those bytes rather than reserializing the JSON, retaining
the candidate digest through owner preparation and protected planning.

For a selected service set, activation acquires the existing Edge admission
hold before provisioning the dedicated Operations identity, new spool and
manager runtime, publishing the approved service set, and running all service
readiness checks. Only then does it activate the gateway/access services.
Failures before gateway activation retain admission. The private
`service-installation.json` records selection and last progress without exception
text; existing journals/spools are refused rather than erased or silently
adopted. This is initial-install integration, not update/recovery support.
Successfully started auxiliary jobs may remain after a later gateway failure;
the hold and journals remain necessary for explicit recovery.

Focused regression passed 426 tests with two opt-in skips. Tests cover ordering,
failed-service admission retention, configuration digest binding, provisioning
failures and journal replay refusal. Real owner preparation/layout plus protected
planning with the staged service bundle passed in 59 seconds. The live check
does not activate a complete new production installation. `install-macos.sh`
still needs orchestration of inputs, Compose and this protected entry point;
the existing running installation has not been migrated by these tests.

### Qualification of the public-beta source selection

The source selected by the shared installer, commit
`b33730436baf5d98bf58f7d57c090318fe19f433`, was acquired and used for a fresh
native runtime acquisition, sandbox image build/execution proof, candidate
configuration, service/runtime packaging and owner layout. The opt-in acquire
qualification passed in 138 seconds and its receipt remained
`awaiting-protected-activation`. This verifies preparation with the actual beta
selection, not just the older development checkout. The receipt does not prove
complete protected activation, Portal behavior or reboot.

The first local source acquisition exposed a Git edge case: cloning advertised
refs does not necessarily transfer the selected commit when no branch points
to it. Acquisition now explicitly fetches the exact approved SHA before detached
checkout. Real local Git tests include an unreferenced selected commit and
preservation of the original HEAD and uncommitted changes. Related preparation,
bootstrap, configuration and layout regression: 97 passed. The original source
checkout was not changed. No release pin or user's model selection was changed.

### Two-phase Docker startup

The owner-side `pixel-native-compose.py` helper separates infrastructure startup
from final health. It starts ingress, preview and Edge without `--wait`, then
requires authenticated idle admission control independently of gateway health.
The dashboard credential travels through stdin, not command arguments. After
protected gateway activation, a separate check requires all three exact Compose
services running and healthy. Neither phase activates the native gateway.

The isolated live Compose test now begins with the host gateway not listening,
requires Edge health to return 503 while admission control is available, starts
the synthetic gateway, and requires full Docker health before model listing and
HTML publication/readback checks. This passed in 38.53 seconds; static/unit
checks passed 20 tests and four subtests. Its project and volumes are removed
afterward. This validates cold-start ordering with a synthetic host gateway,
not the main shell installer or a production model turn. The main installer
still needs to invoke these phases around the protected activation entry point.

### Prepared activation entry point

`pixel-native-activate.py` now joins the previously separate steps for an
owner-prepared initial deployment against an already configured ODS stack:
validate the preparation and protected plan, bind its service manifest, verify
persisted native Compose values and distinct credentials, validate Compose,
start the dashboard/model-relay prerequisites, start native infrastructure,
invoke the protected installer through sudo, and require final Docker health.
The access relay port is read from the persisted environment and validated by
the protected planner. The caller supplies ordered Compose files including the
shared model relay, shared Edge and native override.

This command does not generate or persist the main installer's environment or
Compose selection. It requires those to agree with the preparation before any
service changes. The private owner `activation.json` records phase and outcome;
an existing attempt is refused and failures require recovery rather than an
automatic reset/retry. Credential values are not added to command arguments or
the journal. The root installer independently revalidates the supplied package
identities; an owner receipt is not elevated authorization by itself.

Orchestration and related regression passed 434 tests with one live skip. The
entry point's help runs under the system Python. These tests use mocked process
execution for the full orchestration: no claim of a new production installation
is made. The real component tests above remain separate evidence. Main-shell
input generation/persistence, full initial activation, updates, reboot and
Portal end-to-end qualification are still required before the PR is complete.

### Persisted native environment bindings

The activation entry point now supports `--configure-stack` to persist missing
native Compose bindings before validating/starting the stack. It derives the
UID, group, immutable ingress image, configuration/workspace paths and ports
from the approved preparation/plan. Only this fixed key set and the unused
shared-fragment runtime placeholders can be written; model choices and existing
credentials are not generated or changed by this step.

`pixel-native-env.py` uses the existing ODS `env_values` parser/serializer,
refuses conflicting or duplicate native bindings and non-private, linked or
foreign-owned environments, writes a private before-image under preparation,
and atomically replaces `.env` only if the original snapshot is unchanged.
An identical configuration is a no-op. The protected planner now uses the same
literal parser so quoted paths, escapes and comments have consistent meaning.

Related regression passed 446 tests with one opt-in skip, including an actual
temporary environment write through the activation orchestration, preservation
of model/credential bytes, special characters, existing conflicts and concurrent
changes. Process execution in that orchestration test remains mocked. Main
installer onboarding/source/runtime input generation and Compose-selection
persistence still need integration; this is not a production rollout.

### Onboarding from the installed ODS contract

Preparation can now use `--install-dir` and `--native-home` instead of a
manually written `--answers` file. `pixel-native-onboarding.py` reads a private
owner `.env`, verifies the selected Pixel checkout, hashes the ODS plugin and
calls the existing shared `_ods_pixel_write_onboarding` and
`_ods_pixel_write_operations_policy` functions. This retains the shared
concrete-model selection, `ods/current` alias, context/output budget, reasoning
defaults and existing same-model output override. It does not generate or reset
credentials, infer a different model, or modify the installed environment.
The chosen home must be new and its parent must already exist.

The first full run exposed the manually supplied empty Operations policy in the
older fixture. Automatic onboarding now generates the complete shared policy.
That also exposed a readiness mismatch: the fixture-only `host`/`host.os`
identifiers do not exist in the actual ODS policy. Production readiness and its
joint fixture now use `ods-host`/`host.os-release`; earlier readiness claims
using the invented identifiers did not prove compatibility with that policy.

Related regression: 132 passed, one live skip. Real complete preparation with
the beta-selected source and generated onboarding/policy passed in 82 seconds.
Joint privileged service qualification with the corrected identifiers passed
six tests in four seconds; all three jobs were confirmed absent afterward.
The onboarding unit tests execute the shared renderer and policy writer, not
copies of their model-selection rules. Subsequent qualification added the shared
search-provider selector and pinned Parallel plugin provisioning. New onboarding
defaults to `parallel-free`; existing choices are preserved. Protected packaging
checks the cached archive and exact extracted tree before including the plugin.
Real acquisition, plugin loading and runtime packaging passed in 75 seconds.
This does not prove a remote search request.

Automatic preparation now provisions missing dashboard, Open WebUI and model
relay credentials before onboarding. Existing distinct valid credentials are
preserved, never rotated. Duplicate assignments, malformed or reused credentials
stop preparation. Updates retain unrelated environment bytes and a private backup,
and refuse concurrent edits. Focused environment, preparation, activation,
onboarding and configuration regression: 81 passed, including rejected plugin
identity, symlink and archive substitutions. Real preparation without any
pre-provisioned credentials passed in 82 seconds, including default Parallel
plugin loading, sandbox qualification and protected runtime packaging. This run
asserted the original model/context and private pre-credential backup. The main
shell integration is described in the next qualification entry.

No complete production install/update/reboot or Portal qualification is claimed
by these preparation tests.

### Initial main-installer entry point (2026-09-20, qualification pending)

`install-macos.sh --pixel` now connects the resolved base stack to native
preparation and protected activation. This is an explicit initial-install path,
requiring `PIXEL_LICENSE_ACCEPTED=true` under the applicable agreement. It refuses
Intel, root execution and existing/partial protected native state before the main
installation phases. It is not yet the migration/update entry point.

The entry point selects real native Node/npm executables, including Volta's
underlying executables rather than its shims. When no suitable pair exists it
uses Homebrew node@24, then verifies again. The default source ref matches the
shared beta installer; an explicit full `PIXEL_SOURCE_REF` is forwarded. It
resolves the base Compose project and local Docker socket, pulls and probes a
Linux arm64 Node transport image, then binds its immutable image ID to the native
layout. It never starts a second OpenClaw gateway in Docker.

After preparation, activation starts the shared services, activates the protected
native gateway and waits for health. It now also recreates Open WebUI with the
Pixel Edge routing overlay, a previously missing activation step. The main
installer retains the three ordered Pixel fragments in `.compose-flags` only
after success. Interactive feature selection cannot re-enable Hermes/OpenClaw
over an explicit Pixel selection.

Verification: 170 related tests passed and the shell passed `bash -n`. These
include orchestration fixtures, not a real complete main-installer run. Real
Docker qualification with `node:24-bookworm-slim` passed in 40.45 seconds:
admission before gateway startup, later health and immutable nested HTML preview.
Real owner Node/npm discovery selected the underlying Volta runtime correctly.
No production native installation was replaced. Remaining gates include full
initial install, existing-install migration/update, restart/reboot behavior,
Portal tool workflows, recovery and final PR review/merge.

### Native Compose selection through cache rebuilds (2026-09-20)

The shared resolver and macOS CLI now recover the three ordered native Compose
fragments from a matching successful initial preparation/activation record.
Previously removing `.compose-flags` could lose the native services because
their fragments intentionally remain disabled for generic extension discovery.
Existing absolute or out-of-order occurrences are normalized to the installed
relative paths, after ordinary overrides, matching the initial installer order.
Partial, inconsistent or unreadable activation records stop resolution rather
than returning an apparently complete base-only stack. These owner-side records
select fixed Compose files; they do not authenticate protected runtime custody
or prove current health.

`ods update` now verifies that the pinned ingress image ID still exists locally
and excludes that one service from registry pulls. A local `sha256:` image ID
cannot be pulled as a registry reference. Missing images or incomplete native
service selections fail before pulling/recreating; this does not yet implement
transport image replacement or missing-image recovery. Other services retain
the existing pull/retry behavior. Native runtime upgrades remain separate work.

Verification: 193 related Python tests passed, including execution of the actual
shared resolver and CLI selection/pull functions against fixtures. Existing CLI
Compose-failure, mode-routing and update-retry suites passed; shared resolver
regression passed 38 cases. Shell syntax and whitespace checks passed. A read-only
check found the production Pixel Edge, ingress, preview and model relay containers
healthy; these results do not qualify a new complete installation or reboot.

### Existing installation migration audit (2026-09-20)

Read the active system gateway definition, then its actual selected configuration
rather than assuming the initial-layout path. The deployed runtime is still the
older single `pixel-ods` plugin adaptation, not the complete shared-renderer
plugin set now prepared for new installations. Its Pixel agent uses the existing
workspace; no explicit agentDir or session-store override was present. Container
health alone therefore does not establish parity with the complete new setup.

The protected runtime updater currently qualifies only the two reviewed
stream-progress/workspace-root patches. It intentionally requires the old plugin
count, mapping and configuration. Migrating this deployment requires a joint
runtime/configuration/service transition with preserved workspace, session state,
model settings and credentials, plus recovery. Merely adding a generic release
name to the current patch gate would not supply that transition.

Until that transition is implemented, the base macOS installer now refuses an
existing local native Pixel directory even when --pixel was not passed. This
prevents the ordinary reinstall path from changing files/environment or selecting
the default agent before native migration checks. It also refuses a broken
symlink at that location; nothing is deleted or reset. The installer-specific
suite passed 27 tests including executable shell guards. This restriction is an
explicit unfinished migration requirement, not a completed update workflow.

### Legacy configuration candidate preservation (2026-09-20)

The native configuration preparer now accepts an explicitly selected previous
configuration and state directory for the audited legacy single-Pixel-agent
layout. It renders the complete new plugin configuration first, then preserves
the existing ods/current provider, gateway token, workspace, optional agentDir,
explicit session/channel settings and ordinary agent preferences. The shared
renderer retains control of tools, sandbox and context-injection fields; this
does not silently reapply the legacy unrestricted tool settings. Other legacy
plugin sets, multiple agents, different model aliases or gateway bindings require
separate migration review rather than losing those settings implicitly.

The private migration record binds the candidate configuration hash, canonical
previous configuration hash and original state directory. The state directory
must be preserved by a future joint activation because implicit session/auth
paths depend on it, not only on the workspace. It is not activation authority.
The ordinary new-home layout builder explicitly rejects migration candidates.
No installed workspace, sessions, configuration or service is changed by staging.

Verification: 116 related unit tests passed. Real staging, actual plugin loading,
service/runtime packaging and configuration relocation using the active legacy
configuration passed in 54.59 seconds, with model/provider and gateway auth
preserved and installed configuration bytes unchanged. This fixture uses an empty
Operations policy and an image identity placeholder, so it does not qualify
broker actions, sandbox execution, service activation or rollback. Those checks
must use the complete generated policy and real image in the migration flow.

### Complete migration policy and legacy relay key (2026-09-20)

The migration fixture now invokes the shared onboarding/Operations policy writer
against the installed ODS environment and qualifies the actual sandbox image.
An optional existing workspace argument to the shared policy writer preserves
the legacy workspace in `ods-host.writableRoots`; the default Linux/new-install
path is unchanged. This replaces the earlier empty-policy/image-placeholder
qualification described above.

The real installed environment exposed a separate legacy constraint: its model
relay key exists in the active gateway provider, not in the main `.env`. Explicit
migration onboarding can reuse that key after checking the local relay endpoint,
port and key format. A conflicting configured key or nonlocal provider is refused.
Both source files are checked again for drift. No key is rotated or persisted to
the installed environment during this staging operation.

Model budgets are recomputed after retaining the old provider metadata, avoiding
limits derived from a different newly rendered context. The complete original
provider settings, including its timeout, are retained afterward. The fixture's
old 16K-specific tool-result assertion was replaced by the context-derived
contract; the active deployment uses a 64K context.

Verification: 130 related tests passed; the shared Linux/WSL host suite passed
294 cases in Docker. Real migration preparation with full generated policy,
qualified sandbox image, actual installed configuration, plugin loading and
service/runtime packaging passed in 50.74 seconds. It preserved provider/auth
settings and installed configuration bytes. This still does not start the
Operations services or activate/recover the migration; joint activation and
the full reboot/Portal workflow gates remain outstanding.

### Credential Recovery During Preparation

Native environment persistence can now import the existing local model-relay
credential from a private gateway configuration, rejecting foreign endpoints,
conflicting credentials, duplicate bindings and source drift. Onboarding uses
the same validation. This optional import is available to the migration
executor; it does not itself activate or migrate the installed services.

Initial preparation automatically restores its credential backup when a later
preparation stage fails. Recovery compares against the exact bytes the writer
published, not a later read that could include owner edits. Concurrent edits
leave the environment intact and set `credentialRecovery: review-required` in
the preparation receipt. Backups remain private and are retained for review.

The real migration fixture qualified importing and restoring credentials in an
isolated copy of the installed environment, plus rendering, plugin loading and
runtime/service packaging, in 66.68 seconds. Neither the installed environment
nor the active gateway configuration was changed. Joint service activation,
recovery after activation, reboot and Portal validation remain outstanding.

### Joint Initial-Service Failure Handling

When initial Operations/Manager/Promoter startup succeeds but gateway activation
fails, the installer now stops and disables the approved new services in reverse
order before handling admission. An uncertain stop retains the Edge hold and a
recovery-required journal; it is not reported as a restored installation.
The same stop implementation handles a partially failed service startup.

Binding the approved service set also renders the gateway policy with writable
request/cancellation queues and the native service sockets. Service programs,
private broker state, approvals and result projections remain write-protected.
This fixes missing sandboxed gateway access to the newly introduced queues;
full gateway-to-broker behavior still requires the complete deployment test.

Verification: 963 owner-side macOS tests passed (15 opt-in tests skipped), and
the root live service fixture passed all six cases, including actual launchd
startup/readiness followed by reverse-order stop and disabled-state checks.
These checks do not claim completed legacy migration or reboot qualification.

### Joint Migration Planning

`make_migration_plan` now binds the independently checked legacy selection to a
complete candidate gateway definition, versioned configuration, runtime bundle,
service bundle and native service policy. It retains the active HOME and state
root, checks the selected artifacts again after planning, and leaves all live
files untouched. Initial-install and narrow runtime-patch entry points explicitly
reject this plan: only a joint activation/recovery executor may apply it.

Verification: 968 owner-side tests passed (15 integration skips). The real
migration preparation/planning fixture passed in 79.56 seconds using the active
configuration, with no installed configuration mutation or candidate publication.
Joint activation/recovery remains the next implementation gate, followed by
end-to-end Portal, restart/update qualification and PR review/merge.

### Durable New-Service Stop Evidence

The service installation journal now preserves approved plist snapshots, service
identity, attempted startup order and durable process-tree stop witnesses. The
service adapter can be reconstructed from that protected record and confirm an
already stopped job without relying on an earlier Python object's memory. It
still refuses missing/stale witnesses, changed definitions or uncertain process
absence; this is not a blanket acceptance of `launchctl print` returning absent.

Verification: 975 owner-side tests passed (15 opt-in skips). Six root live service
tests passed in 4.52 seconds, including start, stop, reconstruction from recorded
definitions and repeat stop using disk-backed witnesses. This proof is within
the same boot session. Whole-machine reboot recovery and the protected joint
migration executor remain outstanding; no production migration was applied.

### Joint Activation Hooks

The existing gateway/access/relay activation engine now has migration-specific
hooks: start the approved new service set after file/owner-receipt transition,
and restore that set from its protected journal before restoring old files.
Migration owner-receipt relocation uses the actual previous configuration,
not the candidate's staging configuration. Planning retains those previous
bytes for the rollback contract. Ordinary runtime patches retain their existing
behavior.

The outer migration publication/authorization and crash-recovery entry point is
still incomplete, so public install/upgrade commands continue to reject the
migration plan. Verification: 996 tests passed with 15 opt-in skips. Six root
live service tests passed in 3.43 seconds, including the installer's journal-based
service restoration using real launchd definitions and durable stop witnesses.

### Protected Joint Publication

The internal `migrate_install` executor now shares the existing controller lock,
snapshot journal, immutable publication, activation and reproof path. It
requalifies the joint selection under lock and requires already persisted,
distinct credentials matching the retained relay identity. The public CLI has
not enabled this path yet; owner-side environment/Compose orchestration and
whole-transaction failure qualification remain required.

Protected recovery context now includes the old configuration bytes and native
service selection. Recovery validates preserved state and exact installed plugin
mapping without requiring staging directories. Both ordinary rollback and
interrupted rollback stop the new service set before restoring old files.
Candidate readiness includes all three native services before admission opens.

Verification: 1034 owner-side tests passed (15 integration skips). The real
migration preparation, planning and recovery-context check passed in 84.23
seconds without changing the active installation. This does not qualify a live
full migration, a reboot, or the final Portal workflow.

### Persistent Migration Preparation and CLI

`pixel-native-prepare.py migrate` stages the legacy candidate, services and
runtime and writes a private preparation receipt. It preserves the active
configuration/environment and detects source drift. It requires explicit
licensed source/runtime inputs and does not start or replace services.
`pixel-macos-access-install.py migrate-native` defaults to a read-only plan;
`--activate` is an explicit root-only invocation of the joint executor.
This low-level entry point is not yet the complete owner-side migration flow.

A real persistent preparation and CLI plan completed on the development Mac.
The installed legacy stack has an unmanaged ingress container (no Compose
project/service labels), and its gateway lacks the Docker history transport
bindings used by the new runtime. Owner-side migration must adopt/recreate this
ingress and bind the qualified transport before live activation. Preserve the
old container and Compose selection for rollback; do not treat the successful
native plan as proof that the Docker half is migrated.

### Migration Transport and Retained Docker State

Joint activation now requires explicit Docker executable, Compose project,
immutable ingress image and owner UID/GID selection. Before publication the
executor checks the candidate gateway bindings and requires exactly one running
Compose-managed ingress with that identity. Read-only plans may omit transport;
activation cannot. The native policy includes the selected Docker executable.

The new Compose ingress has a distinct generated name, so preparation need not
stop the existing unmanaged ingress. Retention helpers journal before stopping
and renaming its exact container ID and restore that ID without deleting data
or touching the separately managed ingress. Their caller must first qualify the
new ingress and coordinate the complete stack rollback.

Management selection recognizes a completed legacy-native migration receipt
at the standard installed preparation directory. It removes the obsolete VM
link from the selected flags, not from disk, and restores native fragment order.
Incomplete receipts remain an explicit recovery error.

Focused transport, access, Compose and management tests: 518 passed, one opt-in
skip. Isolated real Docker startup, host connectivity and immutable HTML preview
publication passed in 35.95 seconds. These checks do not establish production
migration readiness. The existing ingress uses a separate named volume for its
history; the owner migration still needs to preserve that data and coordinate
environment, stack and protected-runtime activation before publishing readiness.

An isolated real Docker retention/recovery test also passed in 20.54 seconds:
the running fixture was stopped and renamed, then restored from the pre-rename
checkpoint using the same container ID. The fixture was removed afterwards;
no production container was stopped by this test.

### Existing Docker Storage

After preparing a legacy candidate, `pixel-native-prepare.py storage` takes
`--preparation`, `--docker` and `--project`. It inspects the existing ingress,
preview and Edge containers, verifies their workspace and shared-volume topology,
then stages a private `storage.compose.json` and records its digest and container
IDs in the preparation receipt. It runs only Docker inspection commands.

The override declares all four existing volumes external: history/runtime,
published previews, preview sockets, and transition state. The management
resolver requires the same storage digest in preparation and completed activation
receipts and applies this override last. Thus it cannot silently substitute new
empty volumes after a completed migration. A migration coordinator must still
hold admission and stop the old writer before activating a replacement using
the same history volume; staging alone is not an activation.

This storage preparation completed against the actual development installation.
Docker Compose successfully rendered the complete 34-service selection with all
four exact external-volume bindings, without starting or replacing services.
Preparation, storage topology and management tests: 98 passed.

### Admission During Docker Replacement

`start_infrastructure` accepts an optional approved admission token/revision.
In this mode it re-acquires that exact hold on the replacement Edge and requires
zero streams, blocked admission and the unchanged revision. It neither releases
the hold nor substitutes a new token. Ordinary initial installs still require
idle admission. Credentials and tokens travel over stdin, not process arguments.

Focused Compose/activation tests: 52 passed. An isolated Docker test acquired a
hold, recreated Edge against the same transition volume, proved the hold survived,
then released the original token and verified gateway connectivity and HTML
publication. It passed in 38.97 seconds; temporary resources were removed.
The complete owner-side migration coordinator still needs to connect this step
to Docker rollback, protected native activation and final management receipts.

### Docker Handover Sequence

`migrate_infrastructure` now joins storage revalidation, durable admission intent,
hold acquisition, retention of the old writer, replacement startup and health
verification. It returns with admission held. Failures retain the recorded token,
last attempted phase and recovery requirement; they do not release admission.
The caller must stage approved previous Compose definitions before invoking it.

`finish_migration_infrastructure` verifies the same Edge and healthy services,
journals release intent, then releases that exact token. A lost release reply is
recovered by replaying release, not by acquiring a new token. Its completion means
the Docker handover is ready, not that protected native activation is complete.

Focused preparation, stack, handover and activation checks: 129 passed. The real
isolated Docker test verified release plus replay from the pre-release journal,
then model-list connectivity and HTML publication: passed in 39.14 seconds.
Inspection of the actual protected installation found no pending transition,
policy activation, runtime upgrade or service installation journal at that time.
Production migration and its full owner-side rollback entry point remain open.

### Approved Docker Recovery Definitions

`pixel-native-prepare.py rollback --preparation ... --docker ... --project ...`
stages a private `rollback.compose.json`. It compares the current Compose service
hash with each running container's label before and after rendering, checks
container identity and mounts, pins the running image IDs, removes rebuild and
dependency actions, and preserves existing volumes/networks as external resources.
The preparation receipt binds the resulting digest and selected images.

Independent legacy services can use different networks under the same `default`
alias. The snapshot assigns unique logical aliases while retaining the exact
Docker network names and attachment options. Compose's rendered JSON already
escapes literal dollars; the snapshot retains that escaping unchanged.

The actual Edge/preview recovery snapshot was staged and validated by Docker
Compose without replacing services. Focused checks: 135 passed. An isolated
container confirmed rendered-JSON replay preserves literal dollar values.
This prepares rollback input; it is not proof of a completed rollback transaction.

### Docker Recovery Execution

The handover now refuses an existing managed ingress and verifies the replacement
image, UID/GID, Compose labels and exact history volume before reporting readiness.
Its journal retains the previous ingress ID, approved storage and workspace.

`restore_migration_infrastructure` restores the approved Edge definition and
reacquires the recorded hold, stops and verifies the new writer, restores the
retained old writer, then restores previews. It rechecks the storage topology and
health before releasing admission. It refuses rollback once successful-handover
release has begun; lost rollback-release replies reuse the original token.
Preparation receipt replacement now also fsyncs its parent directory.

Focused checks: 150 passed. The isolated Docker fixture exercised the full
handover and rollback with real containers and a live host endpoint, verified the
old writer resumed, and fetched the same published HTML afterward. It passed in
76.18 seconds and removed its temporary resources. The fixture uses its own
Compose runner; binding the production owner command to the private rollback
snapshot and protected native activation remains pending.

### Atomic Legacy Environment Preparation

`pixel-native-prepare.py environment` verifies the staged storage/recovery
digests and the running legacy ingress, including its read-only gateway config
mount and existing gateway credential. It derives native Compose bindings from
that installation and imports the already-used local relay key. Existing
dashboard/WebUI credentials must be valid and distinct; none are regenerated.

The combined change is one atomic environment replacement. Before publishing,
the preparation stores private planned-after bytes and a digest-bound intent;
the environment helper preserves the original file as a private backup. Both
copies remain available for exact-byte recovery without overwriting owner edits.
No Docker or launchd service is restarted by this preparation command.

The actual installation completed this step. Its original environment content
was verified unchanged as a prefix, private backup/after bytes and receipt digest
matched, and a read-only joint native plan passed with the explicit Docker
transport. Existing Pixel containers remained healthy without restart. Focused
environment/preparation/Compose/activation/management tests: 189 passed.
The production service handover and protected activation are still pending.

### Actual Docker Handover and Native Recovery

`pixel-native-docker-migrate.py` now connects the prepared environment and
digest-verified storage/recovery snapshots to the handover helpers. It defaults
to planning; `--apply` records a private durable journal, attempts rollback on
unreleased failures, and refuses to repeat an existing attempt automatically.
Six command-level cases cover planning, artifact drift and rollback boundaries.

The actual Docker handover completed. Installed Docker source files were synced
with backups, the managed ingress/Edge/preview became healthy, and the old ingress
was retained rather than deleted. The following protected native activation
failed because preparation reset the existing full-access fields to sandbox
values. Owner-receipt rollback then incorrectly rejected a no-op restoration
whose receipt was already bound to the approved previous configuration.

The receipt no-op now validates the already-bound destination without accepting
or changing the unused candidate policy. Actual protected recovery completed,
access reproof passed, and the prior gateway was restored with no pending upgrade.
Preparation now preserves the five explicit access-mode fields while retaining
the new tool definitions. Focused migration/receipt checks: 141 passed, two skips,
19 subtests. The old attempt and its configuration were not overwritten.

A corrected preparation exists separately. Its runtime bundle digest is unchanged
but its service/configuration digest differs. Retrying requires verified retirement
of the restored attempt and distinct candidate configuration revision handling;
runtime-digest-only config filenames must not overwrite the preserved failed
candidate. At that checkpoint the new native runtime was not active.

### Verified Migration And Preview Follow-Up

The restored attempt was subsequently retired through the protected retirement
command. Configuration filenames now distinguish revisions of the same runtime
bundle without overwriting the failed candidate. The corrected migration
completed, including access reproof, with all six native launchd jobs running.

`lib/pixel-native-finalize.py --preparation PATH --docker-preparation PATH`
publishes the owner-side management selection after read-only sudo verification
of the protected completed upgrade, service selection and running jobs, followed
by Docker health checks. It preserves external volumes and keeps corrected
service identity separate from the earlier Docker preparation. This selection
does not grant privileges. Existing destination receipts are never overwritten.
The installed selection resolved a valid Compose configuration with the native
fragments and retained external storage, excluding the previous VM override.

A real model request wrote and read a file. A subsequent request created the
snake-game HTML but exposed a missing native preview transport setting: the
generated plugin configuration defaulted to the Linux Unix socket. Native
preparation now explicitly selects `workspacePreviewTransport: docker-desktop`.
After the same scoped correction locally, the model published its existing HTML
through Edge in 42.8 seconds, with HTTP 200 and verified content readback.
Browser inspection confirmed the canvas rendered, restart worked, and no console
errors were reported. This is not a guarantee of every generated game's quality.
Access proof was refreshed after the live configuration change.

Relevant native regression: 940 passed, 7 opt-in tests skipped. Additional access,
gateway, settings and custody tests: 303 passed, 6 skipped, 67 subtests passed.
Installed CLI stop/start of Edge and restart/recreation of native ingress also
passed, with all three Docker services healthy afterward. A separate disposable
OpenClaw launchd job passed install/restart/stop/bootstrap and process identity
qualification. Linux/WSL `ods-cli` likewise manages Docker, not host Pixel system
services, in its normal stop/start commands; macOS retains that division.
Clean-machine installation/reboot still remain release gates; neither Intel support nor universal hardware
qualification is claimed. PR publication and merge are still pending.

The macOS smoke workflow now runs the native installer/recovery suite. Its exact
selection passed locally: 1,222 tests, 15 optional skips, four subtests. The real
renderer/loader qualification passed with an explicit assertion for Docker
Desktop preview transport. Shared Linux host-installer regression passed all
294 checks in an isolated container with source mounted read-only and Python
bytecode cache redirected to `/tmp`.

### Managed Update Command (Qualification In Progress)

Current release gate: a real managed update exposed a first-install-only service
activation path. Managed updates now snapshot the existing manager, promoter and
operations files alongside the gateway/access/relay files in the same recovery
journal. All six jobs stop before replacement; native helpers start before the
gateway. Recovery restores the previous service definitions, policy, code and
installation receipt without reprovisioning accounts or deleting runtime spools.
Unhealthy prior deployments and incomplete file sets fail during preflight.
On 2026-09-21, a fresh preparation passed real protected activation on the
development Apple Silicon Mac. All six managed services were included; the
owner finalizer published the verified selection and refreshed Docker clients.
The resulting access status was available, not pending, full-access and runtime
verified. Models, credentials, history and workspace were retained. Recovery of
the earlier failed attempt had restored the previous runtime and verified
full-access execution without deleting owner data. This single-machine update
does not qualify clean installation, reboot or every supported hardware tier.

`PIXEL_LICENSE_ACCEPTED=true ./installers/macos/ods-macos.sh update-pixel`
uses the installed source's pinned Pixel release. It downloads and prepares the
candidate as the signed-in owner, activates it through the existing protected
joint migration, and publishes a verified atomic management selection. The
ordinary `update` command still updates Docker images only. `--prepare-only`
stops before protected activation; preparations are retained under
`data/pixel-native/update-*` for diagnosis and recovery.

The coordinator can also be invoked from a newer source checkout with explicit
`--install-dir`, `--ods-source`, and `--license-authorized` arguments using
`installers/macos/lib/pixel-native-update.py`. It pins subprocesses to the active
gateway's local Docker socket and restores inherited Docker environment overrides
on exit. It does not reset credentials or remove models, history, or workspace.

New runtime bundles include a content-checked service bundle binding so a
services-only change receives a different deployment identity without relaxing
the upgrade journal's distinct-version requirement. Original bundles remain
readable. A failed activation must use the existing protected recovery procedure;
do not run a fresh install or discard its journal. If activation completed but
management publication failed, `pixel-native-finalize.py --update-existing
--preparation PATH` re-verifies protected completion before retrying publication.

Finalization also recreates only `dashboard-api` and `open-webui` from the
resolved native stack, with dependencies and data volumes left in place. It
rejects a remaining `pixel-edge` host override and requires an HTTP health probe
from inside the dashboard container. A client refresh failure retains the
published selection; repeating finalization re-verifies activation and retries
the client refresh instead of reinstalling the native services.

This command's orchestration and failure paths are covered by tests. Post-update
Portal artifact creation/preview and clean-install/reboot qualification remain
release gates. A real game-generation request exceeded the 900-second stream
observer timeout; later backend evidence showed interruption and an idle model
slot, not successful artifact creation. Its cancellation caller also timed out,
so that observation does not prove an acknowledged cancellation response. Draft
PR 6155 contains the implementation and is not a claim of completed qualification.
