// Fault-inject the actual installed command's post-turn persistence block.
// Run explicitly with OPENCLAW_AGENT_COMMAND_MODULE pointing to original or
// candidate package bytes; no agent, network call or live config is executed.
import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const manifest = JSON.parse(readFileSync(new URL("../host/openclaw-completion-recovery.json", import.meta.url)));
const source = readFileSync(process.env.OPENCLAW_AGENT_COMMAND_MODULE, "utf8");
assert.ok([manifest.sourceSha256, manifest.patchedSha256].includes(
  createHash("sha256").update(source).digest("hex")
));
const start = "\t\t\tconst transcriptPersistenceRunner = result.meta.executionTrace?.runner;";
const end = "\t\t\tconst payloads = result.payloads ?? [];";
assert.equal(source.split(start).length, 2);
assert.equal(source.split(end).length, 2);
const block = source.slice(source.indexOf(start), source.indexOf(end));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction("result", "context",
  "let {sessionEntry, sessionReboundDuringRun} = context;\n" +
  "const {suppressVisibleSessionEffects, sessionStore, sessionKey, sessionId," +
  "effectiveSessionId, effectiveSessionFile, storePath, sessionAgentId, effectiveCwd," +
  "cfg, opts, prepared, body, transcriptBody, attemptExecutionRuntime, log," +
  "loadCliCompactionRuntime, workspaceDir, agentDir, provider, model, skillsSnapshot," +
  "messageChannel, runContext, resolvedThinkLevel} = context;\n" +
  block + "\nreturn {payloads: result.payloads, sessionEntry};"
);

function fixture(runner, options = {}) {
  const calls = [];
  const result = {
    meta: {executionTrace: {runner}, finalAssistantVisibleText: "Completed and verified."},
    payloads: [{text: "Completed and verified.", mediaUrl: "retained-artifact"}],
  };
  const context = {
    sessionEntry: {sessionId: "test-session"}, sessionReboundDuringRun: false,
    suppressVisibleSessionEffects: false, sessionId: "test-session",
    effectiveSessionId: "test-session", sessionKey: "agent:pixel:test",
    cfg: {}, opts: {}, runContext: {},
    attemptExecutionRuntime: {
      async persistCliTurnTranscript(input) {
        calls.push({kind: "persist", gapFill: input.embeddedAssistantGapFill});
        if (options.persistenceFails) throw new Error("persistence unavailable");
        return {kind: "persisted", sessionEntry: {sessionId: "test-session", persisted: true}};
      },
    },
    log: {warn() { calls.push({kind: "warning"}); }},
    async loadCliCompactionRuntime() {
      calls.push({kind: "load-cli-compactor"});
      return {async runCliTurnCompactionLifecycle() {
        if (options.compactionFails !== false) throw new Error("Compaction timed out");
        return {sessionId: "test-session", compacted: true};
      }};
    },
  };
  return {result, context, calls};
}

for (const runner of ["embedded", undefined]) {
  test("completed embedded payload survives unavailable CLI compaction: " + runner, async () => {
    const f = fixture(runner);
    const actual = await execute(f.result, f.context);
    assert.deepEqual(actual.payloads, f.result.payloads);
    assert.equal(actual.sessionEntry.persisted, true);
    assert.deepEqual(f.calls, [{kind: "persist", gapFill: true}]);
  });
}
test("actual CLI runner retains compaction and updates session state", async () => {
  const f = fixture("cli", {compactionFails: false});
  const actual = await execute(f.result, f.context);
  assert.equal(actual.sessionEntry.compacted, true);
  assert.deepEqual(actual.payloads, f.result.payloads);
  assert.deepEqual(f.calls, [{kind: "persist", gapFill: false}, {kind: "load-cli-compactor"}]);
});
test("CLI compaction failure semantics remain unchanged", async () => {
  const f = fixture("cli");
  await assert.rejects(execute(f.result, f.context), /Compaction timed out/);
});
test("persistence failure still retains the completed payload without compacting", async () => {
  const f = fixture("embedded", {persistenceFails: true});
  const actual = await execute(f.result, f.context);
  assert.deepEqual(actual.payloads, f.result.payloads);
  assert.deepEqual(f.calls, [{kind: "persist", gapFill: true}, {kind: "warning"}]);
});
