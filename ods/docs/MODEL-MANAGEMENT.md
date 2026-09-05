# Model Management

ODS runs local language models as GGUF files from `data/models/`.
The recommended path is the Dashboard Models page. Manual model swaps are also
available for headless maintenance and advanced operator workflows.

## Recommended: Dashboard Models Page

Open the Dashboard and go to **Models**.

From there you can:

- Separate models already installed from the curated ODS catalog.
- Search compatible GGUF repositories on Hugging Face without leaving ODS.
- Check approximate model size, VRAM requirement, context length, and specialty.
- Download a catalog model into `data/models/`.
- Import an integrity-qualified Hugging Face GGUF into `data/models/`.
- Load a downloaded model.
- Load a manually copied single-file GGUF discovered in `data/models/`.
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

### Hugging Face imports

The **Hugging Face** source searches the live Hub and only offers complete GGUF
artifacts with exact byte-size and SHA-256 metadata. Before download, ODS
re-reads the selected repository, pins its immutable revision, rejects
projectors, adapters, incomplete split files, and repositories intended for a
different runtime, then asks the host agent to download and verify every file.
Community imports are labelled as unvalidated until they have been benchmarked
on the local machine; they are not added to the ODS recommended catalog.

ODS requests the Hub's parsed GGUF metadata together with the repository and
uses its declared context window when available. After download, the context
stored in the local GGUF header takes precedence over Hub and catalog values.
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

On macOS native Metal and Windows native/Lemonade installs, use
`http://localhost:8080/v1/models` unless you changed the port.

Dashboard activation, Unix `ods model swap <tier>`, and Windows
`.\ods.ps1 model swap <tier>` use the same authenticated host-agent transaction.
The transaction updates `.env`, `models.ini`, the
native or container inference runtime, LiteLLM, Hermes, OpenClaw, OpenCode, and
Perplexica when those consumers are installed. On a qualified ODS-managed
Pixel installation it also updates Pixel's model ID, context, output limit,
reasoning and model-family compatibility policy, then restarts and verifies the
gateway. It verifies the new runtime and downstream routes before reporting
success. A late failure restores the prior files, runtime, persisted app routes,
and Pixel binding, then proves the previous model is serving again.

An ODS-managed Pixel route supports OpenClaw's 4096-token minimum. Below 16K it
uses a deliberately constrained adaptive prompt, so complex-task reliability
still depends on the selected model and available context, but the route is not
blocked. A requested context below 4K is rejected before activation writes
files or restarts services. Below 32K, ODS gives Pixel an output ceiling of one
quarter of the committed context; at 32K and above it allows up to 4096 output
tokens. Compaction keeps a context-scaled recent tail and uses extra headroom
for 8K-31K profiles so recovery occurs before a dense tool transcript exhausts
the model window.

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
identity and context proof before committing the swap. If NVIDIA Compose,
macOS Metal, Windows native llama-server, or Lemonade cannot establish the
requested context, activation fails and the previous model configuration is
restored.

The committed value is propagated to `CTX_SIZE`, `MAX_CONTEXT`, llama-server
or Lemonade runtime configuration, stable model routes, and installed
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

This selector applies to local inference in `local`, `hybrid`, and `lemonade`
modes. In `cloud` mode, the remote provider owns its context policy, so ODS
does not rewrite local runtime or application context from the Models page.

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

The status page rechecks the current host-owned Pixel runtime instead of
trusting an older activation receipt. If the provider is reachable but ODS or
Pixel has moved to a different model contract, the page reports **Consumer
drift**, marks inference unavailable, and offers **Reconcile route** in the
header. Reconcile runs the same fresh proof and transactional activation as
`ods remote-provider enable`; it reuses the owner-custodied secret and does not
ask the browser to recover or resubmit it.

The operation is transactional. ODS retains the exact prior mode, LiteLLM
config, and Pixel model contract in a private recovery record. A failed render,
container health check, completion, or Pixel restart restores that state.
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

On Linux NVIDIA and managed Linux AMD/ROCm installations with more than one
GPU, the same transaction also checks the target model's declared or
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

For AMD, ODS also updates `ROCR_VISIBLE_DEVICES`. The runtime accepts the
expanded assignment only when every selected GPU has a detected `gfx`
architecture. A homogeneous `gfx1151` set receives the ODS-managed HSA override
and custom llama.cpp binary; ODS refuses to combine `gfx1151` with another
architecture in one llama-server process because that override is not
device-scoped. Custom operator-provided HSA or llama.cpp override values are
preserved; only the exact values managed by ODS are removed when they no longer
match the selected GPU architecture.
Single-GPU, Apple, Windows-native AMD/Lemonade, externally managed inference,
and unpersisted all-GPU fallback configurations keep their existing behavior.

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
tier, artifact integrity, Lemonade identity, and runtime-profile fields as
read-only; use Model Manager instead. Use the manual procedure below only for
recovery or unsupported custom models, and verify every affected consumer
afterward.

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

On Windows installs:

```powershell
$env:USERPROFILE\ods\data\models\
```

Each model is normally a single `.gguf` file:

```bash
ls -lh ~/ods/data/models/*.gguf
```

The active model is recorded in `.env`:

```bash
grep -E "^(LLM_MODEL|GGUF_FILE|CTX_SIZE|MAX_CONTEXT)=" ~/ods/.env
```

`GGUF_FILE` is the filename ODS should load from `data/models/`.
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
  https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf
```

Then open Dashboard -> Models. If the filename matches a catalog entry, the
model should appear as downloaded and you can load it from the Dashboard.

## Bring Your Own GGUF

For a single local `.gguf`, the normal flow is:

1. Copy the file into `data/models/`.
2. Open Dashboard -> Models.
3. Load the local entry.

The Dashboard updates `.env`, `config/llama-server/models.ini`, and the active
runtime routing before restarting the inference service.

On Lemonade installs, loading a model directly inside the Lemonade app only
changes Lemonade's current runtime state. It does not update ODS's
`.env` or LiteLLM routing. Open WebUI talks through ODS/LiteLLM, so
its next chat can ask for the persisted ODS model and Lemonade may
unload the model you opened manually. Use Dashboard -> Models -> Load when you
want Open WebUI and other ODS clients to keep using the local GGUF.

Use the manual procedure below only if you cannot access the Dashboard or need
to repair an install by hand.

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

For Lemonade/AMD backends, use:

```yaml
model:
  default: "extra.MyModel-Q4_K_M.gguf"
  context_length: 65536
```

Also keep `auxiliary.compression.context_length` at the same value and use
`compression.threshold: 0.50`; older absolute-token thresholds can leave Hermes
waiting too long to compact.

5. For AMD/Lemonade installs, verify `config/litellm/lemonade.yaml`.

Each local model alias should use the `extra.<GGUF_FILE>` form and should keep
Qwen3 thinking disabled for clients that do not pass that flag themselves:

```yaml
extra_body:
  chat_template_kwargs:
    enable_thinking: false
```

6. If Perplexica is enabled, reseed or verify its model setting.

```bash
LLM_MODEL="$(grep -E '^LLM_MODEL=' .env | tail -n1 | cut -d= -f2 | tr -d '"')"
PERPLEXICA_PORT="$(grep -E '^PERPLEXICA_PORT=' .env | tail -n1 | cut -d= -f2 | tr -d '"')"
scripts/repair/repair-perplexica.sh "http://127.0.0.1:${PERPLEXICA_PORT:-3004}" "$LLM_MODEL"
```

Dashboard activation and `ods model swap` handle this automatically. Raw GGUF
or `.env` edits still require verification because Perplexica stores its own app
settings in its volume.

7. Restart the affected services.

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
- On AMD/Lemonade, a service is still asking for the raw filename instead of
  `extra.<GGUF_FILE>`.

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

For AMD/Lemonade, use `extra.<GGUF_FILE>`.

## Current Limitations

- Dashboard download and load support the ODS catalog and integrity-qualified
  GGUF artifacts discovered through the Hugging Face source.
- Import from an arbitrary URL is not a first-class Dashboard workflow. Local
  single-file GGUFs are discovered after they are copied into `data/models/`.
- `ods model swap` switches ODS tiers, not arbitrary GGUF files.
- `scripts/upgrade-model.sh` is a legacy helper for model-directory layouts and
  should not be used as the primary GGUF switch path on current installs.
