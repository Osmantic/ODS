import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { workProviderExecutorTestOnly } from "../deploy/work-provider/executor.mjs";
import { inspectWorkProviderCredentialCustody } from "../deploy/work-provider/credential-custody.mjs";
import { moonshotTransportTestOnly } from "../deploy/work-provider/moonshot-transport.mjs";
import { providerIdempotencyKey } from "../deploy/work-provider/run-ledger.mjs";
import { NOW, SUFFIX, semanticSetup } from "./fixtures/work-provider-exec.mjs";

// A deliberately long reasoning string so a truncated replay would be caught verbatim.
const REASONING = "step-one: inspect the module. step-two: note the tool contract. step-three: choose read_file. step-four: continue after the tool result. ".repeat(4);

function chatResponse({ id, finishReason, content, reasoningContent, toolCalls, usage }) {
  const message = { role: "assistant", content };
  if (reasoningContent !== undefined) message.reasoning_content = reasoningContent;
  if (toolCalls) message.tool_calls = toolCalls;
  return { id, object: "chat.completion", created: 1, model: "kimi-k3", choices: [{ index: 0, finish_reason: finishReason, logprobs: null, message }], usage };
}

async function credentialCustody(s, root) {
  const directory = join(root, "credential"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  return inspectWorkProviderCredentialCustody({ resolvedProvider: s.resolvedProvider, policy: s.policy, credentialPath, now: NOW, suffix: SUFFIX });
}

test("multi-turn K3 settles turn 1 before turn 2 and replays complete reasoning_content through the bound transport", async () => {
  const s = semanticSetup("moonshot-kimi");
  const root = await mkdtemp(join(tmpdir(), "pixel-multiturn-"));
  try {
    const custody = await credentialCustody(s, root);
    async function runTurn({ ledger, key, input, responseValue }) {
      let capturedBody = null;
      const transport = async (args) => moonshotTransportTestOnly.executeMoonshotChatTurnWithExchange({
        ...args, credentialHandle: custody.handle, proxyHost: "127.0.0.1",
      }, async (request) => {
          capturedBody = JSON.parse(request.body.toString("utf8"));
          const body = Buffer.from(JSON.stringify(responseValue));
          return { statusCode: 200, headers: { "x-request-id": `req-${key}` }, body, networkBytes: request.body.length + body.length };
      });
      const result = await workProviderExecutorTestOnly.executeProviderTurnWithTransport({
        decision: s.decision, request: s.request, routerPolicy: s.routerPolicy, resolvedProvider: s.resolvedProvider,
        enabledPrivatePolicies: s.enabledPrivatePolicies, qualifications: s.qualifications,
        policy: s.policy, privatePolicy: s.privatePolicy, qualification: s.qualification,
        taskId: "pixel-k3-multiturn", model: s.model, idempotencyKey: key, ledger,
        credentialHandle: custody.handle, input, estimatedInputTokens: 100, maxOutputTokens: 128, maxEstimatedCostMicros: 1000,
        now: NOW, suffix: SUFFIX,
      }, transport);
      return { result, capturedBody };
    }

    const key1 = providerIdempotencyKey({ task: "pixel-k3-multiturn", turn: 1 });
    const input1 = {
      schemaVersion: 1, model: s.model,
      messages: [{ role: "user", content: "analyze" }],
      tools: [{ name: "read_file", parameters: { type: "object" } }],
      maxOutputTokens: 128, reasoningEffort: "low",
    };
    const { result: turn1, capturedBody: body1 } = await runTurn({
      ledger: undefined, key: key1, input: input1,
      responseValue: chatResponse({
        id: "chatcmpl-turn1", finishReason: "tool_calls", content: null, reasoningContent: REASONING,
        toolCalls: [{ id: "call_1", type: "function", function: { name: "read_file", arguments: "{\"path\":\"README.md\"}" } }],
        usage: { prompt_tokens: 80, completion_tokens: 20, total_tokens: 100 },
      }),
    });
    assert.equal(turn1.status, "succeeded");
    assert.equal(turn1.ledger.requests[0].state, "succeeded");
    assert.equal(turn1.assistant.message.toolCalls[0].name, "read_file");
    assert.equal(turn1.assistant.message.providerState.assistantMessage.reasoning_content, REASONING);

    const key2 = providerIdempotencyKey({ task: "pixel-k3-multiturn", turn: 2 });
    const input2 = {
      schemaVersion: 1, model: s.model,
      messages: [
        { role: "user", content: "analyze" },
        turn1.assistant.message,
        { role: "tool", toolCallId: "call_1", content: "README.md contents" },
      ],
      maxOutputTokens: 128, reasoningEffort: "low",
    };
    const { result: turn2, capturedBody: body2 } = await runTurn({
      ledger: turn1.ledger, key: key2, input: input2,
      responseValue: chatResponse({
        id: "chatcmpl-turn2", finishReason: "stop", content: "done",
        usage: { prompt_tokens: 140, completion_tokens: 10, total_tokens: 150 },
      }),
    });
    assert.equal(turn2.status, "succeeded");
    assert.equal(turn2.ledger.requests.length, 2);
    assert.equal(turn2.ledger.requests[0].state, "succeeded");
    assert.equal(turn2.ledger.requests[1].state, "succeeded");
    assert.equal(turn2.assistant.message.content, "done");
    assert.equal(body2.messages.length, 3);
    assert.equal(body2.messages[1].role, "assistant");
    assert.equal(body2.messages[1].reasoning_content, REASONING);
    assert.equal(body2.messages[1].tool_calls[0].id, "call_1");
    assert.equal(body2.messages[2].role, "tool");
    assert.equal(body2.messages[2].tool_call_id, "call_1");
    assert.equal(body2.messages[2].content, "README.md contents");
  } finally { await rm(root, { recursive: true, force: true }); }
});
