# Confined file receipts

The pinned SDK embeds `file-operation-receipts.mjs` through the exact
`openclaw-compaction-resume.json` recipe. The source test requires that embedded
helper to match the reviewed module. The existing compaction changes remain in
the recipe, with the former patched hash retained as an upgrade predecessor.

`openclaw-read-range.json` supplies the confined native read factory and forwards
the private receipt context to existing read/write/edit adapters.
`openclaw-file-identity.json` attaches a digest of device, inode and resolved path
at the sandbox bridge's already-open read FD. Native reads use the same identity
from the existing confined `RootHandle.read` result. No independent path stat,
shell command, or host shortcut is used to read sandbox files.
`openclaw-file-operations.json` threads the private context through coding tools
and exposes its factory/version on `createOpenClawCodingTools`.

Integration must create one context per Pixel attempt with the actual session
key and effective workspace. Only a configured ODS Pixel agent enables it.
Hydrate it from actual retained session tool results, then call `updateVisible`
on every final provider message list **after** projection and truncation.
Metadata or a receipt header without its complete rendered range does not count
as visible file content. Same-version visible ranges accumulate within a bound;
a newer version or opened identity discards earlier coverage. An explicit read
always performs a real read and returns its requested content.

The guard's `fileVersionAdmissionAvailable` option defaults to false. Integration
must enable it only when the repaired SDK function exposes version 1 and the
factory. Older runtimes retain the per-run read prerequisite. The capability
replaces that prerequisite with confined byte/identity checks; it does not grant
new filesystem access or waive preview/behavior verification.

Before an edit, the adapter compares the actual read with visible content. Before
either an edit or a full write, it rereads using the same confined operations and
checks bytes plus opened identity. Full writes require a visible complete file;
edits may use the relevant visible ranges. Unmapped mutations fail closed. New
files are allowed only while absent. A successful mutation result is emitted
with a receipt only after actual readback matches. Failed checks, partial writes,
readback failures and cancellation remain failures even if an SDK recovery path
would infer success from intended content.

This preserves the SDK's existing check/write race against an external writer.
It is **not atomic compare-and-swap** and does not lock user editors. Opened
identity detects same-byte path retargets between observations; it does not claim
to make all external filesystem activity serializable. Byte fidelity uses UTF-8
buffers; unsupported binary/invalid UTF-8 reads retain ordinary SDK results and
do not grant receipt-based mutation reuse.

Successful text feedback contains a content hash, byte count and numbered range,
with at most 12,000 characters of body. Edited files show a bounded region around
the first changed line. Omitted lines have an explicit next read offset. Failure
feedback retains the original error and adds at most 2,000 characters of the last
observed bytes, explicitly marked as no mutation approval. It does not rewrite
historical model messages. Measure the extra confined prewrite/readback IO
separately from avoided model turns in the paired performance campaign.

Required integration ordering: install the existing compaction-resume and
read-range repairs, then the new `--file-identity` and `--file-operations` repairs,
then the composed provider-boundary selection repair. Linux and Mac installers
must include both new exact recipes. Each uses existing private backup,
apply/reapply, predecessor verification and refusal to overwrite changed bytes
on restore. Deployment is incomplete until both installers and the selection
callback are composed and the real provider-wire fixture passes.
