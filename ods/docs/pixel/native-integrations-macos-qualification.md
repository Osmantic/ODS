# Native Portal integrations qualification

Status: native lifecycle implementation and owned-file regression coverage;
**physical Apple Silicon qualification has not been completed**. Linux/WSL
metadata-driver results do not establish launchd, Darwin ACL, Seatbelt, reboot,
Metal/model or native process behavior.

This matrix covers the native Portal runtime, scheduling and Coffee integrations.
The candidate includes the native Source Broker path for Google/Calendar and
reviewed Connected apps, alongside the Linux/WSL route. Native custody, first
connection, repeated updates, retained history and retirement now have Linux
owned-file coverage. The Darwin catalog/relay and these owner journeys still
require the physical run below; an offline pass is not Mac acceptance.

The previously accepted Windows candidate (`fa2e`) used Source **4.3.46**
(`bf6db06c`), with 51 installed SDK files and 26 canonical repair receipts. Its real Portal
Calendar-count and scheduled-task journeys passed. The PR qualification note
identifies the exact composed candidate and records its final Windows update
result.

## First physical run

Start from the exact source commit named in the PR qualification note, with
the checkout outside the install directory. Keep the local container engine
running. For a first native installation, use a disposable Apple Silicon Mac
or macOS guest with no existing native Pixel deployment. A second user account
or a different `ODS_HOME` still shares the system launchd services.

On a Mac that already runs native Portal, retain the installation, model and
private receipts and use the existing-install procedure below. The native
initial-install preflight deliberately reports
`existing-native-pixel-requires-migration-or-recovery` for an existing deployment;
that is not an instruction to remove it. The general `ods-macos.sh update`
command uses the installed source.

Before updating an existing MacBook installation, record the selected model,
connected accounts, integration choices, existing tasks and retained results.
Do not retire or reinstall a working installation to perform this test.

For a checkout at `~/src/ODS` and installation at `~/ods`, record the source and
platform first:

```sh
git -C "$HOME/src/ODS" status --short
```

If there are local edits, stop and preserve them. Replace `QUALIFIED_PR_HEAD`
below with the reviewed commit supplied in the PR qualification note. Fetch
the PR and select that revision; a moving branch name is not an acceptance
identity:

```sh
git -C "$HOME/src/ODS" fetch origin pull/8293/head
git -C "$HOME/src/ODS" switch --detach QUALIFIED_PR_HEAD
git -C "$HOME/src/ODS" rev-parse HEAD
git -C "$HOME/src/ODS" status --short
sw_vers
uname -m
docker context show
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
```

Record the actual `rev-parse HEAD` output with the results and confirm the
checkout is clean. Use that recorded revision for the journeys below.
For an existing installation, use **Update the existing MacBook installation**
below. On a disposable Mac or guest for a fresh-install test, run these
separately, stopping at the first error:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/src/ODS/ods/install.sh" --pixel --preflight-only
```

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-install.py" \
    --install-dir "$HOME/ods" --preflight-only
```

On the disposable Mac or guest, proceed after both preflights pass.

```sh
ODS_HOME="$HOME/ods" bash "$HOME/src/ODS/ods/install.sh" --pixel
```

Choose the local test model and installation options when prompted. For a
custom install directory, use the same path for `ODS_HOME`, `--install-dir`
and the status command.

After installation:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/ods/ods-macos.sh" status
```

In Portal, first confirm chat and Settings load. Use only invented data for
these acceptance journeys:

1. Save a test integration, reload Settings and confirm the same choices.
2. Under Ask first, request one exact test-note append. Confirm a pending
   request and unchanged note; Approve must also leave the note unchanged.
   A new explicit retry must append exactly once. Repeat with Decline and
   confirm the refused action never changes the note.
3. From ordinary chat, schedule a one-off calculation a few minutes ahead,
   explicitly with no tools or external notifications. Close the browser,
   let it run, reopen Portal and inspect its retained result. Create another
   one-off task, pause it before it is due and confirm it remains Paused with
   no run. Remove only those test tasks through the normal controls.
4. Complete the apps, local MCP, isolation and lifecycle cases in the matrix
   below. Restart the Mac, wait for Docker Desktop, rerun status, and confirm
   chat, saved integrations and retained task results survive.

Record the first failing step, exact error and source commit. Share sanitized
results, never credentials, account content or private receipt files. Passing
the initial UI journeys does not replace the remaining physical matrix.

### Update the existing MacBook installation

From the normal owner's terminal, keep Docker Desktop running and invoke the
reviewed checkout's updater directly:

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-update.py" \
    --install-dir "$HOME/ods" --ods-source "$HOME/src/ODS/ods" --prompt-for-sudo
```

This uses the candidate's packaging and verification helpers. The older
installed `ods-macos.sh update` does not select an unmerged checkout. Substitute
the actual installation directory consistently. Preserve any preparation
directory and private recovery journals. If it stops, record the first error
before another attempt; do not force a reinstall, delete receipts or reset the
model/connection state.

When `--ods-source` selects an external checkout, the updater also prepares the
owner-side host-agent code and both Portal web images before protected
activation. It retains the installed Compose routes, environment and local
extensions. The public file inventory excludes owner configuration, model
weights, history and credentials. Images are built from the staged bytes;
preparation leaves the running containers and their selected tags unchanged.
After Core activates, public files and clients change under one owner admission
hold shared with ingress finalization. Original public files are retained by
rename, and the prior web image IDs and host-agent definition remain recorded.
Readiness requires the expected images, mounts, health and authenticated host
route before admission reopens. This does not update unrelated ODS services.

An interrupted client handover does not mean Core activation was rolled back.
Keep its preparation and the held operation. After resolving the reported
failure, resume only that preparation from the same source checkout:

```sh
/usr/bin/python3 "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-update.py" \
    --install-dir "$HOME/ods" --ods-source "$HOME/src/ODS/ods" \
    --resume-preparation "/exact/path/printed/by/the/update/preparation"
```

This command requires proof that protected Core activation already completed.
It resumes owner finalization; it cannot activate a prepare-only result or
recover an unrelated deployment. The installed-source `update-pixel` route
continues to update the native runtime from already installed public code.

An initial install can also retain an owner-side `protected-activation` error
after the protected services actually completed. The explicit initial-install
recovery accepts that receipt only for readback: it must prove the exact
protected installation and Source state, check the selected client routes, and
repeat the protected proof before publishing the owner selection. An incomplete
or changed protected activation still refuses recovery; the original receipts
are retained and protected activation is never replayed by this path.
When that recovery already holds Edge admission, the readback also checks the
private recovery journal, selected configuration, and exact live hold token and
revision. An unrelated or changed hold remains a refusal.

After a successful update:

```sh
ODS_HOME="$HOME/ods" bash "$HOME/ods/ods-macos.sh" status
```

Record each owner journey separately:

1. Portal loads, the expected installed model answers an ordinary chat, and
   previous model choices, conversations and integration settings remain.
2. Settings → Connected apps offers the native Google route. Use standard
   sign-in if this deployment's Google app is provisioned. Otherwise, Advanced
   may use a Desktop client the owner already manages. For this test select
   only Calendar read. A maintainer's local test client is not the official
   distributed app; Google's production verification remains external work.
3. Pick an explicit date in the primary calendar and ask Portal for only that
   day's event count, without names/details or changes. Compare with that same
   primary calendar and owner-local day. Verify connection status and native
   Calendar-read evidence separately; a plausible answer alone is not a pass.
4. Create one clearly named disposable dated task a few minutes ahead, asking
   for `14 + 8` with no tools or notifications. Close the browser before it is
   due, then reopen Portal and verify one retained result. Remove only that
   task through the rendered confirmation. Previous tasks/history must remain.
5. Complete Notes approval, local MCP, ntfy and configured-app cases below.
   Disabled or declined tools stay disabled or declined. Approval itself makes
   no edit; the matching explicit retry consumes it once.
6. Restart the Mac, start Docker Desktop and recheck saved choices, chat,
   retained results and the native connection route. Record actual launchd,
   Seatbelt and ACL behavior separately from Linux metadata-driver evidence.

Share the exact source commit, OS/chip, model, access mode, per-journey result
and first failure. Do not share client JSON, callback addresses, tokens,
account contents or private receipts. Fresh install and retirement tests belong
on a disposable Mac or guest; another user or install directory still shares
the system services. Results apply to the recorded hardware, model, provider
and access mode.

### MacBook preparation observation, 4 November 2026

The clean starting revision `4525380c411e2dfc85d5664b9f0040154fa2b77c`
was inspected on a physical Apple M5 with 16 GB, macOS 26.5.1 (25F80), arm64.
The existing installation was separate from both the reviewed checkout and the
checkout used for fixes. Existing unrelated checkout edits were left intact.
Docker Desktop was updated from 4.74.0 to 4.98.0 through its normal updater;
the engine changed from 29.4.3 to 29.9.2 and all existing ODS services recovered.

Before ODS activation, the installed Qwen 3.5 9B Q4_K_M at 65,536 context
answered an invented no-tool request correctly in 3 minutes 25 seconds. The
response survived reload and the Docker update. Reading Settings in another
tab triggered the old frontend's stale-conversation warning; closing that tab
and reloading retained the result. This was a baseline observation, not a
candidate conversation-isolation pass.

Private comparisons found unchanged environment, model-selection and active
protected-configuration bytes, including the protected configuration inode.
Both model files retained their inodes and sizes; their initial hashes were
recorded privately. Twenty existing history files remained byte-identical.
The invented QA result and its session index changed normally, and its request
lock disappeared after completion. No existing scheduled tasks or Google grant
were available in this installation to establish preservation of those items.

The reviewed prepare-only command staged its source/runtime, then stopped at
`native-migration-proof-or-authorization-failed`: noninteractive sudo required
owner authentication. Its preparation was retained and Core was not activated.
The proposed owner-client preparer separately passed the real Mac's file/ACL,
Compose-baseline and host-agent binding checks and built both candidate web
images under separate tags, without replacing running services. Preparation
does not establish that a client handover or protected migration succeeds.

After owner authentication, the full updater from
`6dea6ff3517ae0e30e8a0cfeef53e751f6dfb69f` prepared both runtime and clients,
then stopped before activation at `runtime-upgrade-existing-file-missing`.
The existing protected deployment lacked the three newly introduced scheduler
programs. The updater incorrectly treated these new files as required old
files. Inspection also found that the integration lifecycle's ten new paths
were missing from the filesystem adapter's closed addition set, including its
new code directory. The old configuration bytes and inode remained unchanged,
and all eleven existing ODS containers remained healthy after the refusal.
The preparation and private recovery material were retained. Fixes require
complete approved addition families and journaled rollback; silently repairing
missing files in an already capable deployment is not an accepted migration.

The next full update from `adbf9f82c4eaa54ef94d82cbb8b10d7cc22240ac`
passed those snapshots and stopped at
`runtime-upgrade-previous-guard-unqualified`, still before activation. The
installed controller's `locked()` and `status()` matched the reviewed legacy
revision `b0599adb07cedad5f95f8a423180f1407e8c8fdc`; adding an optional wait
parameter to the newer lock had made the exact-AST recognizer reject the old
guard. Compatibility now recognizes that explicit old lock and unchanged
status without executing archived code. Mutated guards remain refused. The
old configuration bytes/inode and all eleven healthy containers were again
preserved. Both failed preparations remain available privately.

The third full update from `a2058e8ff69852f881b507abf82d022541eb3c43`
stopped with `native-joint-migration-failed` after creating a protected recovery
journal. The controller reports `runtime-upgrade-recovery-required`. The
candidate runtime was not published; the old active configuration, model
selection and file inodes remained unchanged, and all eleven containers stayed
healthy. The real protected reader wraps a missing-file error as a custody
error, but the Source journal's live-image verifier caught only the original
file-not-found exception. A real descriptor regression reproduces that mismatch
for the complete legacy addition families. The corrected reader proves absence
again through protected ancestors only for an initially absent, fixed-family
journal image; permission, ACL, link and unknown-path failures remain refusals.

The prepared recovery path also incorrectly required a running previous process
for the newly introduced integrations role. Only the exact approved absent-role
adapter may now prove its missing definition and stopped job. The six existing
roles still require their original live identity. Its journaled regression
forbids stop/start, publication and restoration calls during this unstarted
recovery. The composed installer/runtime/recovery tests passed 1,661 cases
with one platform skip on this Mac. The new absence-reader cases also passed
with the Linux metadata driver, including the actual withdrawal path; these
remain simulated ownership evidence on Linux. At this checkpoint, physical
recovery was still pending. A retained journal must be qualified and completed
before a fresh ordinary update; deleting it or reusing the failed preparation
as if activation succeeded is not a recovery method.

The actual recovery from `a074bc6c1289e2a32e9eeabeb897608f77caf61c`
then stopped with `native-runtime-recovery-failed`, before the preservation gate
or another update. Its preceding read-only Root inspection confirmed schema 5,
phase `prepared`, 68 unchanged existing images and 46 unchanged absences, with
no edge hold or published candidate runtime. The prepared Source path had
constructed its discovery bridge from the candidate binding while the old
protected plist was still active. A regression using the actual binding
verifier reproduces `gateway-installation-changed`. Recovery now selects only
the already-authorized before-image and requires exact equality with current
protected settings. It retains the original lock and outer deadline; the
separate old bridge's bounded lease cannot extend that deadline. Changed owner,
configuration or definition still refuses recovery. The composed recovery,
binding and diagnostic suite passed 1,680 tests with one platform skip on this
Mac; lint and Python 3.9 syntax checks passed.

Generic migration and recovery failures now include only a fixed exception
category and a trusted source-relative file/line. Exception messages, arguments,
local variables, private filenames and subprocess output are not emitted.
The corrected recovery from `7ae1a035cd56e3544ff282e0873b979ca8a7c887`
subsequently completed with status `restored` on this Mac. An independent owner
gate confirmed all ten baseline comparisons, including full hashes and inodes
of both model weights, the selected configuration and controller readiness.
Only then did the same command start a fresh ordinary update.

That update published its candidate runtime but stopped at
`native-connection-sidefile-unsafe`, before the integration service appeared.
Darwin inherited the owner directory's group for a Root-created `gateway.env`;
the producer required either Root:wheel or owner:staff and refused Root:staff
before recording the inode. Automatic rollback restored the old deployment.
The normal owner startup proof succeeded, and the independent baseline gate
again passed all ten comparisons with a ready controller and no pending work.
The independent post-rollback inspection also found all 20 original history
files unchanged, all seven baseline session entries retained, and the original
Portal attempt plus ten stored response chunks preserved in the active store.

A read-only protected inspection confirmed the exact failed context and schema
5 restored archive, with 114 validated image records. The sidefile receipt
matches that context; both child inodes remain unbound and neither is ready.
The retained `gateway.env` is empty, Root:staff, mode 0600 and singly linked;
`onboarding.json` is absent. This does not authorize adoption of an existing
unbound file. Its inode and the original receipt must be preserved through an
explicit qualified recovery before another update. No token or receipt was
removed, and no candidate user journey is counted as passed.

A causal review found the same group assumption in the first Source preparation:
`private/oauth` is created below a directory already owned by the dedicated
broker. An isolated real-descriptor test with explicit Darwin group inheritance
reproduced `native-source-descendant-unmanaged`. The prospective correction
requires the exact prepared parent and child inode, journals the empty child
before normalizing its inherited group, and retains refusal of unknown existing
directories. This is Linux driver evidence; the first real Google connection
still needs to exercise the complete Darwin identity and preparation path.

The `7ae1a035c` CI joined-update case also exceeded its unchanged 180-second
finish bound. A separate controlled baseline/patch pair exercised a narrow
compiled-code cache for two protected helper loaders. Every use still repeats
custody, current-byte and manifest checks, and executes with fresh module state.
The first finish changed from 147.74 to 126.00 seconds; the baseline second
finish refused at 180.00084 seconds, while the patch completed it in 153.28
seconds and passed the complete case. Whole-case totals are not comparable
because the failed baseline did not reach the later assertions. This local
measurement does not establish a CI or physical Mac pass. A subsequent joined
case with a retained sidefile archive failed in its first finish at 180.0019
seconds. Its capsule used 79 records with current public code after-images and
synthetic legacy before-images, rather than the physical attempt's 114 records.
That failure remains recorded separately. A bounded diagnostic trace located
19 of its first 20 broker compilations in the nested Source verifier: the bundle
verifier had not passed its already validated operation to that helper loader.
The correction passes the same operation, rechecks its bundle-root binding,
reads the protected helper bytes again and reuses only the compiled code. Fresh
module globals, source provenance, payload, custody and receipt checks remain.
Ten new real-descriptor driver cases and the existing 49 cache cases passed.
The next joined run first exposed a test reader whose signature omitted the
production reader's size limit; that harness error occurred before either finish
operation. The corrected fixture retains bounded reads and explicit size/type
refusals. The valid rerun still refused its first finish at 180.0020 seconds.
Broker compilations fell from 403 to 135 and their measured cost from 88.02 to
27.34 seconds, but this is still a failed case. Its final stack was in the
test driver's content-hashing replacement for `protected_tree_metadata`; the
production function checks descriptor custody and link topology without hashing
file contents at that boundary. The corrected test driver runs that production
descriptor traversal in an isolated module, models only the disposable Root
ownership and fixed external ancestors, and retains the separate runtime and
bundle hash proofs. The focused Linux packet passed 37 cases; follow-up cases
also prove that the metadata gate cannot invoke the content manifest, accepts
valid internal relative links, and still refuses content drift in a warm lease.
The shared deployment fixture's separate Darwin descriptor incompatibility was
reproduced on the pre-correction tree and is not a physical product result.
No production deadline or validation was relaxed. The corrected-driver run
still refused at 180.0405 seconds, after publication returned and during final
code-only kit verification; the second update had not started. Its cold
publisher material path still compiled both helpers repeatedly. A further
correction is limited to reusing the existing operation only when the freshly
qualified selection and operation identify the same bundle root. Historical
or different-root verification keeps its cold path. Eight focused cases and
the existing public-publisher lifecycle case passed, including a fresh first
read, current metadata/ACL checks, a new cold operation and preserved state.
The complete joined case then passed in 245.50 seconds: its two finish
operations took 65.63 and 79.22 seconds, both within the unchanged 180-second
bound, followed by successful first preparation and retained archive checks.
This is one local 1-CPU Linux driver result with the 79-record capsule described
above, not a physical Mac or CI result. The prior failures remain recorded.

The ordinary physical update from `e911f5e102e69ce4a11702a0f70f19419032463f`
first passed the actual Root Darwin sidefile creation smoke and preserved the
preceding empty file and receipt by rename. All ten original baseline checks
passed before activation. Core then published runtime `d9ed4b464e0dd3e973b796b5f7443898a7babd9b742a36437587ea466ff68a66`,
but Source completion failed. A protected, read-only diagnostic confirmed an
active schema 5 journal, held admission, and all 114 approved after-images
(76 changed and 38 identical). Source kit, private state, logs, identity and
jobs are absent. The two completed owner sidefiles, their Root receipt, and
the earlier preservation archive remained unchanged throughout the diagnostic.
The controller requires recovery; owner client handover has not run.

The real bridge reproduced `connection-runtime-plugin-unavailable` at
`pixel_connections/planner.py:160`, wrapped by `AccessError` at
`pixel_access_bridge.py:2907`. The candidate producer included only the four
active plugins and omitted the required Source and Frontier payloads. The
prospective correction packages those dormant payloads without enabling them,
preserves active paths and order, and checks completeness before new admission.
Historical incomplete bundles remain readable for explicit recovery. A separate
Darwin copy correction gives only newly created public symlinks mode 0755 using
nofollow chmod, preserving inode, ownership and target; the existing immutable
runtime is not repaired in place.

The independent owner inspection preserved both model weights, the original
configuration bytes and inode, all 20 original history files, all seven original
session entries, and the original Portal attempt and ten chunks in the actual
active store. Migration did remove an explicit `contextTokens` preference.
This Mac still declares an effective 65,536-token capacity, but an invented
32,768-token cap reproduced a real preference loss. The correction preserves
the field's value, scope and absence and checks it on new admission. Recovery
continues to validate historical frozen contexts under their original contract.
These corrections have not yet established a successful physical update.

The e911 CI is terminal: 155 successful checks, one failure and one intentional
skip. Both joined-update cases, macOS smoke, all three compaction platforms and
the 26 active-publication cases passed. The failed operation-cache job collected
zero tests because its four file paths were passed as one quoted argument.
The workflow now passes a Bash array; corrected collection found all 74 cases.
No rerun, timeout extension or physical acceptance claim follows from this fix.

The packaging correction was then exercised with the real retained Pixel source,
runtime and candidate, using a separate owner-only destination and the reviewed
component commit `a9ab834712c85a8481fc0752dfbc0ddb8d482dbd`. Normal service and
runtime packaging, source bindings, all 111 Source code entries, OpenClaw config
validation and the runtime plugin probe passed without mocks or Root actions.
The runtime artifact was `4cea654ab1167524bf40fa29f59681187b0b2ede5318ee2f8dac269b649b4974`.
Packaging took 279.17 seconds. A separate real plugin inspection confirmed only
the original four plugins were imported; Source and Frontier remained inactive.
The retained candidate and sidefiles kept their bytes, inodes and modes. This
component preparation did not activate or touch the installation, and does not
qualify the subsequent composed recovery or an installed user journey.

The explicit prepublication recovery uses a distinct seven-object capsule for
the two filled sidefiles and their two Root receipts. Its Linux owned-file
packet passed 238 cases, including the real recovery dispatcher, partial-move
and restored-phase replay, original inodes, prior archive history and strict
Source absence. Sixteen Root-owned support/entry cases passed separately.
Darwin metadata and launchd remain declared driver adapters in those results.
The composed Mac selection/preservation packet passed 126 cases with the one
Root-only case skipped; whole-tree Python lint and Python 3.9 parsing passed.
The historical-mapping fixture initially failed three cases because its mocked
manifest omitted the required `entries` mapping. Correcting that fixture alone
restored all 60 cases in its file; production validation stayed unchanged.

The physical command pinned to `22b9d7448` passed the Root Darwin public-link
smoke: the mode-0700 control was refused to the owner, the new mode-0755 copy
was readable, and the original link and target remained unchanged. Recovery
then refused at `pixel-native-integrations-service.py:239`, before the baseline
gate or another ordinary update. The running service publishes `discovery.json`
beside its socket, while the preservation validator allowed only `control.sock`.
The observed directory and both children had the expected owner and modes.
Code order placed the refusal after sealing the capsule and before phase change,
service stop or moving originals. The next command's protected read confirmed
that exact prefix, including the original inodes and ready bindings.

The correction validates the literal discovery filename through a nofollow FD,
including owner/group/mode, ACL, link count, size, stable bytes/inode and the
closed producer format. Expired hints remain preservable; they grant no execution
authority. Unknown children still fail. The focused Mac packet passed 102 cases,
including the real producer and malformed/custody cases. A separate actual
owner-only read reproduced the old refusal and passed the corrected validator,
preserving the runtime directory, socket and JSON. It made no Root changes.
The 22b9 CI protected-entry job passed 16 cases, and operation-cache passed all
74 after the quoting fix. Its planning fixture omitted `plugins.allow`; declaring
the intended Source-off allowlist fixed both cases, with a separate missing-list
refusal retained.

The physical command pinned to `b4bde0538` passed its initial Root read-only
inspection: schema 5 remained active with 114 exact images, the sealed capsule
contained only its three control files, all four originals remained in place,
and the older archive and ledger were stable. Recovery then stopped all seven
native jobs and refused at `pixel_gateway_service.py:158` while qualifying a
stop witness. The baseline gate and ordinary update did not run. Owner-side
observation still found the candidate plist and both original owner sidefiles;
the exact protected journal phase and per-service witness state require the
prepared read-only diagnostic. No service was manually bootstrapped afterward.

An isolated reproduction identified inconsistent selection for identical service
definitions: observation selected the previous slot, while stopped-state proof
selected the candidate slot. A separate case showed that starting either version
must invalidate both equivalent slots, or a later exited instance could inherit
an earlier tree's proof. The prospective correction pairs only identical plist
bytes and process contracts, selects the candidate slot consistently, and clears
both slots before either bootstrap. It never copies a valid stop witness or
adopts the already-stopped legacy prefix. Eighteen new cases passed with actual
service adapters and file-backed receipt stores; only OS observations were
invented. One additional driver case preserved the stopped legacy prefix,
simulated a new boot with seven new process identities, and required fresh
canonical stop receipts before restoration. It did not adopt the old witnesses
and does not establish a physical reboot or the full Source composition.
Independent review found no material issue. The affected seven legacy
fixtures passed in the isolated Linux environment; six encounter this Mac's
existing runtime before reaching the changed factory. Physical recovery remains
pending, and none of these tests establishes a fresh live stop witness.

The `b4bde0538` CI is terminal with 156 successful checks and one intentional
skip, without failures, cancellations or reruns. Its final active-publication
packet passed all 26 cases in 2,054.24 seconds. This CI result belongs to b4,
does not include the later stop-witness correction, and does not resolve the
physical failure above. The corrected revision requires its own CI and live
recovery evidence.

A fleet update of the composed candidate `a3a1f2164` on an M4 Mac mini (16 GB,
macOS 26.2, colima Docker) published runtime
`28fad3b8f5ba2d1a27a033e847ac4746fb27c18cdb5baf187b498e155e358ae8`, started all
seven candidate jobs and wrote journal phase `active` at 22:56:48. Source
completion stopped with `native-joint-migration-failed
(pixel_access_bridge.AccessError at bin/pixel_access_bridge.py:113)` at
22:59:48, 180 seconds later; the enclosing 900-second transaction still had
more than nine minutes. A read-only Root inspection found the active schema 6
journal with 115 records, the held Edge admission and a `staging` first-kit
publication. Its candidate directory was created at 22:59:34, before its inode
was recorded. An ordinary resume refuses that prefix with
`native-source-installer-first-kit-prefix-changed`, and the empty-stage
quarantine accepted only code-only kits.

Read-only Root timings of the same proofs on that Mac measured 4.6 seconds per
deployment qualification and 3.6 seconds per bundle proof with the shipped
34,651-entry runtime. Counted with the Linux driver, a production-equivalent
first-kit finish makes 278 deployment and 110 bundle proofs, about 1,400-1,700
seconds on that Mac. The first-kit authority now reuses its publisher's proof,
as the code-only authority already did, and its plan rechecks the selection
once more. That lowers the counts to 173 and 48, about 800-970 seconds. Every
guard still re-proves the deployment, bundle and records. The finish bound is
now 3,600 seconds, inside a 4,500-second transaction and a 5,400-second
protected child; the next update's code-only kit is estimated at 1,400-1,700
seconds. The empty-stage quarantine now also covers the first kit.

Candidate chat, Calendar, browser-closed scheduling, Notes approvals, MCP,
notifications and reboot acceptance remain pending. The Mac has not been
restarted for this qualification. With explicit owner approval, a separate local
Desktop OAuth test client and one test user were prepared and the Calendar API
was enabled. No Calendar grant has been completed. Official application
provisioning and verification remain separate maintainer work. These
observations must not be reported as macOS acceptance.

### Scheduled Notes approval on Apple Silicon

Record the source commit, physical model, selected OpenClaw provider and model
alias, and whether the run used the installed Portal or an isolated gateway.
For the managed model route, use the shipped Pixel model relay and the
installer-generated Pixel hook policy. The configuration writer sets
`plugins.entries.pixel-ods.hooks.allowConversationAccess` on the private
candidate. The native runtime reports whether it can restrict a follow-up's
tools and require its first tool call. Record those two acknowledgments
separately when testing a follow-up. A custom provider can report a different
capability set even when it reaches the same model. Keep API keys, private
configuration and note contents out of shared receipts.

1. With a disposable vault set to **Ask first**, schedule a recurring task that
   appends one unique line to an existing note. Close the chat and let the
   scheduler run it. Check for one pending request in owner Settings and
   verify the note is byte-identical. A question in task history alone does
   not give the owner a request to approve.
2. Approve the pending item and verify the file is still unchanged. Let the
   next occurrence run. Verify that it consumed the approval and appended the
   line exactly once. Cancel the task, retain both history entries, and check
   that the following due time passes without another edit.
3. Schedule a read-only request for the note heading. Check that the result
   contains the actual heading, the file stays unchanged and Settings gains
   no approval. For revocation, turn note writing **Off** between occurrences
   and verify that subsequent runs cannot edit the note or queue an approval.

Record whether an owner decision came from rendered Settings or the
authenticated owner API. Track a supported update of an existing launchd
installation as a separate physical qualification.

## Deployment contract

The integrations snapshot publishes nine programs and one owner-run launchd
definition. The composed Source lifecycle includes a separate protected,
code-only installer-support capsule and a fixed 111-file runtime Source
closure. Ordinary discovery selects the actual old owner, gateway, runtime
and configuration inside the bounded, locked operation; it does not initialize
the bridge from candidate fields or relax the protected-program check.

The composition retains the genuine Source 4.3.46 bundle/pins and 26 managed
SDK compatibility repairs. Older Source45 evidence keeps its original identity.
These custody results do not replace the physical Mac checks below. The
controller admits exactly the legacy three-role
or new four-role native service sets, selected from verified complete source
snapshots. A partial set is refused.

The agent endpoint is `/private/var/lib/ods-pixel-integrations/control.sock`.
This runtime is persistent so an owner daemon does not need to recreate a
root-owned `/var/run` path after reboot. Settings remain in the real owner's
`~/.config/ods/pixel-integrations`, outside the gateway's private HOME.

The owner endpoint authenticates one canonical request with fresh 32-byte
server/client nonces and HMAC under a root-provisioned credential. The key and UID
directory are root-owned and grant only the selected owner read/search with
an exact non-inheriting ACL. They do not grant `staff` access. Authentication
has a whole-frame deadline and connection cap. Lost replies never cause an
automatic resend. Replies use a separate authenticated domain bound to the
both nonces and exact request, so a substituted socket cannot forge a successful
settings acknowledgement without the key. This does not encrypt Unix traffic;
private endpoint/alias and relay behavior remain part of the physical gate.
Agent requests cannot select an owner role.

The gateway profiles deny state, credential and log reads/writes in both
modes. This is not a claim of absolute same-UID isolation: arbitrary
owner-selected programs and separately authorized Full Access retain their
existing owner-account implications. Current host-agent Python/LaunchAgent
paths are owner-writable, so a peer PID/path match would not establish trusted
host-agent custody. The protocol does not pretend otherwise.

Readiness checks owner settings and agent tool inventory only. It never
contacts a provider, starts an MCP program, sends a notification or approves
an action. SIGTERM closes admission, reaps owned HTTP workers and local MCP
groups, and preserves effect journals. Native process/descendant behavior
still needs the physical checks below; there is no cgroup-equivalent memory
limit claim.

## Upgrade and preservation

### Optional preparation without activation

The MacBook command above performs the update. To inspect preparation alone,
use the same reviewed checkout with `--prepare-only`. This does not complete
the update or its acceptance journeys. Packaging and verification helpers are
imported relative to that checkout; an older installed helper cannot select
the candidate merely by receiving a newer source directory.

```sh
cd "$HOME/src/ODS/ods"
/usr/bin/python3 installers/macos/lib/pixel-native-update.py \
    --install-dir "$HOME/ods" --ods-source "$PWD" --prepare-only
```

Substitute the actual installation directory for `$HOME/ods`. If protected
verification needs a password, add `--prompt-for-sudo` in a local terminal.
A successful preparation prints its directory and returns the status `prepared`.
Retain that directory and its recovery journals. The existing gateway and
services stay active while the preparer verifies the proposed migration.

Before scheduling a live rollout, check the host agent and both web images.
After signing in, open **Settings → Portal integrations** and verify that
the Dashboard API can read a pending request through the host agent. During
qualification, make a disposable note edit with **Ask first** enabled. Approval
should leave the note untouched until the next matching tool call. Record
separate results for the prepared migration and for this journey through the
running installation.

Legacy recovery schemas 1/2 remain exact. Schema 3 admits only the whole
ten-file integrations addition, or that addition together with the five
inspection additions. Rollback restores prior file absence and old policy
bytes. A protected never-started witness is consumed before bootstrap;
loaded jobs require normal PID/birth/descendant stop evidence.

Schema 5 separately records the complete three-file schedule-helper addition.
The protected previous bundle must establish that these helpers were not
already part of the deployment. Current candidates cannot omit them from
recovery. Schema 5 may include the unchanged Source proof described below;
older schemas remain supported under their original contracts.

Source updates use a separate closed schema 4 family. Before replacing code,
runtime or kit bindings, the actual old selection either proves strict absence
or quiesces all recorded Source roles and withdraws the exact old generation.
The protected context and journal bind that receipt to the candidate. Owner
model/environment/onboarding bytes are preserved from an admitted current
snapshot; an old bootstrap receipt is retained as history. Rebinding preserves
identity and token inode and leaves the new generation inactive until explicit
connection activation. Repeated updates retain qualified fixed-slot records
through a finite history ledger rather than overwriting earlier evidence.

Private settings, credentials, delivery journals and notes are not copied
into public deployment snapshots or deleted during rollback. Native retirement
verifies the selected service set and quarantines its runtime, authentication
tree and logs; the owner's settings and external vault remain intact.

Before the first private provisioning step, the controller durably records
the exact prior protected settings/service/installation authority, owner and
install identity, complete three-root prior absence, and approved validator
source. After rollback, the modern retirement reader can recognize that exact
preserved state without first requiring a successful new-service update.
All ten static additions must be absent and the integration job must be
absent and not intentionally disabled. Partial directories accept only known
preparation prefixes; foreign data, links, ACL grants and altered authority
are refused. Interrupted receipt publication never becomes authority.

This record also supports retry and interrupted private quarantine. It does
not widen the restored legacy gateway's filesystem policy, rotate a key or
delete private settings. Unknown residues still fail closed. Real-home
configuration components are created by an owner process, so a root
mkdir/chown interruption cannot leave a root-owned `.config` behind.

Only the verified legacy-preservation transaction may finish interrupted
preparation. It transfers ownership of verified empty pre-chown objects,
preserves every existing byte of a never-activated partial key, appends only
its missing random suffix, and completes the exact missing owner ACL steps.
A complete credential is never replaced or rotated. An already active new
deployment does not use this path to repair drift.

Normal Source retirement has a distinct transaction, protected Source cursor
and fixed Core move plan. It preserves exact state, token, environment, logs,
definitions and ledger-derived history by rename, then archives the final
witness/lock/cursor. Lost acknowledgements resume the same archive and inodes,
including after Core settings/runtime paths have moved. Source-bearing archives
are excluded from legacy completed-retirement pruning. Tokens are not copied
or read to repair custody.

This is not universal stopped-state or reboot recovery. A fresh Source-bearing
retirement with already-stopped Core services and no same-transaction witness,
and Source-bearing `--prepare-stopped-recovery`/`--resume-stopped-recovery`, are
refused. The new ordinary descriptor-only classification also does not admit
fresh stopped recovery. The pre-existing no-descriptor legacy schema 1/2 assist
path is unchanged. A reboot does not silently refresh a retirement witness.
Unknown state, new actors/token, identity drift or cross-filesystem rename
preserves the data and stops the operation.

History capture is bounded to 1,024 nodes, 32 MiB and depth 16; Source and Core
move plans each allow at most 1,024 items. Installer history has at most 64
explicit entries, and pending-initial boot history has at most 16 transactions.
Reaching a bound preserves/refuses; it does not authorize deletion or a prefix
allowlist.

## Required physical matrix

Use a disposable Apple Silicon installation and invented notes, provider
records, pages and notifications. Record the exact source commit, native
snapshot digest, OS/architecture, Python and selected access mode. Use the
ordinary native installer/update path from that reviewed source revision.

| Case | Required evidence |
|---|---|
| Fresh install | Seven verified launchd jobs, both authenticated/restricted channels ready, Settings save and reload |
| Legacy upgrade | Existing Google grant and settings preserved; complete new service snapshot; no provider calls during readiness |
| Existing new-service update | Key unchanged, approvals/journals preserved, no extra default permissions or notification opt-in |
| File/load/readiness failure | Admission remains closed on uncertainty; rollback restores old bytes/absence; forward retry succeeds |
| Interrupted private provisioning | A restored legacy deployment can retire directly using its exact preservation record, including a resumed private quarantine |
| Reboot | Owner, agent, watcher and scheduled notifications return without manual directory creation or service repair |
| Owner-channel isolation | Sandbox cannot read key/state/logs or dispatch owner actions; test direct paths, aliases, hardlinks, child exec and available IPC |
| Role/replay/refusal | Bare request, wrong key, reused proof, changed arguments/version/role, overlong frame and drip client refused without effects |
| Local MCP | Homebrew `npx`/`uvx`, tools, call, restart and idle stop work; SIGTERM and watcher failure leave no owned live descendants |
| Apps and notes | Invented Paperless/Immich/FreshRSS/changedetection data, ntfy, notes, exact approval, task notification and digest recovery |
| Retirement | Six-role legacy and seven-role new deployments; applicable legacy reboot assist tested separately; ordinary Source retirement and same-transaction cursor retry preserve private data/vault; unknown already-stopped Source refused |

The owner-key ACL ABI follows Apple's public
[ACL header](https://github.com/apple-oss-distributions/Libc/blob/main/include/sys/acl.h)
and [membership API](https://github.com/apple-oss-distributions/Libinfo/blob/main/membership.subproj/membership.h).
Mocked ABI tests establish control flow and refusal coverage, not enforcement
by a Mac kernel. Keep this gate pending until the physical evidence exists.
