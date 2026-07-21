# Swap-Safe Extensions

This guide is for extension authors whose service sends prompts to a local
language model. The default ODS contract is simple: use the stable ODS gateway
route and do not store a concrete model name in your app.

## The Three Tiers

| Tier | Use this when | Author work | Swap behavior |
|---|---|---|---|
| Tier 1: gateway default | Your app speaks the OpenAI API and does not need model metadata | Configure the app to use the ODS gateway alias | Swap-safe by construction |
| Tier 2: manifest-aware | Your app needs a context floor, route probe, or compatibility gate | Add the `service.llm` block to `manifest.yaml` | ODS can gate swaps and probe the app after each swap |
| Tier 3: dynamic | Your app needs live model id, context length, capabilities, or sequence ordering | Read model-state metadata and subscribe to swap events when available | The app refreshes itself without restart or stale cached model state |

If your app can use Tier 1, use Tier 1.

## Tier 1: Point At The Gateway

Configure the app once:

```yaml
base_url: http://litellm:4000/v1
model: ods/current
api_key: ${LITELLM_KEY}
```

Equivalent OpenAI SDK settings are:

```text
OPENAI_BASE_URL=http://litellm:4000/v1
OPENAI_MODEL=ods/current
OPENAI_API_KEY=${LITELLM_KEY}
```

Do not persist GGUF filenames, Lemonade `extra.*` ids, llama-server model names,
or catalog ids in your app config. The gateway alias is the app contract.

## Tier 2: Add The `llm:` Manifest Block

Any extension that consumes an LLM should declare the LLM contract in
`extensions/services/<id>/manifest.yaml`:

```yaml
service:
  id: my-agent
  name: My Agent
  llm:
    consumes: true
    route: gateway
    pinning: none
    min_context: 65536
    probe:
      kind: chat
      path: /v1/chat/completions
      auth: env:LITELLM_KEY
```

Field reference:

| Field | Required | Meaning |
|---|---:|---|
| `consumes` | Yes | `true` when the service sends prompts or completions to an LLM. |
| `route` | For consumers | `gateway` for `http://litellm:4000/v1` and `ods/current`; `direct` only when the app cannot use the gateway. |
| `pinning` | For consumers | `none` when the app stores no concrete model id; `dynamic` when it has a refresh/reconcile path after swaps. |
| `min_context` | Optional | Minimum context length required for the app's LLM path. Hermes-style agent flows use `65536` unless a lower floor is proven safe. |
| `probe.kind` | For consumers | `chat`, `completion`, or `custom`. |
| `probe.path` | For consumers | Probe endpoint the harness can call after every model swap. |
| `probe.auth` | For consumers | How the harness authenticates, such as `env:LITELLM_KEY`, `cookie:dream-session`, or a service-specific provisioned credential. |

Direct routes are allowed, but they are not the default. A direct route must
declare `pinning: dynamic` and a probe that proves the app refreshed to the
new model after a swap.

## Tier 3: Dynamic Model-Aware Apps

Dynamic apps should treat model metadata as runtime state, not install-time
configuration. When the model-state API and swap event stream are available,
use them to refresh:

- current model id and display name;
- context length and capability flags;
- monotonic sequence number for ignoring stale updates;
- swap progress and rollback events.

The app should keep working during a swap by routing through the gateway alias
and refreshing metadata after the sequence advances.

## Badges And Gates

The dashboard may badge LLM consumers as:

| Badge | Meaning |
|---|---|
| Swap-safe | The app uses the gateway alias with no model pin, or declares a dynamic refresh path and passes its probe. |
| Not swap-safe | The app appears to talk directly to a runtime or stores a model id without an `llm:` contract. |
| Gated | The selected model does not satisfy a declared floor, such as `min_context`. |
| Probe failed | The model swap completed, but the app-specific probe failed or could not authenticate. |

Model swaps must show agent viability gates visibly. For example, a model below
an agent's `min_context` floor should be blocked or warned in the UI before the
user swaps, not discovered later as a broken agent session.

## Author Checklist

- Use `http://litellm:4000/v1` and `ods/current` unless you have a specific
  reason not to.
- Keep concrete model names out of your app's persistent settings.
- Add `service.llm` when the app consumes an LLM.
- Declare `min_context` when agent behavior depends on a context floor.
- Provide a deterministic probe that proves the app can use the active model.
- Document any credential the probe needs. A login wall is not a passing probe.
- Validate the manifest with the schema and extension audit before opening a PR:

```bash
python3 -c "import yaml; yaml.safe_load(open('extensions/services/my-service/manifest.yaml'))"
python3 scripts/audit-extensions.py --project-dir .
```
