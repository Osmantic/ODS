import test from "node:test";
import assert from "node:assert/strict";
import {
  ODS_CONVERSATION_CONTRACT,
  ODS_VERIFICATION_FAILED_CONTRACT,
  ODS_VERIFICATION_PENDING_CONTRACT,
  ODS_WORKSPACE_PREVIEW_CONTRACT,
  ODS_WORKSPACE_VISUAL_CONTINUATION_CONTRACT,
  composePromptBuildResult,
  promptContractForAgent,
} from "../plugin/prompt-contract.mjs";
import { ACTIVITY_CONTRACT } from "../plugin/activity-display.mjs";
import { executionContext } from "../plugin/completion-assurance.mjs";
import { GOAL_CONTRACT } from "../plugin/goal-progress.mjs";

const DELIVERY = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]";

// The three consecutive owner messages of Mac session dcef4e47. On ee738a9a
// their system prompts were 52029, 51004 and 47527 chars; the cached prefix
// broke at token 14,675, where the message-selected contracts begin.
const MAC_JOURNEY = [
  'Create a polished responsive static event website in a new workspace directory fleet-qualification-4de6a2213f1b. Actually write files and publish a verified Pixel workspace preview. Page title and one h1 must be exactly "Night Garden FLEET-63c79f3de4". Include three event cards: Dawn jazz, River lantern walk, and Midnight sold-out concert. Initially hide the entire Midnight sold-out concert card. Provide an accessible button named exactly "Show sold out" that reveals that card when clicked. Mobile width 375px must not overflow horizontally. Use semantic HTML, attractive CSS, working JavaScript, no external libraries, no localStorage dependency. Include the preview URL in your final response. Do the work now.' + DELIVERY,
  'Update that same website: change both page title and h1 to exactly "Night Garden FLEET-63c79f3de4 Revised", change the accent to amber, add a visible footer "FLEET-63c79f3de4 edited successfully". Preserve all three event cards and the working Show sold out behavior. Publish the updated preview and provide its new URL.' + DELIVERY,
  'Without changing any files or rereading files, tell me the exact unique FLEET marker and the directory of the website we just edited.' + DELIVERY,
];

const OTHER_OWNER_MESSAGES = [
  "Build and show me a website for Acme's accounting product.",
  "Implement a Python CLI that reads usage records and writes a JSON report.",
  "Install the extension from https://github.com/example-org/example-extension and tell me when it is ready.",
  "Open http://192.168.1.20:8080/status and summarize what it shows.",
  "Download https://example.com/data.bin and save it with the exact bytes.",
  "What extensions are installed right now?",
  "hello, how are you?",
];

const PIXEL = { agentId: "pixel", contextTokenBudget: 65536 };

function compose(prompt, options = {}, extra = {}) {
  const contract = promptContractForAgent(PIXEL, "pixel", { prompt, messages: [] }, options);
  return {
    contract,
    result: composePromptBuildResult(contract, {
      activity: ACTIVITY_CONTRACT,
      execution: executionContext(),
      ...extra,
    }),
  };
}

const squash = text => text.replace(/\s+/g, " ").trim();

test("the observed Mac journey selected different system text for every owner message", () => {
  const legacy = MAC_JOURNEY.map(prompt => compose(prompt, { configuredContextWindow: 65536 }).contract.appendSystemContext);
  assert.equal(new Set(legacy).size, 3);
  assert.ok(legacy[0].includes(ODS_WORKSPACE_PREVIEW_CONTRACT));
  assert.ok(legacy[1].includes(ODS_WORKSPACE_VISUAL_CONTINUATION_CONTRACT));
  assert.ok(!legacy[2].includes(ODS_WORKSPACE_PREVIEW_CONTRACT));
});

test("system context is byte-identical for every owner message of one configuration", () => {
  for (const options of [
    { configuredContextWindow: 65536 },
    { configuredContextWindow: 65536, configuredLeanPrompt: true },
    { configuredContextWindow: 16384 },
    { configuredContextWindow: 65536, executionHost: "gateway" },
    { configuredContextWindow: 65536, privateBrowserAccess: true },
  ]) {
    const systems = new Set();
    for (const prompt of [...MAC_JOURNEY, ...OTHER_OWNER_MESSAGES]) {
      for (const verificationStatus of [undefined, "pending", "failed"]) {
        systems.add(compose(prompt, { ...options, verificationStatus }).result.appendSystemContext);
      }
    }
    assert.equal(systems.size, 1, JSON.stringify(options));
  }
});

test("message-selected guidance moves to the current turn without losing any text", () => {
  for (const prompt of [...MAC_JOURNEY, ...OTHER_OWNER_MESSAGES]) {
    for (const verificationStatus of [undefined, "pending", "failed"]) {
      const { contract, result } = compose(prompt, { configuredContextWindow: 65536, verificationStatus });
      // The legacy combined text equals the stable part plus the turn part.
      assert.equal(
        squash(contract.appendSystemContext.replace(contract.systemContext, " ")),
        squash(contract.turnContext),
        prompt,
      );
      assert.ok(result.appendSystemContext.includes(contract.systemContext));
      if (contract.turnContext) {
        assert.equal(result.appendContext, contract.turnContext);
        assert.ok(!result.appendSystemContext.includes(contract.turnContext));
      } else {
        assert.equal(result.appendContext, undefined);
      }
    }
  }
  const [create, update] = MAC_JOURNEY.map(prompt => compose(prompt, { configuredContextWindow: 65536 }).result);
  assert.ok(create.appendContext.includes(ODS_WORKSPACE_PREVIEW_CONTRACT.trim()));
  assert.ok(update.appendContext.includes(ODS_WORKSPACE_VISUAL_CONTINUATION_CONTRACT.trim()));
  const pending = compose(MAC_JOURNEY[2], { configuredContextWindow: 65536, verificationStatus: "pending" }).result;
  const failed = compose(MAC_JOURNEY[2], { configuredContextWindow: 65536, verificationStatus: "failed" }).result;
  assert.ok(pending.appendContext.includes(ODS_VERIFICATION_PENDING_CONTRACT.trim()));
  assert.ok(failed.appendContext.includes(ODS_VERIFICATION_FAILED_CONTRACT.trim()));
});

test("system space holds exactly the activity, conversation and execution contracts", () => {
  const { result } = compose(MAC_JOURNEY[0], { configuredContextWindow: 65536 });
  assert.equal(
    result.appendSystemContext,
    `${ACTIVITY_CONTRACT} ${ODS_CONVERSATION_CONTRACT.trim()} ${executionContext()}`,
  );
  assert.doesNotMatch(result.appendSystemContext, /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/);
});

test("goal mode and repository evidence ride on the turn, never in system space", () => {
  const evidence = "Repository evidence: README excerpt for example-org/example-extension.";
  const plain = compose(MAC_JOURNEY[2], { configuredContextWindow: 65536 }).result;
  const goal = compose(MAC_JOURNEY[2], { configuredContextWindow: 65536 }, { goal: GOAL_CONTRACT, repositoryEvidence: evidence }).result;
  assert.equal(goal.appendSystemContext, plain.appendSystemContext);
  assert.ok(!goal.appendSystemContext.includes(GOAL_CONTRACT));
  assert.equal(goal.appendContext, `${GOAL_CONTRACT}\n\n${evidence}`);
  const both = compose(MAC_JOURNEY[0], { configuredContextWindow: 65536 }, { goal: GOAL_CONTRACT }).result;
  assert.ok(both.appendContext.startsWith(`${GOAL_CONTRACT}\n\n`));
  assert.ok(both.appendContext.includes(ODS_WORKSPACE_PREVIEW_CONTRACT.trim()));
});

test("a sticky team role keeps its whole contract in system space", () => {
  const role = "You are the Reviewer in the owner's Portal team.\nReview the Builder report.";
  const first = promptContractForAgent(PIXEL, "pixel", { prompt: role, messages: [] });
  const later = promptContractForAgent(PIXEL, "pixel", {
    prompt: "Also check the footer.",
    messages: [{ role: "user", content: role }],
  });
  assert.equal(first.turnContext, "");
  assert.equal(first.systemContext, later.systemContext);
  const result = composePromptBuildResult(first, { activity: ACTIVITY_CONTRACT, execution: executionContext() });
  assert.ok(result.appendSystemContext.includes("read-only Reviewer"));
  assert.equal(result.appendContext, undefined);
});

test("other agents and legacy contract objects are handled conservatively", () => {
  assert.equal(promptContractForAgent({ agentId: "main" }, "pixel", { prompt: "hi" }), undefined);
  assert.equal(composePromptBuildResult(undefined, { activity: ACTIVITY_CONTRACT }), undefined);
  assert.deepEqual(
    composePromptBuildResult({ appendSystemContext: "legacy" }, { activity: "A", execution: "E" }),
    { appendSystemContext: "A legacy E" },
  );
});
