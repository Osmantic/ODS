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

Enabling the extension makes this helper available; it does not require a Laya
call on every turn. The model considers it for repeated classification, triage
or scoring that advances the requested task, and respects an owner's request to
work without Laya. Relevant consultations of an already enabled local helper
need no extra confirmation. Sorting, counting, calculations, code generation
and ordinary replies stay with the model's existing capabilities. Disabled
extensions are not automatically enabled to answer a request.

Selection applies to the subtask, not just the overall request. A news website
can use Laya to categorize its article dataset, join the saved decisions to
the original rows by ID, and continue creating the website with ordinary tools.
A plain landing page has no such classification step. Ask only the requested
semantic questions: output format, IDs, row order, counts and build success
belong to file checks or execution, not additional classifier questions.
Language settings describe the source text, not the language of the chat.

Selection remains a model decision, not a keyword router or a second inference
before every response. Quality varies by language and domain; activation,
multilingual support and prediction confidence do not establish a speed or
accuracy advantage. Evaluate representative tasks separately rather than adding
benchmarks or duplicate classification to the owner's ordinary work.

Discovery registers the tool schema without loading a checkpoint. Execution
checks activation again, including a tool descriptor cached before disable.
Known service failures return a bounded tool error so the main model can
continue with other capabilities. Errors never substitute a canned final answer.
The result carries no execution authority or external verification receipt.

## Workspace datasets

`pixel_ods_laya_batch` accepts an existing UTF-8 CSV, TSV, JSON array or JSONL
file, its text column/key, optional unique ID column/key, shared questions and
an output directory. It reads up to 128 rows/64 KiB, partitions inference into
bounded batches and saves `report.csv` and `decisions.json` inside a new
`laya-<id>` directory. The chat model does not need to copy rows into arguments
or transcribe classifier output. Missing columns, malformed records, duplicate
IDs and exceeded limits reject the dataset rather than silently dropping rows.

The CSV preserves row order/IDs and contains editable decisions. Original
confidence statistics stay separately in JSON, so corrected labels cannot
silently inherit a different prediction's confidence. Formula-like text cells
are escaped for spreadsheet use; JSON retains the exact identifiers and all
original probabilities. The JSON also
records the source hash and complete question definitions. Verified readback
proves saved bytes, not classification accuracy.

The tool returns bounded examples of the least decisive choice predictions for
the model to inspect against their original text. Ranking uses the probability
gap between the leading options, not an invented universal acceptance threshold.
The shortlist does not certify other rows; long excerpts and omitted examples
are explicitly identified. Unsupported CSV labels should be corrected through
ordinary file tools and verifying the edit. `decisions.json` remains the original Laya evidence, not an assertion
that later model corrections came from Laya.

File operations run through the installed SDK's scoped exec tool and existing
Sandbox/Full Access policy. A factory cannot invent a live run: admission binds
the current session, arguments and single invocation. The fixed helper uses
workspace-relative no-follow reads and create-only outputs. No arbitrary host
path, shell code or destination enters the schema. Source bytes are rechecked
before saving. A failed/cancelled attempt does not deliver partial decisions as
complete; any possible output directory is reported for inspection. The batch
tool is not replay-safe. Disable and configuration changes are rechecked during
processing, and failure leaves the model's other capabilities available.

This is most useful when an owner wants structured classification or scoring of
a dataset, particularly when copying rows and producing reports would dominate
the model's work. It is not a default preprocessor for every chat or coding task.

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

### Installed batch comparison

`tests/laya_batch_portal.integration.py` exercises the actual authenticated Portal
API with a fixed bilingual 32-row dataset. It supports `--mode baseline`,
`--mode inline` (the existing tool), and `--mode batch`. Supply `--install-root`,
`--workspace` and `--evidence` as absolute local paths. Authentication is read
privately from the installation. Run modes in alternating order and retain every
trial. Each creates its own workspace directory; none changes the selected model.
The output records total duration, actual tool activity and CSV label accuracy,
including incorrect or incomplete attempts. A 200 response alone is not success.

`tests/runtime_laya_batch.integration.mjs <openclaw-package> [sandbox-image]`
separately checks real scoped file execution in an isolated temporary workspace.
It uses fixture decisions and is not an accuracy or performance benchmark.

### Natural selection in the installed Portal

`tests/laya_selection_portal.integration.py` records one attempt per declared
case/phase through the authenticated installed Portal API. Pass absolute paths
with `--install-root`, `--workspace`, `--sessions` (the Pixel session directory),
`--evidence`, `--plan` and `--fixture`; select `--phase` and `--cases` explicitly.
The fixture is a JSON object with `cases`, each containing public/synthetic
`id` and `text`. Additional expected labels never enter the model workspace.
The plan is a JSON object with named `cases`, each specifying `expected`
(`use` or `skip`), `rows`, and `prompt`. `{directory}` in a prompt is replaced
with its newly created workspace directory. Each case receives `articles.csv`,
an `orders.csv` arithmetic fixture, and a `styles.css` editing fixture.

Declare cases before changing guidance: repeated semantic classification,
classification inside a larger deliverable, ordinary conversation, an obvious
single label, direct CSS changes, arithmetic and an explicit opt-out. Do not
name Laya in positive prompts. Retain baseline and candidate evidence, then
test held-out wording. Do not retry incomplete runs automatically or change
the selected model to improve the comparison. The runner neither enables the
extension nor changes services; separately verify disabled behavior and restore
the owner's prior state after lifecycle qualification.

For expected-use dataset cases, selection passes only with a successful native
batch result and a saved receipt matching its verified digest, not merely an
attempted call or a file the model wrote itself. Audit questions, source digest and original
IDs independently. The runner's single-question planning check is suitable for
one-label tasks; it is not a general restriction on multi-output requests.
`terminalComplete` records the stream/task outcome, not artifact correctness.
Independently inspect requested output paths, complete data joins and actual
filter/search interactions before claiming a mixed website task succeeded.
Retain schema-repair attempts and latency: an eventually successful call can
still expose avoidable work or model limitations.
