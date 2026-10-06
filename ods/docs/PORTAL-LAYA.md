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
node "$INSTALL_DIR/extensions/services/pixel-agent/plugin/laya-setup-cli.mjs" "$INSTALL_DIR" 8017 "$EXTENSION_DIR/compose.yaml"
```

The catalog recipe must be installed first. The hook passes its actual location,
normally `data/user-extensions/laya/compose.yaml`, including custom data paths.
Setup creates a dedicated private `config/laya/api-key` and writes the owner
connection at `~/.config/ods/laya-portal.json`. A repeated setup preserves the key.
It never rewrites the shared ODS `.env`. The key lives under an owner-only
mode-700 directory; only that individual mode-444 file is mounted read-only in
the container. This permits rootless Docker UID mappings without exposing the
directory to other host users. The connection record remains mode 600. The
container must not receive Dashboard or inference-provider credentials.

The record binds the canonical installation root, local port and actual Compose file path and
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

## Qualification

The dependent catalog recipe supplies the pinned CPU service, key mount,
startup inference checks, resource limits and enable/reconfigure lifecycle.
Its container qualification runs real English, multilingual and typed decisions
on Linux AMD64 and ARM64, including disable/re-enable and inference after restart.
The core runtime matrix checks transport on Windows, Linux and macOS and private
connection custody on POSIX hosts. These checks complement an installed Portal
journey; they do not establish that every chat model selects tools correctly.
A successful `/health` response alone does not establish inference readiness.

### Real chat-model qualification

`extensions/services/pixel-agent/tests/laya_model_journey.integration.mjs` runs
an explicit, isolated test with the pinned OpenClaw runtime and an already
running local Ollama model. It uses the real Laya tool, runtime prompt hint and
on-demand operating guide. Activation is injected; two fixture file tools can
only save/read a fixed report in a new test workspace. This does not exercise
the installed Portal UI, its complete tool inventory or owner setup.

Use Node 24 and set these environment variables in your shell:

| Variable | Value |
| --- | --- |
| `OPENCLAW_PACKAGE_DIR` | Directory of the OpenClaw package matching `runtime-source/source-lock.json` |
| `LAYA_OLLAMA_URL` | Local Ollama origin, for example `http://127.0.0.1:11434` |
| `LAYA_CHAT_MODEL` | An already downloaded tool-capable Ollama model |
| `LAYA_TEST_PORT` | Port of the running Laya service |
| `LAYA_TEST_KEY_FILE` | Path to its dedicated key file; never paste the key into a prompt |
| `LAYA_QUALIFICATION_OUTPUT` | Directory for retained synthetic-task evidence |

From `ods/`, run each scenario separately:

```sh
node extensions/services/pixel-agent/tests/laya_model_journey.integration.mjs success
node extensions/services/pixel-agent/tests/laya_model_journey.integration.mjs unavailable
node extensions/services/pixel-agent/tests/laya_model_journey.integration.mjs baseline
node extensions/services/pixel-agent/tests/laya_model_journey.integration.mjs ordinary
```

The runner does not download a chat model, restart a service or modify the
installation. It launches a temporary gateway on a free loopback port and stops
that gateway when finished. The unavailable scenario uses its own failing local
endpoint; it never stops the real Laya service. Evidence includes tool calls,
the saved CSV, final response, gateway log and complete request latency.

Success requires correct classification, saving and reading back the final
report. Unavailable additionally requires one failed consultation followed by
completion and an honest explanation. Baseline performs the same task without
Laya; ordinary answers a simple question without tools. A failed assertion is a
failed model qualification, not a transport success to be relabeled as a pass.
Preserve failures and inspect the actual calls before accepting a model/route.

Small chat models can omit limitations or copy an uncertain classification even
when tool execution succeeds. Compare representative tasks with and without
Laya, including loading, tool selection and synthesis. This integration does
not guarantee a speedup or accuracy improvement; do not infer either from the
classifier's forward-pass time or introduce a universal confidence threshold.
