# Native ODS integration

Claude-Mem connects to **ODS itself**: its admitted conversation requests,
validated activity events, and final verified deliveries. It needs no agent
plugin and makes no changes to the underlying agent's configuration.

The ODS extension runs a local Claude-Mem worker, stores memory in
`data/claude-mem`, and uses the existing ODS model gateway (`ods/current`) for
observation extraction. SQLite search is enabled; Chroma and telemetry are off
in this lightweight deployment.

## Enable native memory

This optional integration ships with ODS. No external source patch or agent
plugin is required. The service remains disabled until explicitly enabled.

Set these values in the ODS installation's `.env`:

```dotenv
ODS_CLAUDE_MEM_ENABLED=true
ODS_CLAUDE_MEM_PORT=37778
ODS_CLAUDE_MEM_SCOPE=conversation
```

After upgrading ODS, redeploy the native ingress through the existing ODS
installation workflow so its environment receives these settings.
Then enable the service:

```bash
ods enable claude-mem
ods start claude-mem
```

The worker's viewer and API are at `http://127.0.0.1:37778`; its health endpoint
is `/api/health`. The API has no authentication, so its exposure is limited by
network placement:

- **Host:** the published port binds `127.0.0.1` only. Any local process or
  user on the ODS host can read, write and delete memory through it, the same
  as a desktop Claude-Mem worker.
- **Containers:** the worker is not on the shared `ods-network`. It sits on a
  private Compose network whose only other member is `claude-mem-gateway`.
  Other ODS services and extensions cannot reach the worker's API.
- **Gateway:** `claude-mem-gateway` (`gateway.mjs`) is the one container on
  `ods-network`. It relays the worker's observer calls to LiteLLM and answers
  the dashboard's `GET /api/health` probe from the worker. It forwards no other
  worker request.

`LITELLM_KEY` is passed to the existing local model gateway inside the ODS
network. This integration does not choose a new model or change ODS's
local/cloud routing policy.

## Memory scope

`conversation` is the default. Each ODS conversation has its own persistent
memory, including when the underlying session has been replaced or compacted.

For a personal, single-owner ODS appliance, set
`ODS_CLAUDE_MEM_SCOPE=installation` to recall memories across its chats. This
intentionally shares one `ods` project across all conversations on that
installation. It is not an account-isolation boundary for a multi-user service.
Scope comes only from operator configuration, never from model output.

## What the integration records

| ODS event | Claude-Mem operation |
| --- | --- |
| New admitted request | Session init with the native request ID and `platformSource: ods` |
| Before model execution | Retrieve the five most recent observations in scope as a bounded, model-only reference |
| Validated terminal activity event | Store `ODSActivity` with its source and status |
| Verified final delivery | Store `ODSConversation`, preserve the verification separately, summarize |
| Cached response replay | No new capture or model execution |
| Conversation deletion | Fence queued writes and request deletion of the exact Claude-Mem session |

The integration captures ODS's **public activity projection**, not raw tools
hidden inside an agent runtime. Activity records are labeled as observations,
not independent proof of success. Final messages use ODS's verified delivery.
Streaming and non-streaming requests use the same native boundary.
Both modes collect a final activity snapshot before the final answer is stored.
Private blocks are removed before capture size limits are applied; nested and
unfinished private blocks stay private. An entirely private prompt skips capture.

Memory reads and writes have short timeouts. Unavailable memory leaves chat
usable and emits a sanitized warning. Failed writes stop that turn's write
chain; they are not reported as saved and are not silently replayed. This
version does not provide an offline outbox. Native request and tool-use IDs
support the worker's existing duplicate detection.

The released worker refuses to delete a session while its observer is active.
ODS reports `memory-deletion-unconfirmed` in that case, rather than claiming all
copies were erased. Retry deletion once the observer is idle. A session that
was never captured or was already deleted is treated as absent.

## Validation

From the repository root:

```bash
node --test ods/extensions/services/pixel-agent/tests/claude_mem*.test.mjs
```

The tests cover capture/recall, privacy skips, bounded responses, failed writes,
conversation isolation, deletion, cached replay, and both ODS response modes.

## Disable

Set `ODS_CLAUDE_MEM_ENABLED=false`, redeploy the ingress, and run
`ods disable claude-mem`. Retain `data/claude-mem` for backup or reinstallation;
remove it separately only when you intend to erase persisted memory.

## Provenance

The adapter, service packaging, and tests originate from
[thedotmack/claude-mem#4980](https://github.com/thedotmack/claude-mem/pull/4980)
(commit `22c884d4a8bf02d08b73734c0fcf459db22178df`), Apache-2.0,
Copyright TheDotMack. The worker is installed as the pinned `claude-mem@15.0.1`
package; no Pixel vendor code is changed.
