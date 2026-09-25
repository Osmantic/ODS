---
title: Have the first real Pixel conversation
doc_type: tutorial
audience: [owner, operator]
feature_status: mixed
owners: [documentation]
sources_of_truth: [README.md, CONTROL-SURFACE.md, control/server.py, control/ui/app.js, schemas/control-chat-v1.schema.json, schemas/control-chat-turn-request-v1.schema.json]
last_verified_at: 2026-08-27
---

# Have the first real Pixel conversation

This procedure proves that the owner workspace can use the exact configured local Pixel agent. It is intentionally separate from deployment verification and from synthetic or model-off qualification.

## Before you send a message

Confirm:

- `./pixel verify` succeeds on the installed host;
- private onboarding names the intended OpenClaw binary, state directory, agent, model provider, and model ID;
- the configured local model endpoint is running and privately reachable; and
- you are using the dedicated deployment-owner account.

Start the workspace:

```bash
./pixel ui
```

Open the exact URL printed for this process. Its shape is a loopback address with a process-lifetime review fragment. Do not bookmark, share, log, or substitute the token. Restarting `./pixel ui` creates a new access boundary, so an old URL should be treated as stale.

## Read the connection state

The composer is enabled only when the private URL is authorized and chat reports `ready` for the exact configured agent.

| UI message | Meaning | Owner action |
|---|---|---|
| Connected to the configured Pixel agent | The launcher, state, agent, provider, and model configuration passed the local safety checks | Continue with the bounded message below |
| Pixel chat is not connected to an exact configured local agent yet | Required private onboarding is absent or configuration has not been completed | Finish configuration; do not call this a model failure |
| Conversation custody could not be verified safely | A private file, runtime identity, history record, or receipt failed validation | Stop, preserve private logs, and use the troubleshooting path; no partial history is authoritative |
| Task is running | The local turn is in durable progress and the page may reconnect | Wait or reconnect; do not submit a duplicate request |
| Needs review | The last turn failed or was interrupted | Inspect its state and recover before assuming an answer exists |

`ready` means the exact local chat runtime is safely configured. It does not mean every tool, limb, external provider, or consequential action is available.

## Send a bounded first message

Use a harmless request that requires the configured model but no tool or external effect. For example:

> In two sentences, explain the difference between deployment verification and a real model-turn check. Do not use tools.

Do not include credentials, private paths, customer data, or a request to mutate another system. This first check is about the model route and conversation custody.

The browser sends bounded text to one fixed configured agent. It cannot select an arbitrary agent or command. While the turn runs, the composer is disabled so a repeated click cannot create duplicate work.

## Interpret the result

A successful first turn shows:

- your bounded user message;
- content-free progress state;
- bounded assistant text;
- a succeeded turn state; and
- when present, content-free model and capability accounting.

The model receipt can prove that the provider and model identities matched the configured launcher result and can retain bounded aggregate usage. It does not prove answer quality, task completion, publication, deployment, or acceptance.

If the turn is `failed`, Pixel claims no answer. If it is `interrupted`, recovery keeps it interrupted rather than fabricating a response. Do not copy a partial launcher log into the conversation as if it were the answer, and do not retry until you know whether the original turn settled.

## Then test one enabled capability

After the harmless model-only turn succeeds, choose one read-only capability that the approved profile actually enables. Use non-sensitive input and verify:

- the result is useful;
- the UI reports the tool-call count without exposing tool arguments or private result content;
- the capability state is within the declared boundary; and
- consequential or unclassified activity remains pending until its independent broker/approval evidence exists.

Disabled or intentionally unavailable behavior is a valid safety result. It is not a reason to enable a limb during this test.

## Evidence to retain

Record the installed release/source identity, configured model identity hash or bounded receipt reference, conversation/turn handle, final state, time, and the operator's quality assessment. Keep private message text and session data outside Git.

The check passes only with a real configured local model response independently observed by the operator. A scripted answer, mocked launcher, synthetic fixture, or model-off run may test plumbing but cannot satisfy this acceptance step.

Next: [status and evidence](../concepts/status-and-evidence.md), [post-install checklist](../getting-started/post-install-checklist.md), and [operations](../../OPERATIONS.md).
