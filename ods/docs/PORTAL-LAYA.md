# Laya inside Portal

Laya is an optional local decision service. Portal's selected model uses it to
classify supplied text, score items against ordered criteria, or estimate a
yes/no answer, then continues the owner's task. It does not replace the chat
model, implement projects, execute operations, or establish that a build passed.

## Integration contract

The native `pixel_ods_laya` tool accepts named text items and typed questions.
Independent items with the same questions can share a batch. The adapter maps
these to Laya's HTTP `/v1/systemone/batch` contract, validates the complete reply,
and returns decisions associated with the original item IDs. Missing answers,
invalid probabilities and reported context truncation reject the result.

The Portal prompt advertises Laya only when the managed connection is enabled.
The `laya` operating guide explains meaningful use, question construction,
uncertainty and continuation. No inference runs automatically during prompt
construction. Simple conversation and direct edits need no classification.

Discovery registers the tool schema without loading a checkpoint. Execution
checks activation again, including a tool descriptor cached before disable.
Known service failures return a bounded tool error so the main model can
continue with other capabilities. Errors never substitute a canned final answer.
The result carries no execution authority or external verification receipt.

## Managed connection

The extension setup hook calls, as the installation owner:

```sh
node "$INSTALL_DIR/extensions/services/pixel-agent/plugin/laya-setup-cli.mjs" "$INSTALL_DIR" 8017
```

The catalog recipe must exist at `extensions/user/laya/compose.yaml` first.
Setup creates a dedicated private `config/laya/api-key` and writes the owner
connection at `~/.config/ods/laya-portal.json`. A repeated setup preserves the key.
It never rewrites the shared ODS `.env`. The container must read the key as the
same owner UID; it must not receive Dashboard or inference-provider credentials.

The record binds the canonical installation root, local port and Compose file
digest. Removing or disabling that Compose definition makes the tool unavailable;
changing its bytes requires setup again. Setup refuses unsafe file custody and
does not repair unrelated permissions. Concurrent setup is refused with a lock;
an interrupted lock must be inspected before recovery, not stolen after a timer.

Transport is fixed to IPv4 loopback and authenticated with this dedicated key.
Model arguments cannot specify a destination, credential or model download URL.
Request/response sizes, concurrent inference and observation time are bounded.
Cancellation stops observation; it does not prove server inference stopped.

Windows uses the POSIX connection mechanism inside WSL. Native Windows processes
cannot substitute POSIX permission checks for Windows ACL validation. Linux and
macOS use the same owner mechanism. Platform tests exercise private file custody
on Linux/macOS, transport on all three runners, and reject native Windows setup.

## Qualification still required before release

The adapter and lifecycle helper are not a complete extension installation.
The catalog recipe must supply the pinned service build, key mount, startup and
inference readiness checks, resource limits and enable/reconfigure lifecycle.
Qualify real inference, local/API model tool use, restart/disable/re-enable and
failure continuation on supported hosts. A successful `/health` response alone
does not establish inference readiness. Container GPU support and performance
must be measured separately from the CPU path.

Compare representative Portuguese/English Portal tasks with and without Laya.
Record completion, classification quality and whole-task latency, including
loading, tool selection and model synthesis. Do not infer a universal speedup
from the classifier's forward-pass time or use a universal confidence threshold.
