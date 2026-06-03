# Lemonade SDK Compatibility

Dream Server's Linux installer can wrap an existing Lemonade SDK install instead
of starting its own managed Lemonade runtime. This is intended for AMD Linux
systems where Lemonade is already installed, configured, and serving models.

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

Windows AMD installs already use a separate host-managed Lemonade path. These
flags are for Linux installs that should attach to a pre-existing Lemonade SDK
service.

## Model Selection

Dream Server auto-detects the first model id returned by Lemonade's
`/api/v1/models` endpoint that does not look like an image-generation model
and writes it to `LEMONADE_MODEL`.

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

## Managed vs External

| Mode | Who owns Lemonade? | Default API target | Model storage |
| --- | --- | --- | --- |
| Managed AMD Lemonade | Dream Server | `llama-server:8080/api/v1` inside Docker | Dream `data/models` |
| Existing Lemonade SDK | User / OS service | Auto-detected `host.docker.internal:<port>/api/v1` from containers | Lemonade cache |

In both modes, Dream services talk to LiteLLM first. LiteLLM normalizes model
routing and gives Open WebUI, Hermes, Perplexica, and other services one stable
OpenAI-compatible gateway.

## Diagnostics

`dream doctor` and the dashboard AMD runtime endpoint report external Lemonade
as:

```text
runtime: lemonade
location: host
runtimeMode: external-lemonade
managedByDreamServer: false
```

Use this to distinguish Lemonade service/network issues from Dream-managed
container failures.
