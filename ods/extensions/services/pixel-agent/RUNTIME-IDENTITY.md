# Runtime identity diagnostics

Portal's authenticated `GET /api/pixel/status` separates `available` from
`runtimeMatchesRelease`. Chat availability means the agent/model route is usable;
it is not a claim that the currently running code matches a reviewed release.
When the diagnostic route is available, `runtimeIdentity` contains only bounded,
allowlisted metadata. Missing/older routes remain usable for chat and return
unknown identity. No filenames, credentials, endpoint origins, prompts, or model
responses are projected.

## What version 1 actually observes

The running ODS plugin reads its own tree and the pinned OpenClaw tool-search
module at its first non-discovery registration. Its tree hash uses Pixel's
`extension-hash` framing. The plugin retains those initialization observations;
it does not replace them with newer files on subsequent status requests. A fresh
comparison can report that disk changed after initialization. Unreadable files
produce unknown comparison, not agreement. Discovery/schema-capture registration
does not read the host filesystem for diagnostics.

These are **initialization-time file observations**, not a digest of evaluated
JavaScript. Node resolves static imports before registration. A loader cache,
alternate registration path, or update racing initialization can therefore have
different evaluated code despite matching disk hashes. Matching hashes never
turn `runtimeMatchesRelease` green.

Separately, the plugin records the actual parameter objects returned by its
Pixel-specific tool factories. It publishes a digest over the latest schema hash
for each observed name and a count. Schema object keys are sorted recursively;
tool entries use the same name ordering as Pixel's tree digest. This can expose
schema drift even when disk files match. It is a union of latest-created plugin
tools, **not** a per-turn record, core-tool catalog, post-filter offered surface,
or model request. The offered count and offered schema digest stay null.

The gateway-authenticated plugin route is relayed through the restricted private
ingress and bearer-authenticated edge. Each projection discards extra fields and
rejects invalid, oversized or stale observations. The owner API does not trust an
upstream `true` bit. The Portal panel is under **Chat options → Advanced tools →
Runtime identity** and displays model/context separately from code identity.

## Tri-state release match and remaining work

- `false`: the producer observed a non-null initialization hash and a different
  fresh hash for that same file/tree. Installed bytes no longer match the
  initialization observation; restart/update reconciliation is needed.
- `null`: full release match is unproven. This includes matching partial file
  observations, missing producers, old deployments, and unavailable readings.
- `true`: reserved for a future complete, validated running-release binding.
  Version 1 cannot emit or accept it.

ODS release commit, Pixel source revision, preview image digest, final offered
tool schema digest/count remain explicitly null. A configured ref, image tag,
static expected patch hash, or vendor attestation alone cannot populate them as
active runtime identity.

Full acceptance still needs the following producer work and installed tests:

1. Linux/WSL `pixel-host-install.sh` and the macOS native transaction must write
   an owner-protected, versioned expected-release receipt after checksum-verified
   installation. Bind ODS commit, vendored Pixel source, canonical plugin tree,
   all patched OpenClaw modules, and preview implementation to a launch nonce.
   Rollback must restore/invalidate the receipt with the corresponding runtime.
2. Gateway launch/loader must measure the exact executed artifact or use an
   immutable verified tree with a process-start binding. The existing standalone
   vendor `runtime-attestation.json` checks source/install/config files, but its
   `gateway-verified-model-unproven` boundary is not full ODS runtime proof. File
   checks after imports must not silently stand in for a loader measurement.
3. Preview native/Compose producers must attest the actual running renderer or
   immutable image ID/digest, not a configured tag. Bind it to that same release
   and current process/container instance. Native preview has no image digest;
   the next protocol should represent native implementation identity explicitly.
4. Capture the final per-request tool schemas after native catalog/deferred-tool
   filtering and any provider transforms. Bind the hash/count to the model route
   and context identity without exposing user content. Created plugin schema
   observations in this PR deliberately do not substitute for that capture.
5. Join these fresh independent receipts in a new validated protocol; test clean
   install, upgrade, rollback, hot-swap, stale receipts, restart, missing services,
   and at least one real authenticated agent journey. Until every required
   binding is verified, availability can remain true but release match stays null.

Unit/source checks are not installed acceptance. Linux/macOS relay and upgrade
qualification must still be run on the exact candidate.
