import test from "node:test";
import assert from "node:assert/strict";
import {
  TURN_GUIDANCE_HEADER,
  createTurnGuidancePersistence,
  formatTurnGuidance,
  stripTurnGuidance,
  withTurnGuidance,
  withoutPersistedTurnGuidance,
} from "../plugin/turn-guidance.mjs";
import { composePromptBuildResult, promptContractForAgent } from "../plugin/prompt-contract.mjs";
import { ACTIVITY_CONTRACT } from "../plugin/activity-display.mjs";
import { executionContext } from "../plugin/completion-assurance.mjs";

const DELIVERY = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]";
const CREATE = 'Create a polished responsive static event website in a new workspace directory fleet-qualification-5d151135bff1. Actually write files and publish a verified Pixel workspace preview. Page title and one h1 must be exactly "Night Garden". Do the work now.' + DELIVERY;
const UPDATE = 'Update that same website: change both page title and h1 to exactly "Night Garden Revised", change the accent to amber. Publish the updated preview and provide its new URL.' + DELIVERY;
const QUESTION = "Without changing any files or rereading files, tell me the exact unique FLEET marker and the directory of the website we just edited." + DELIVERY;
const PIXEL = { agentId: "pixel", sessionKey: "agent:pixel:openai-user:ods-aaaa", runId: "run-1" };
const OTHER_SESSION = { ...PIXEL, sessionKey: "agent:pixel:openai-user:ods-bbbb" };

function guidanceFor(prompt, messages = []) {
  const contract = promptContractForAgent({ agentId: "pixel", contextTokenBudget: 65536 }, "pixel",
    { prompt, messages }, { configuredContextWindow: 65536 });
  return composePromptBuildResult(contract, { activity: ACTIVITY_CONTRACT, execution: executionContext() }).appendContext;
}

// OpenClaw 2026.6.33 composeModelPromptContext (attempt.llm-boundary.ts:250-258).
const openClawModelPrompt = ({ prependContext, prompt, appendContext }) =>
  [prependContext, prompt, appendContext].filter(value => Boolean(value?.trim())).join("\n\n");

const user = (content, timestamp = 1) => ({ role: "user", content, timestamp });

test("guidance is labelled, joined like OpenClaw and strippable back to owner prose", () => {
  assert.equal(formatTurnGuidance("  "), "");
  assert.equal(formatTurnGuidance(" Do X. "), `${TURN_GUIDANCE_HEADER}\nDo X.`);
  const guidance = guidanceFor(CREATE);
  assert.ok(guidance.startsWith(`${TURN_GUIDANCE_HEADER}\n`));
  const stored = withTurnGuidance(CREATE, guidance);
  assert.equal(stored, openClawModelPrompt({ prompt: CREATE, appendContext: guidance }));
  assert.equal(stripTurnGuidance(stored), CREATE);
  assert.equal(stripTurnGuidance(CREATE), CREATE);
  assert.equal(withTurnGuidance(QUESTION, ""), QUESTION);
});

test("the owner message is stored exactly as the model saw it, once", () => {
  const persistence = createTurnGuidancePersistence();
  const guidance = guidanceFor(CREATE);
  persistence.remember(PIXEL, CREATE, guidance);
  const written = persistence.beforeMessageWrite({ message: user([{ type: "text", text: CREATE }]) }, PIXEL);
  assert.deepEqual(written, {
    message: user([{ type: "text", text: openClawModelPrompt({ prompt: CREATE, appendContext: guidance }) }]),
  });
  assert.equal(persistence.beforeMessageWrite({ message: user([{ type: "text", text: CREATE }]) }, PIXEL), undefined,
    "a later write of the same text is not rewritten again");
  persistence.remember(PIXEL, UPDATE, guidanceFor(UPDATE));
  assert.deepEqual(persistence.beforeMessageWrite({ message: user(UPDATE) }, PIXEL),
    { message: user(withTurnGuidance(UPDATE, guidanceFor(UPDATE))) }, "string content keeps its form");
});

test("only the current owner message of the same Pixel session is rewritten", () => {
  const persistence = createTurnGuidancePersistence();
  const guidance = guidanceFor(CREATE);
  const again = () => persistence.remember(PIXEL, CREATE, guidance);
  again();
  for (const [event, context] of [
    [{ message: user(CREATE) }, OTHER_SESSION],
    [{ message: user(CREATE) }, { ...PIXEL, agentId: "main" }],
    [{ message: user("Different text") }, PIXEL],
    [{ message: { role: "assistant", content: [{ type: "text", text: CREATE }] } }, PIXEL],
    [{ message: { role: "toolResult", toolCallId: "t", content: [{ type: "text", text: CREATE }] } }, PIXEL],
    [{ message: user([{ type: "image", data: "x", mimeType: "image/png" }]) }, PIXEL],
  ]) assert.equal(persistence.beforeMessageWrite(event, context), undefined);
  assert.equal(persistence.pendingCount(), 1);
  persistence.forget(PIXEL);
  assert.equal(persistence.beforeMessageWrite({ message: user(CREATE) }, PIXEL), undefined, "agent_end drops it");
  persistence.remember(PIXEL, QUESTION, guidanceFor(QUESTION));
  assert.equal(guidanceFor(QUESTION), undefined);
  assert.equal(persistence.pendingCount(), 0, "no guidance, nothing to store");
  persistence.remember({ ...PIXEL, agentId: "main" }, CREATE, guidance);
  persistence.remember({ agentId: "pixel" }, CREATE, guidance);
  assert.equal(persistence.pendingCount(), 0);
});

test("attachments keep their parts and a stored message is never extended twice", () => {
  const persistence = createTurnGuidancePersistence();
  const guidance = guidanceFor(CREATE);
  const image = { type: "image", data: "aGk=", mimeType: "image/png" };
  persistence.remember(PIXEL, CREATE, guidance);
  const written = persistence.beforeMessageWrite({ message: user([{ type: "text", text: CREATE }, image]) }, PIXEL);
  assert.deepEqual(written.message.content, [{ type: "text", text: withTurnGuidance(CREATE, guidance) }, image]);
  persistence.remember(PIXEL, CREATE, guidance);
  assert.equal(persistence.beforeMessageWrite({ message: user(withTurnGuidance(CREATE, guidance)) }, PIXEL), undefined);
  assert.equal(persistence.pendingCount(), 0);
});

test("pending entries stay bounded", () => {
  const persistence = createTurnGuidancePersistence();
  for (let index = 0; index < 300; index += 1)
    persistence.remember({ ...PIXEL, sessionKey: `session-${index}` }, CREATE, "guidance");
  assert.equal(persistence.pendingCount(), 256);
});

test("classification reads owner prose, not guidance stored with earlier messages", () => {
  const github = "Research the official Osmantic/ODS GitHub repository." + DELIVERY;
  const githubGuidance = guidanceFor(github);
  assert.match(githubGuidance, /https:\/\/github\.com\/Osmantic\/ODS/);
  const raw = [user(github), { role: "assistant", content: [{ type: "text", text: "Done." }] }];
  const stored = [user(withTurnGuidance(github, githubGuidance)), raw[1]];
  const sanitized = withoutPersistedTurnGuidance(stored);
  assert.deepEqual(sanitized, raw);
  assert.equal(withoutPersistedTurnGuidance(raw), raw, "unchanged history keeps its identity");
  for (const prompt of [QUESTION, "thanks" + DELIVERY, UPDATE]) {
    assert.deepEqual(
      promptContractForAgent({ agentId: "pixel", contextTokenBudget: 65536 }, "pixel", { prompt, messages: sanitized }),
      promptContractForAgent({ agentId: "pixel", contextTokenBudget: 65536 }, "pixel", { prompt, messages: raw }),
    );
  }
  const parts = withoutPersistedTurnGuidance([user([{ type: "text", text: withTurnGuidance(github, githubGuidance) }])]);
  assert.deepEqual(parts, [user([{ type: "text", text: github }])]);
});

test("three owner turns replay as append-only prefixes of each other", () => {
  // Model view of each owner message during its own run, and the stored copy
  // OpenClaw replays for every later run.
  const persistence = createTurnGuidancePersistence();
  const history = [];
  const seen = [];
  for (const [index, prompt] of [CREATE, UPDATE, QUESTION].entries()) {
    const guidance = guidanceFor(prompt, history);
    const context = { ...PIXEL, runId: `run-${index}` };
    persistence.remember(context, prompt, guidance);
    seen.push(openClawModelPrompt({ prompt, appendContext: guidance }));
    const written = persistence.beforeMessageWrite({ message: user([{ type: "text", text: prompt }], index) }, context);
    const stored = written?.message ?? user([{ type: "text", text: prompt }], index);
    history.push(stored, { role: "assistant", content: [{ type: "text", text: `answer ${index}` }] });
    persistence.forget(context);
  }
  const replayed = history.filter(message => message.role === "user").map(message => message.content[0].text);
  assert.deepEqual(replayed, seen);
  assert.notEqual(seen[0], CREATE);
  assert.notEqual(seen[1], UPDATE);
  assert.equal(seen[2], QUESTION);
});

test("the plugin entry stores owner messages through before_message_write", async () => {
  const { readFileSync } = await import("node:fs");
  const source = readFileSync(new URL("../plugin/index.js", import.meta.url), "utf8");
  assert.match(source, /api\.on\("before_message_write", \(event, context\) =>\s+turnGuidance\.beforeMessageWrite\(event, context\)\s+\);/);
  assert.match(source, /turnGuidance\.remember\(context, rawEvent\?\.prompt, result\?\.appendContext\);/);
  assert.match(source, /withoutPersistedTurnGuidance\(rawEvent\?\.messages\)/);
  assert.match(source, /api\.on\("agent_end", \(_event, context\) => turnGuidance\.forget\(context\)\);/);
});
