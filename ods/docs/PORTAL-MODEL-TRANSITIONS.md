# Model switching and conversation context

The inference process and the agent must agree on the active model, context
capacity, output budget and remote route identity. Loading a model is not enough
to complete a Portal model switch. The native agent's loaded configuration must
also pass readback before new conversations are admitted.

## Transaction boundaries

For `/model/activate` and remote route activation on installations with Portal
Edge configured, the host uses the existing owner-authenticated
Edge → ingress → native controller transport. Windows and
macOS hosts do not select a WSL distribution, user or executable in a request.
The controller uses the installation binding already qualified for the managed
Linux/systemd gateway. This does not add a native macOS agent installer or qualify
an unsupported installation topology.

1. `model-status` reads the running gateway's model contract.
2. `model-begin` verifies the revision, saves recovery state and acquires both
   admission holds before the host changes inference. A journal alone is not
   evidence that the holds were acquired.
3. The existing host activation proves the actual inference model and capacity.
   `model-apply` updates the agent as its registered owner, validates the staged
   configuration and verifies the loaded contract. Only the already bound
   gateway service may be restarted.
4. After dependent services and the host activation receipt are committed,
   `model-finish` verifies the final state and releases admission. Rollback
   requires the previous inference state to be proven before restoring the
   agent's exact saved configuration.

The fixed contract contains only model name, context length, output limit,
reasoning support and an optional credential-free route fingerprint. Requests
cannot select a filesystem path, endpoint, executable, user or service unit.
Sampling and template customizations are retained; context and output limits
are recalculated for the selected model.

## Interrupted switches

Transport errors do not cause automatic replay of model loading or controller
mutations. The host first reads the current transaction. Private durable
journals preserve its identity and bounded evidence across process restarts.

The model menu shows **Recover model switch** when the host reports an interrupted
transaction, including when the conversation's model source is unknown.
`GET /api/models/recovery` reads its summary; `POST /api/models/recovery` accepts
only `{}` and asks the host to reconcile that existing transaction. Both require
the Dashboard's owner credential. Private configuration and credential values
are excluded from the response.

Recovery may finish an already committed switch, or release a verified unchanged
or fully restored previous state. It checks configuration digests and actual
inference evidence. It does not choose or load another model. An intermediate
crash without sufficient proof remains pending and needs explicit repair;
there is no generic reset that discards the saved state or silently unlocks an
unverified model.

Windows `/runtime/lemonade/ensure` remains a bootstrap operation outside this
transaction. The launcher may need it before Edge and the agent have started.
It is not the chat model-switch endpoint and must not be described as providing
the interactive switch's admission and recovery guarantees.

Remote SSH configuration can involve a staging step followed by an egress proof.
Staging is allowed only when the previous local route can be verified unchanged.
Changing an active remote route through that staged path requires deactivating
it first. Synchronous remote route changes use the full transaction.

## Deployment and verification

Deploy the host relay, protected Python controller/worker bundle, native plugin,
ingress, Edge, API and dashboard together. The installer verifies the required
modules and their ownership. Updating only the host must fail before unloading
inference when the controller protocol is not yet available.

Relevant regression suites cover admission races, unknown sources, lost replies,
partial release, restart failure, exact rollback, route identity and recovery
after reload. The Python and JavaScript projections have parity tests. Physical
platform and model validation remains separate from these portable contract
tests; passing them does not guarantee every model follows instructions or that
every device has sufficient memory.
