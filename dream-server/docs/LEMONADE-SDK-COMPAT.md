# Lemonade SDK Compatibility

Dream Server's Linux installer can wrap an existing Lemonade SDK install instead
of starting its own managed Lemonade runtime. This entry point was added for the
AMD Lemonade integration path, but the external-Lemonade contract is not
AMD-only: if Lemonade is already installed, configured, and serving compatible
OpenAI-style endpoints on NVIDIA, CPU, or another Lemonade-supported backend,
Dream Server should treat it as the same provider boundary. Hardware support is
still limited by the upstream Lemonade runtime and Dream Server's installer and
preflight coverage for that target. The longer-term contract for making
Lemonade a supported provider mode across platforms is defined in
[Engine Provider Modes](ENGINE-PROVIDER-MODES.md).

## Provider and Hardware Scope

Treat Lemonade as the inference provider and the GPU vendor as a deployment
detail. AMD, NVIDIA, CPU, NPU, ROCm, Vulkan, or other backend choices determine
how Lemonade itself runs; Dream Server's integration verifies the provider
surface it receives:

- the configured Lemonade base URL is reachable from Dream containers;
- `LEMONADE_MODEL` names a chat-capable model in Lemonade's catalog;
- LiteLLM can complete through Lemonade for Dream's app-facing chat route;
- optional selected capabilities are either proven through Lemonade or clearly
  left to Dream-owned services such as Whisper, Kokoro, or embeddings.

This keeps the docs honest for AMD systems while also giving non-AMD Lemonade
operators the same configuration and validation path when their Lemonade service
already works.

macOS needs a separate note. Upstream Lemonade supports macOS with a Metal
llama.cpp backend, but Dream Server's supported macOS installer already uses a
host-native `llama-server` with Metal acceleration. This PR does not replace
that macOS path or claim a validated macOS external-Lemonade install; treat
macOS Lemonade as a follow-up smoke target for the provider contract.

## Install Around Existing Lemonade

Start Lemonade first, then install Dream Server with:

```bash
./install.sh --use-existing-lemonade
```

If Lemonade is not using its default URL, pass it explicitly:

```bash
./install.sh --use-existing-lemonade --lemonade-url http://localhost:13305
```

When `--lemonade-url` is omitted, Dream Server checks `http://localhost:13305`
first, then `http://localhost:8000`. This covers current Lemonade Server
packages and older Python SDK installs. If neither endpoint is reachable, the
installer falls back to `http://localhost:13305` and the Phase 12 completion
check will fail with a targeted Lemonade routing error instead of declaring a
false-green install.

If Lemonade requires an API key:

```bash
./install.sh --use-existing-lemonade \
  --lemonade-url http://localhost:13305 \
  --lemonade-api-key "$LEMONADE_API_KEY"
```

Dream Server will keep Lemonade unmanaged:

- it does not install Lemonade;
- it does not start or stop Lemonade;
- it does not download Dream's GGUF model into `data/models`;
- it routes Dream services through LiteLLM, which calls the existing Lemonade
  service.

This only applies to the LLM runtime. Dream Server's optional voice and image
services are separate from Lemonade:

- Whisper speech-to-text listens on port `9000`;
- Kokoro text-to-speech listens on port `8880`;
- ComfyUI image generation listens on port `8188`.

If you choose **Full Stack**, Dream Server still enables those services by
default. That is useful when Dream should own the full app stack, but it can
conflict with an existing local AI setup that already runs Whisper, TTS, ComfyUI,
or other services on the same ports.

To wrap an existing Lemonade install without Dream-managed voice or image
services:

```bash
./install.sh --use-existing-lemonade --no-voice --no-comfyui
```

If you are using `--all`, put the opt-out flags after `--all` because installer
flags are processed left to right:

```bash
./install.sh --use-existing-lemonade --all --no-voice --no-comfyui
```

If you want Dream Server's Whisper or ComfyUI services but need to avoid port
collisions, set alternate ports before running the installer:

```bash
WHISPER_PORT=9100 COMFYUI_PORT=8190 \
  ./install.sh --use-existing-lemonade
```

If the installer reports that ports `9000`, `8880`, or `8188` are already in
use, either disable the matching Dream feature or choose a different port where
the installer supports it. Today, a Kokoro/TTS conflict on port `8880` should be
handled with `--no-voice`. The port conflict is from the optional Dream service,
not from Lemonade itself.

Windows AMD installs already use a separate host-managed Lemonade path. The
macOS Apple Silicon installer also has a separate native Metal path and does
not currently accept these external-Lemonade flags. These flags are for Linux
installs that should attach to a pre-existing Lemonade SDK service.

## Model Selection

Dream Server auto-detects the first model id returned by Lemonade's
`/api/v1/models` endpoint that does not look like a specialized non-chat model
(image, audio, embedding, or reranking) and writes it to `LEMONADE_MODEL`.

Set `LEMONADE_MODEL` only if you want Dream Server to use a specific served
model:

```bash
LEMONADE_MODEL=Qwen3-0.6B-GGUF ./install.sh --use-existing-lemonade
```

The model id should match an id returned by Lemonade's model list endpoint, for
example:

```bash
curl http://localhost:13305/api/v1/models
```

Use a text/chat model for `LEMONADE_MODEL`. Image models such as Flux, SDXL, or
Stable Diffusion can appear in Lemonade's model list, but they are not valid for
Dream Server's chat/completions route.

### Migrating Older `LLM_MODEL`-Only Installs

`LEMONADE_MODEL` is the canonical provider model setting. For compatibility,
Dream Server temporarily accepts `LLM_MODEL` as the external Lemonade chat
target only when that exact id exists in `/api/v1/models` and is identified as
a text/chat model. The dashboard emits `chat_model_legacy_llm_model` while this
fallback is active, or `chat_model_legacy_llm_model_ignored` when the legacy
value cannot be accepted safely.

Migrate the existing value explicitly:

```dotenv
LLM_MODEL=Qwen3-0.6B-GGUF
LEMONADE_MODEL=Qwen3-0.6B-GGUF
```

Then rerun the active provider probe. Dream Server intentionally ignores
`LLM_MODEL` when it is absent from Lemonade's catalog or identifies an image,
audio, embedding, or reranking model; this prevents stale tier defaults from
silently replacing the provider's real chat model.

Phase 12 verifies the selected model with a real chat completion through
LiteLLM. If Lemonade is reachable from the host but not from Docker containers,
if the selected model id is wrong, or if the selected model is an image/non-chat
model, the installer fails there with a recovery hint instead of finishing with
a broken chat path.

## Linux Docker Networking

On Linux, Docker containers cannot always reach a host service that is bound
only to `127.0.0.1`. Dream Server converts a host URL such as
`http://localhost:13305` into the container-side URL
`http://host.docker.internal:13305`, but Lemonade must be reachable there.

On a trusted host, configure Lemonade to bind beyond loopback:

```bash
lemonade config set host=0.0.0.0
```

If UFW or firewalld is active, the installer adds a scoped rule that allows
Dream containers on `dream-network` to reach the configured Lemonade port. If
that automatic rule cannot be added, allow the `dream-network` subnet to reach
the Lemonade API port manually.

If you expose Lemonade beyond localhost, set `LEMONADE_API_KEY` or
`LEMONADE_ADMIN_API_KEY` in Lemonade and pass the matching key to Dream Server
with `--lemonade-api-key`.

`dream doctor` warns when external Lemonade is host-routed from Docker without a
user-provided Lemonade API key. The installer-generated
`LITELLM_LEMONADE_API_KEY=sk-dream-lemonade-*` value is only the key LiteLLM
sends upstream; it does not prove that the Lemonade daemon requires
authentication.

## Managed vs External

| Mode | Who owns Lemonade? | Default API target | Model storage |
| --- | --- | --- | --- |
| Managed AMD Lemonade | Dream Server | `llama-server:8080/api/v1` inside Docker | Dream `data/models` |
| Existing Lemonade SDK | User / OS service | Auto-detected `host.docker.internal:<port>/api/v1` from containers | Lemonade cache |

In both modes, Dream services talk to LiteLLM first. LiteLLM normalizes model
routing and gives Open WebUI, Hermes, Perplexica, and other services one stable
OpenAI-compatible gateway.

## Diagnostics

`dream doctor` and the dashboard `/api/providers/lemonade` endpoint report
external Lemonade as the same provider contract even when the machine itself is
not AMD. The older `/api/gpu/amd-runtime` endpoint remains a compatibility
alias for clients from the original AMD/Lemonade rollout:

```text
runtime: lemonade
location: host
runtimeMode: external-lemonade
managedByDreamServer: false
```

Use this to distinguish Lemonade service/network issues from Dream-managed
container failures.
