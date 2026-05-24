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

Windows AMD installs already use a separate host-managed Lemonade path. These
flags are for Linux installs that should attach to a pre-existing Lemonade SDK
service.

## Model Selection

Set `LEMONADE_MODEL` if the default Dream model id does not match a model in
your Lemonade library:

```bash
LEMONADE_MODEL=Qwen3-0.6B-GGUF ./install.sh --use-existing-lemonade
```

The model id should match an id returned by Lemonade's model list endpoint, for
example:

```bash
curl http://localhost:13305/api/v1/models
```

## Linux Docker Networking

On Linux, Docker containers cannot always reach a host service that is bound
only to `127.0.0.1`. Dream Server converts a host URL such as
`http://localhost:13305` into the container-side URL
`http://host.docker.internal:13305`, but Lemonade must be reachable there.

On a trusted host, configure Lemonade to bind beyond loopback:

```bash
lemonade config set host=0.0.0.0
```

If you expose Lemonade beyond localhost, set `LEMONADE_API_KEY` or
`LEMONADE_ADMIN_API_KEY` in Lemonade and pass the matching key to Dream Server
with `--lemonade-api-key`.

## Managed vs External

| Mode | Who owns Lemonade? | Default API target | Model storage |
| --- | --- | --- | --- |
| Managed AMD Lemonade | Dream Server | `llama-server:8080/api/v1` inside Docker | Dream `data/models` |
| Existing Lemonade SDK | User / OS service | `host.docker.internal:13305/api/v1` from containers | Lemonade cache |

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
