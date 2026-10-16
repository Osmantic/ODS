# Model Management

ODS runs local language models as GGUF files from `data/models/` or an
installer-registered model store. Windows AMD with Portal in WSL uses the
Windows model store described below.
The recommended path is the Dashboard Models page. Manual model swaps are also
available for headless maintenance and advanced operator workflows.

## Recommended: Dashboard Models Page

Open the Dashboard and go to **Models**.

From there you can:

- Separate models already installed from the curated ODS catalog.
- Search compatible GGUF repositories on Hugging Face without leaving ODS.
- Check approximate model size, VRAM requirement, context length, and specialty.
- Download a catalog model into the installation's model store.
- Import an integrity-qualified Hugging Face GGUF into that store.
- Load a downloaded model.
- Load a manually copied single-file GGUF discovered in the model store.
- Delete a downloaded catalog model.

The expected user flow is a six-verb chain:

1. Discover a viable model in the catalog.
2. Download it.
3. Load it.
4. Use it through every enabled LLM app.
5. Restore the original model when validating a temporary swap.
6. Delete the temporary model when cleanup is part of the workflow.

The Models page should keep compatibility gates visible before a user commits
to a load. Agent viability gates are especially important: if an enabled agent
declares a context floor such as `65536`, models below that floor should be
shown as gated or warned before the swap. Badges should distinguish downloaded,
loaded, swap-safe, not-swap-safe, gated, and probe-failed states so a model
cannot look ready while an enabled app is known to be incompatible.

### Windows AMD with Portal in WSL

The Windows installer binds its `ODSLlamaServerRuntime-<Windows SID>` task,
which runs llama.cpp's `llama-server.exe`, to one WSL distribution and ODS
runtime directory. With verified task ownership and model store registration,
**Models** supports catalog and Hugging Face downloads, activation and context
changes using the existing ODS transaction. Downloads go to
`%LOCALAPPDATA%\ODS\lemonade\models`, which WSL accesses as a registered
Windows store. The import registry and download progress remain in the ODS
runtime's `data/` directory.

Wait for verification after download, choose **Run**, then choose the context.
Activation updates the owned Windows launcher selection as well as Portal and
ODS routes, and restarts llama-server with the new model. **Configure context**
uses the same transaction. The current Windows controller accepts
4,096–262,144 tokens; the model, available memory
and enabled apps must still support the requested value. A Hub listing or a
verified download alone does not prove runtime or agent compatibility.

**Unload model** stops the owned runtime while preserving its saved model and
context. Portal admission stays paused while inference is stopped. **Resume
model** starts and verifies the saved selection before releasing that pause;
resume before attempting another model or context change.

A task without a WSL installation binding does not gain control from an update
alone. Rerun the current Windows installer with the same distribution and
runtime directory to register it; a task that ran Lemonade Server moves to
llama.cpp in the same run (see
[AMD GPUs now run on llama.cpp](MIGRATION-LEMONADE-TO-LLAMACPP.md)). If
ownership cannot be verified, the controls remain unavailable. Do not manually
edit the binding or startup plan.

When the host agent cannot prove that this installation manages the Windows
model server, **Models** shows "Model changes managed externally" or "Runtime
management unavailable" and does not change the model; downloads and deletion
remain available. `ODS_HOST_LLM_TRANSPORT=model-router` describes how WSL
reaches the Windows server; it is not permission to manage a process. Linux,
NVIDIA and macOS retain their existing model-management paths.

### Hugging Face imports

The **Hugging Face** source searches the live Hub and only offers complete GGUF
artifacts with exact byte-size and SHA-256 metadata. Before download, ODS
re-reads the selected repository, pins its immutable revision, rejects
adapters, incomplete split files, and repositories intended for a different
runtime, then asks the host agent to download and verify every file.
Community imports are labelled as unvalidated until they have been benchmarked
on the local machine; they are not added to the ODS recommended catalog.

#### Checks before download

When you open a repository, ODS reads the GGUF metadata header of one of its
files with HTTP range requests (usually 1–16 MB, never the model weights) and
reports, before anything is downloaded:

- **Runtime support.** Each llama.cpp build ODS pins can load a fixed set of
  model architectures (`config/llama-cpp-architectures.json`, generated from
  llama.cpp's own source). A model whose architecture the build on this machine
  does not list is refused: loading it would fail after the download. If you
  know better, **Import anyway** imports it after an explicit warning; the
  switch then proves whether it loads and returns to your current model if it
  does not.
- **Model kind.** Embedding, reranking, speech and image models, and vision
  projector files on their own, are not chat models. ODS refuses them as the
  chat model and names the service that runs that kind of model instead.
- **Memory fit.** The header's attention layout feeds the same estimate the
  installer uses, per quantization: fits at the context ODS would serve, fits
  below the 64K that ODS Talk needs, or too large. Layouts ODS cannot size
  precisely are labelled as a rough estimate.
- **Template signals.** Whether the file's own chat template describes tool
  calls and thinking. These are hints from the file; how the model behaves
  shows only once you use it.
- **Disk space.** An import is refused before it starts when the model store
  would keep less than 2 GB or 5% of its drive, whichever is larger.

#### Vision models

A repository that ships a vision projector (an `mmproj` GGUF) beside its
weights offers **Include vision**. ODS then downloads the projector with the
chosen weights (F16 first, then BF16, F32 or Q8_0, or the repository's only
one), verifies it like the weights, and loads it with the model so the model
can read images; the memory and disk checks include it. Unticking it imports
the weights alone, and the disk check then counts the weights alone. Deleting the model also removes its projector unless another
installed model uses it. While a projector is loaded, llama.cpp does not reuse
cached prompt chunks (`--cache-reuse`).

On Windows the projector is loaded by the llama.cpp launcher that Windows setup
installs. An installation set up before vision support says so in the import
dialog and imports the weights alone until Windows setup is run again. Docker
Desktop installations whose llama.cpp runs on Windows itself import the weights
alone.

These checks never block on missing information: when Hugging Face does not
answer (for example a rate limit), the dialog says which checks could not run
and the import stays available. Each header read counts as one download in
the repository's Hugging Face statistics, so ODS reads one file per repository
you open and nothing for search results.

ODS requests the Hub's parsed GGUF metadata together with the repository and
uses its declared context window when available. The selected file's own
header, read before download, takes precedence over the Hub summary, and after
download the context stored in the local GGUF header takes precedence over Hub
and catalog values.
Some community repositories do not publish parseable context metadata. ODS
labels that limit as unknown instead of presenting a guessed maximum, starts
from a conservative 8K runtime default, and still permits an explicit context
choice through the normal verified activation transaction.

Public repositories require no configuration. For a private or gated
repository, accept its upstream license first and set `HF_TOKEN` in `.env` or
in the Dashboard environment editor. The token is read at request time, is
never returned to the browser, and is not written into the import registry.
In **Settings → Environment Editor**, the field is under **Provider and Hub
Credentials**. A stored token is shown only as a masked placeholder; leaving
the field blank preserves the existing value. Saving a replacement does not
restart the stack because dashboard-api and the host agent read the mounted
`.env` when each Hub request or fallback download starts.

Cancelling a download stops the active transfer and removes job-only temporary
files. Any previously verified shards remain available for a later retry. A
retry re-reads the immutable Hub revision and verifies every retained or newly
downloaded file before the model can become installed. An incomplete or
cancelled transfer is never eligible for activation.

The import dialog can be closed while its request is pending. If the request
times out, use **Check download status** to reconcile the selected artifact
with the catalog and download record. ODS does not automatically resubmit an
uncertain import; a timeout alone does not mean the download failed.

Imported metadata is stored separately in `data/model-imports.json`, so a
source update or installer rerun does not modify `config/model-library.json` or
discard community imports. Deleting a downloaded file keeps the import record,
allowing the same pinned artifact to be downloaded again.
The registry and completed model files are also independent of dashboard-api
and host-agent process lifetime: restarting either service reloads the same
pinned records and on-disk artifacts.

A routine Linux installer rerun also preserves the valid local model that is
currently active, including its exact GGUF pin, context, runtime profile, and
safe llama.cpp tuning. The installer still refreshes its hardware-based
recommendation separately, so the Models page can offer a better candidate
without changing the live agent behind the operator's back. Use
`./install.sh --reselect-model` only when you intentionally want the installer
to replace the active local model with its current recommendation. Missing,
incomplete, non-local, or catalog-mismatched state is never adopted.

When a catalog model is loaded, ODS updates the active GGUF settings
and restarts the local inference service so OpenAI-compatible clients use the
new model. After the switch settles, verify it from the host:

```bash
ods model current
curl http://localhost:11434/v1/models
```

On macOS native Metal and native Windows installs, use the configured local
API port (normally `8080`). For Windows AMD with Portal in WSL, verify the
loaded model and send a message through Portal: WSL's localhost may differ
from Windows localhost, and setup may select another llama-server port (18080
or 28080).

Dashboard activation, Unix `ods model swap <tier>`, and Windows
`.\ods.ps1 model swap <tier>` use the same authenticated host-agent transaction.
The transaction updates `.env`, `models.ini`, the
native or container inference runtime, LiteLLM, Hermes, OpenCode, and
Perplexica when those consumers are installed. On a qualified ODS-managed
Pixel installation it also updates Pixel's model ID, context, output limit,
reasoning and model-family compatibility policy, then restarts and verifies the
gateway. It verifies the new runtime and downstream routes before reporting
success. When maintenance ownership is confirmed, a late failure restores the
prior files, runtime, persisted app routes, and Pixel binding, then proves the
previous model is serving again. An uncertain acknowledgement stays pending
instead of assuming that rollback or commit completed.

With the managed Portal/Edge relay configured, `/v1/model/activate` and remote
route activation acquire both native and Edge admission before changing
inference. The native coordinator retains
the exact previous model contract, including a remote route's fingerprint.
A private `data/pixel-model-transaction.json` journal records the transaction
ID, phase and configuration hashes before admission; it contains no API keys
or configuration contents. Standalone native installs without an Edge relay
retain their existing direct model reconciliation.

The Portal model menu offers **Recover model switch** when this journal is
pending. Its authenticated `GET /api/models/recovery` reads journal metadata;
`POST /api/models/recovery` invokes the host's `POST /v1/model/recover` with an
empty body. Recovery also runs before a subsequent model activation. It can
finish an unchanged rejected/partial begin, a fully restored rollback, or an
already committed model with matching configuration and fresh runtime proof.
It never loads a different model or repeats inference mutations. An interrupted
gate release retries only the same native finish operation; the coordinator
revalidates its ownership and can restore/requalify the gateway contract.
Configuration changes during proof, missing evidence, or a crash halfway
through inference changes leave recovery pending and require explicit repair.

An ODS-managed Pixel route supports OpenClaw's 4096-token minimum. Below 16K it
uses a deliberately constrained adaptive prompt, so complex-task reliability
still depends on the selected model and available context, but the route is not
blocked. A requested context below 4K is rejected before activation writes
files or restarts services. ODS gives Pixel an output ceiling of one quarter
of the committed context, capped at 8192 tokens. Compaction keeps a
context-scaled recent tail and reserves room for that output ceiling. At 8K
that leaves about 4.9K tokens for Portal's prompt and tools, which many tasks
exceed; Portal then reports a context overflow. Load such a model with 16K or
more where it supports it.

### What a model can do (model profiles)

The first time a model runs on this machine, ODS checks what it can actually
do before Portal and the other apps switch to it. The Models page shows
**Checking what this model can do (first time only)** while it runs. The check
takes up to two minutes and happens once per model file, llama.cpp build and
machine. ODS:

- asks for a plain answer;
- asks the model to call a tool, hands back the tool's result, and repeats the
  call as a stream;
- turns thinking on and off, where the model's template allows it;
- shows it a small image, when the model was imported with its vision projector;
- measures its generation speed.

The result appears under the running model on the Models page, with
**Check again**. Everything stays on the machine: the check talks only to the
local model, and results are stored in `data/model-profiles.json`. A failed or
unfinished check never blocks a switch.

The tool check uses one small tool. Passing it shows that the model's tool
calls work with this machine's llama.cpp; it does not promise that the model
finishes every multi-step Portal task. Small models can still end some tasks
without an answer.

By default profiles are advisory (`ODS_MODEL_PROFILES=observe` in `.env`):
apps keep working exactly as before. `off` skips the check.

With `ODS_MODEL_PROFILES=enabled`, the apps also use what the check measured,
from the next switch:

- The model route records whether the model can call tools and read images.
  A model that failed the tool check is not offered to agents as agent-ready,
  unless the curated catalog has verified it for agents.
- Portal shows one advisory above the conversation when the model is served
  below 16K tokens of context, failed the tool check, has not been checked yet,
  or always thinks before it answers. Chat always stays available.
- ODS Talk notes when the model failed the tool check; Talk stays available.
- Portal's agent is told whether the model reasons and reads images from the
  check, instead of from the model's name and the catalog. Where Pixel is
  managed without the Portal coordinator, its rendered config also follows
  the measured way of turning thinking off.

Turning `enabled` on, or **Check again**, updates the advisories and the
agent-ready status at once; the route record and Portal's agent settings
follow on the next switch.

#### Fixed chat templates

A few models carry a chat template that does not work well with llama.cpp,
for example one that loses tool calls. ODS ships fixed templates from
llama.cpp for templates it knows to be broken (`config/chat-templates/`,
reviewed like code and checked by SHA-256). When the check shows that the
running model's own template is exactly one of them, the Models page offers
**Try a fixed template**: ODS restarts the model with the fixed template and
checks it again. A later switch clears it. With `ODS_MODEL_PROFILES=enabled`,
ODS tries the fixed template by itself when the model does not answer the
first check: it keeps the fixed template if the model then answers, and
otherwise returns to the model's own template, all within the same two-minute
check. Templates are never downloaded while ODS runs. The Windows model
runtimes cannot use a fixed template yet.

### Choosing the runtime context

Before loading a model, the Dashboard offers context presets derived from the
catalog and a custom context input for any safe whole number from `1024`
tokens. The catalog's recommended context is the conservative default. Its
declared maximum is shown separately and is used to build presets, not as a
hard block for advanced operators.

A custom value above the declared model context is allowed with a warning.
This does not claim that the model, runtime, available VRAM, or enabled apps can
serve that value. ODS writes the requested context through the same activation
transaction, starts the platform-specific runtime, and requires runtime
identity and context proof before committing the swap. If the Compose
llama-server, macOS Metal or Windows `llama-server.exe` cannot establish the
requested context, activation fails and the previous model configuration is
restored.

The committed value is propagated to `CTX_SIZE`, `MAX_CONTEXT`, the
llama-server runtime configuration, stable model routes, and installed
context-aware consumers such as Hermes. Optional stopped services remain
stopped while their persisted configuration is updated. Use the Models page to
change this value; the corresponding Environment Editor fields remain
read-only so direct `.env` edits cannot bypass activation verification and
rollback.

For Hugging Face imports, repository GGUF metadata is preferred over generic
model config. After download, the context embedded in the local GGUF header is
authoritative and replaces stale Hub metadata. If neither source publishes a
usable value, the Dashboard reports the declared limit as unknown instead of
inventing one; the operator may still choose an explicit context and let the
runtime verification decide whether it can be served.

This selector applies to local inference in `local` and `hybrid` modes. In
`cloud` mode, the remote provider owns its context policy, so ODS does not
rewrite local runtime or application context from the Models page.

### Activating a remote provider for agents

The Dashboard **Remote Provider** page can move the stable `ods/current` route
to an OpenAI-compatible provider without giving Pixel a provider URL or
credential. Enter the provider model ID, context window, maximum output tokens,
and whether that route supports reasoning. Those limits become Pixel's managed
runtime contract, so model-family and context changes are explicit instead of
being guessed from a provider response.

For a direct HTTPS provider, **Configure** first performs a bounded provider
probe. ODS then writes the private egress credential, renders the cloud
LiteLLM route, recreates and health-checks LiteLLM, serves a real completion
through `ods/current`, and reconciles the ODS-managed Pixel gateway. For an SSH
provider, Configure stages the route; **Test route** completes the same
consumer activation only after the managed tunnel and egress proof succeed.
The Dashboard reports the provider as Ready only when the egress path and the
actual consumer route are both active and proven.

With managed Portal admission, SSH staging requires a verified local model to
remain active. Disable an active remote provider before configuring or enabling
its SSH replacement. Staging retains the local model and releases its maintenance
hold only after proving it again; the later tunnel proof opens a new transaction
for consumer activation. Direct HTTPS providers can be replaced synchronously,
including providers that use the same model name at different endpoints.

The status page rechecks the current host-owned Pixel runtime instead of
trusting an older activation receipt. If the provider is reachable but ODS or
Pixel has moved to a different model contract, the page reports **Consumer
drift**, marks inference unavailable, and offers **Reconcile route** in the
header. Reconcile runs the same fresh proof and transactional activation as
`ods remote-provider enable`; it reuses the owner-custodied secret and does not
ask the browser to recover or resubmit it.

The operation is transactional. ODS retains the exact prior mode, LiteLLM
config, and Pixel model contract in a private recovery record. A failed render,
container health check, or completion restores that state only while the same
transaction still owns maintenance and the previous route can be proved. An
unconfirmed apply or finish retains its recovery record and blocks new work
until recovery proves the outcome; ODS does not replay the mutation or claim a
successful rollback from an ambiguous response.
**Disable** and **Remove** likewise restore and prove the pre-provider route
before reporting success. Disable is a reversible pause: ODS retains the
non-secret route metadata in an owner-only, fingerprint-bound profile and keeps
the existing secret custody, so `ods remote-provider enable` can freshly prove
and reactivate either a direct or SSH route without asking for the endpoint,
model, key, or SSH inputs again. A transition from paused or degraded state
never trusts the prior probe receipt; an exact healthy already-active route is
an idempotent no-op. An SSH proof failure automatically pauses the staged route
again. Remove is the intentional clean slate and deletes the saved profile as
well as the stored secrets. A legacy disabled route that predates saved profiles
remains disabled and requires `ods remote-provider configure` once. Provider
credentials remain in the host-owned egress secret store and never enter
generated LiteLLM YAML, Pixel state, Dashboard responses, or browser logs.

```bash
ods remote-provider disable       # restore local ODS/Pixel and retain the route
ods remote-provider status        # shows only whether a saved route is available
ods remote-provider enable        # fresh proof, then transactional reactivation
ods remote-provider remove        # delete route profile and secret custody
```

### Multi-GPU assignment replanning

On Linux NVIDIA and managed Linux AMD installations with more than one GPU,
the same transaction also checks the target model's declared or
size-derived VRAM envelope against the persisted llama-server assignment. If
the existing subset is too small, ODS reruns the topology planner, expands
llama-server to a sufficient GPU subset, and updates
`GPU_ASSIGNMENT_JSON_B64`, `LLAMA_SERVER_GPU_UUIDS`,
`LLAMA_SERVER_GPU_INDICES`, `LLAMA_ARG_SPLIT_MODE`, and
`LLAMA_ARG_TENSOR_SPLIT` atomically with the model route. Pipeline assignments
clear a stale explicit tensor split so llama.cpp can fit layers to the live
free memory of heterogeneous cards. If activation fails, the previous model
and GPU assignment are both restored and health-proven before rollback is
reported as successful.

For AMD, ODS also updates `ROCR_VISIBLE_DEVICES`. The AMD multi-GPU overlay
passes the assigned GPUs to llama.cpp as `GGML_VK_VISIBLE_DEVICES` (Vulkan)
and `ROCR_VISIBLE_DEVICES` (ROCm). The runtime accepts the expanded assignment
only when every selected GPU has a detected `gfx` architecture, and ODS refuses
to combine `gfx1151` with another architecture in one llama-server process.
Custom operator-provided HSA values are preserved; only the exact values
managed by ODS are removed when they no longer match the selected GPU
architecture.
Single-GPU, Apple, Windows AMD (`llama-server.exe`), externally managed
inference, and unpersisted all-GPU fallback configurations keep their existing
behavior.

To inspect or intentionally override a multi-GPU plan, use the supported CLI
instead of editing the encoded assignment in `.env`:

```bash
ods gpu assignment
ods gpu reassign --dry-run --auto
ods gpu reassign --manual
ods gpu validate
```

Manual reassignment accepts GPU indices for llama-server and optional
accelerated services, validates them against the live topology, and persists
the readable runtime variables together with `GPU_ASSIGNMENT_JSON_B64`.
Leaving an auxiliary prompt blank preserves its current placement; on a legacy
install without prior placement, an enabled service defaults to the first live
GPU while a disabled service remains omitted. Applying the change recreates
the affected stack without first tearing it down. If Compose rejects the new
contract, ODS restores the previous `.env` and recreates the previous stack;
declining the prompt leaves the validated plan saved until the next
`ods restart`. Model activation never expands an assignment marked as manual;
if that set is too small for a target model, choose a larger set with
`ods gpu reassign --manual` first. Explicit NVIDIA visibility controls such as
`all`, `none`, and `void` are also left unchanged.

Open WebUI, Token Spy, Privacy Shield, and OpenAI-compatible SDK clients follow
the stable ODS endpoint and do not persist a separate model route. Optional
apps that are not installed are skipped. Optional services that were stopped
remain stopped: persisted Hermes/OpenCode state and Compose environment are
updated without starting them, and Perplexica reconciles its app-owned state
from that environment the next time ODS starts it.

Direct edits to `.env`, `models.ini`, or app-owned settings bypass this
transaction. The Dashboard Settings editor therefore treats active model,
tier, artifact integrity, and runtime-profile fields as read-only; use Model
Manager instead. Use the manual procedure below only for recovery or
unsupported custom models, and verify every affected consumer afterward.

New or updated LLM apps should avoid direct model coupling. The swap-safe
extension contract is documented in
[SWAP-SAFE-EXTENSIONS.md](SWAP-SAFE-EXTENSIONS.md): route through
`http://litellm:4000/v1`, use model `ods/current`, and declare `service.llm`
when the app needs a context floor, dynamic refresh, or post-swap probe.

## Where Models Live

Default model directory:

```bash
~/ods/data/models/
```

On native Windows installs:

```powershell
$env:USERPROFILE\ods\data\models\
```

On Windows AMD with Portal in WSL, the registered Windows model store is
`%LOCALAPPDATA%\ODS\lemonade\models`. Catalog and Hugging Face downloads use
that store automatically; the Ubuntu `~/ods/data/models/` directory is not
the Windows runtime's model directory.

Each model is normally a single `.gguf` file:

```bash
ls -lh ~/ods/data/models/*.gguf
```

The active model is recorded in `.env`:

```bash
grep -E "^(LLM_MODEL|GGUF_FILE|CTX_SIZE|MAX_CONTEXT)=" ~/ods/.env
```

`GGUF_FILE` is the filename ODS should load from its selected model store.
`LLM_MODEL` is the friendly logical model name used by scripts and config.
`CTX_SIZE` and `MAX_CONTEXT` control context length.

Hermes requires at least a 64K context window. Installer bootstrap mode uses
`65536` for the fast-start model, then switches `.env`, llama-server, and
Hermes config to the model selector's chosen full-model context when the
background download completes. Larger tiers may use `131072`; constrained tiers
can remain at a smaller selected context.

## Manual: Download a Catalog Model

For most users, use the Dashboard. If you are debugging a failed download or
preloading a machine, download the exact catalog GGUF URL from
`config/model-library.json` into `data/models/`.

Example:

```bash
cd ~/ods
mkdir -p data/models

curl -L \
  -o data/models/Qwen3.5-9B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/3885219b6810b007914f3a7950a8d1b469d598a5/Qwen3.5-9B-Q4_K_M.gguf
```

Then open Dashboard -> Models. If the filename matches a catalog entry, the
model should appear as downloaded and you can load it from the Dashboard.

## Bring Your Own GGUF

For a single local `.gguf`, the normal flow is:

1. Copy the file into the installation's model store (normally `data/models/`; see [Where Models Live](#where-models-live)).
2. Open Dashboard -> Models.
3. Load the local entry.

The Dashboard updates `.env`, `config/llama-server/models.ini`, and the active
runtime routing before restarting the inference service.

With an external OpenAI-compatible server, ODS does not load models: make the
model available in that server, then rerun the installer with
`--external-llm-model <id>`.

Use the manual procedure below only if you cannot access the Dashboard or need
to repair an install by hand. It does not update the bound Windows/WSL startup
plan; use the managed controls or rerun the Windows installer for that path.

1. Download the GGUF into `data/models/`.

```bash
cd ~/ods
mkdir -p data/models
cp /path/to/MyModel-Q4_K_M.gguf data/models/
```

2. Update `.env`.

```bash
ods config edit
```

Set:

```dotenv
LLM_MODEL=my-model
GGUF_FILE=MyModel-Q4_K_M.gguf
CTX_SIZE=8192
MAX_CONTEXT=8192
```

3. Update `config/llama-server/models.ini`.

```ini
[my-model]
filename = MyModel-Q4_K_M.gguf
load-on-startup = true
n-ctx = 8192
```

4. If Hermes is enabled, update `data/hermes/config.yaml`.

```yaml
model:
  default: "MyModel-Q4_K_M.gguf"
  context_length: 65536
```

Also keep `auxiliary.compression.context_length` at the same value and use
`compression.threshold: 0.50`; older absolute-token thresholds can leave Hermes
waiting too long to compact.

5. If Perplexica is enabled, reseed or verify its model setting.

```bash
LLM_MODEL="$(grep -E '^LLM_MODEL=' .env | tail -n1 | cut -d= -f2 | tr -d '"')"
PERPLEXICA_PORT="$(grep -E '^PERPLEXICA_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '"')"
scripts/repair/repair-perplexica.sh "http://127.0.0.1:${PERPLEXICA_PORT:-3004}" "$LLM_MODEL"
```

Dashboard activation and `ods model swap` handle this automatically. Raw GGUF
or `.env` edits still require verification because Perplexica stores its own app
settings in its volume.

6. Restart the affected services.

```bash
ods restart llama-server
ods restart litellm
docker restart ods-hermes 2>/dev/null || true
```

If your install uses direct Docker Compose commands instead of the `ods` CLI,
recreate `llama-server` so it rereads `.env`.

## Verify a Switch

Use these checks after Dashboard or manual model changes:

```bash
ods model current
curl http://localhost:11434/v1/models
```

For LiteLLM installs that require an API key, use the key from `.env`:

```bash
LITELLM_KEY=$(grep '^LITELLM_KEY=' .env | cut -d= -f2-)
curl -H "Authorization: Bearer $LITELLM_KEY" http://localhost:4000/v1/models
```

From inside a Docker container, the inference endpoint is:

```text
http://llama-server:8080/v1
```

For release or harness validation, do not stop at server identity. A valid
model-management pass proves the full verb chain for the selected tier:

- release tier: a six-model matrix per host, with download, load, app use,
  restore, and cleanup evidence for each planned target;
- smoke tier: one complete verb chain through one planned test model;
- app probes: every enabled LLM consumer discovered from manifests or known
  routing config is probed after the swap;
- Open WebUI: an auth wall, missing admin credential, or HTTP 401 is not a
  passing probe. Provision an admin/API credential for the lane, or mark the
  probe red/deferred with the reason visible in the report;
- agent gates: context and capability floors for Hermes-style agents remain
  visible before selection and are rechecked after load.

## Troubleshooting

### The download finished, but the model is not visible

Check the file is present and non-empty:

```bash
ls -lh data/models/*.gguf
```

If it is a catalog model, confirm the filename exactly matches
`config/model-library.json`. The Dashboard only marks catalog models as
downloaded when the on-disk filename matches the catalog entry.

### The model file exists, but loading fails

Check service logs:

```bash
ods logs llm
```

Common causes:

- The model needs more VRAM or unified memory than the machine has.
- Context length is too high; lower `CTX_SIZE` / `MAX_CONTEXT`.
- The GGUF is not compatible with the active backend.
- The model's architecture is newer than this machine's llama.cpp build. The
  Hugging Face browser checks this before download; a model imported with
  **Import anyway** can still fail here.

### Open WebUI or another app still shows the old model

Verify the server first:

```bash
curl http://localhost:11434/v1/models
```

If the server is correct, refresh the app. If the server is wrong, restart
`llama-server` and verify `.env` / `models.ini`.

### Hermes still asks for the old model

Hermes has its own config:

```bash
grep -n "default:\|context_length:" data/hermes/config.yaml
docker restart ods-hermes
```

## Current Limitations

- Dashboard download and load support the ODS catalog and integrity-qualified
  GGUF artifacts discovered through the Hugging Face source.
- Import from an arbitrary URL is not a first-class Dashboard workflow. Local
  single-file GGUFs are discovered after they are copied into `data/models/`.
- `ods model swap` switches ODS tiers, not arbitrary GGUF files.
- `scripts/upgrade-model.sh` is a legacy helper for model-directory layouts and
  should not be used as the primary GGUF switch path on current installs.
