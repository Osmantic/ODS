import test from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  createToolLoopGuard,
  RECURSIVE_DELETE_GUIDED_REASON,
  RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
} from "../plugin/tool-loop-guard.mjs";
import { recursiveDeleteAlternate, recursiveDeleteStaysInProject } from "../plugin/recursive-delete-scope.mjs";

const GUIDED = { block: true, blockReason: RECURSIVE_DELETE_GUIDED_REASON };
const TERMINAL = { block: true, blockReason: RECURSIVE_DELETE_REQUIRES_OWNER_REASON };
const PROJECT = "fleet-qualification-9e639aea7751-coding-v2";

// strixy round-076 coding follow-up: the owner prompt, the model's blocked
// command and its non-destructive retry, verbatim from the Pixel session.
const ROUND_076_PROMPT = "FOLLOWUP CODE TASK fleet-qualification-9e639aea7751-coding-v2: Edit the same three-file CSV expense report project. Add optional CLI flag --minimum-total VALUE, accepted before or after INPUT.csv; VALUE uses the same nonnegative finite ordinary decimal syntax (at most two fractional digits, no exponent). Filter AFTER aggregating so only category totals >= VALUE appear; the boundary is inclusive. Default behavior unchanged. Invalid threshold must exit 2 with concise stderr and no stdout; still reject negative amounts and malformed input even when those rows would have been filtered out. Add actual regression tests for threshold boundary, aggregation before filtering, decimal 0.10+0.20, and invalid/negative data; execute python3 -m unittest -v again. Regenerate public/sources.json and all three raw .py.txt copies from the exact executed files, overwrite test-results.txt with real fresh test output, and publish a new verified preview snapshot. Actually edit and run the code; preserve the exact CLI/output contract.";
const REGENERATE = `python3 -c "
import json, os

sources = {}
for fname in ['report.py', 'totals.py', 'test_totals.py']:
    path = os.path.join('.', fname)
    with open(path, 'r', encoding='utf-8') as f:
        sources[fname] = f.read()

for fname, text in sources.items():
    with open(os.path.join('public', fname + '.txt'), 'w', encoding='utf-8') as f:
        f.write(text)

with open(os.path.join('public', 'sources.json'), 'w', encoding='utf-8') as f:
    json.dump(sources, f, indent=2, ensure_ascii=False)

with open('test-results.txt', 'r', encoding='utf-8') as f:
    content = f.read()
with open(os.path.join('public', 'test-results.txt'), 'w', encoding='utf-8') as f:
    f.write(content)

print('Done')
"
`;
const ROUND_076_DELETE = {
  command: `cd /workspace/${PROJECT} && rm -rf public && mkdir public && ${REGENERATE}`,
  workdir: `/workspace/${PROJECT}`,
};
const ROUND_076_RETRY = {
  command: `cd /workspace/${PROJECT} && ls public/ 2>/dev/null; echo "---"; mkdir -p public && ${REGENERATE}`,
  workdir: `/workspace/${PROJECT}`,
};

function workspace(t) {
  const root = mkdtempSync(path.join(tmpdir(), "pixel-rmrf-"));
  const outside = mkdtempSync(path.join(tmpdir(), "pixel-rmrf-outside-"));
  t.after(() => {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  });
  for (const directory of [`${PROJECT}/public`, "site/build/lib", "site/dist", "site/node_modules/pkg", "site/.git",
    "Playground/game/dist", ".openclaw/sandbox-skills"]) {
    mkdirSync(path.join(root, directory), { recursive: true });
  }
  mkdirSync(path.join(outside, "data"));
  symlinkSync(outside, path.join(root, "site", "escape"));
  symlinkSync(outside, path.join(root, "linked"));
  return root;
}

function deletionGuard(root, prompt = "In the existing /workspace/site project, refresh the build output and rerun its tests.") {
  const aborted = [], signalled = [], prepared = [];
  let calls = 0;
  const guard = createToolLoopGuard({
    abortRun: (sessionId) => { aborted.push(sessionId); return true; },
    execControl: {
      signal: (runId) => { signalled.push(runId); return true; },
      prepare: (_runId, command) => { prepared.push(command); return command; },
    },
  });
  guard.observeRun({ agentId: "pixel", runId: "run-1", sessionId: "session-1" }, "pixel",
    { prompt }, { executionHost: "sandbox", workspaceRoot: root });
  const call = (toolName, params) => {
    calls += 1;
    return guard.beforeToolCall({ toolName, runId: "run-1", params, toolCallId: `call-${calls}` },
      { agentId: "pixel", toolName, runId: "run-1", sessionId: "session-1", toolCallId: `call-${calls}` }, "pixel");
  };
  return { guard, call, aborted, signalled, prepared };
}

test("strixy round-076: an in-project rm -rf is refused once and the coding turn continues", (t) => {
  const root = workspace(t);
  const { guard, call, aborted, signalled, prepared } = deletionGuard(root, ROUND_076_PROMPT);
  assert.deepEqual(call("exec", ROUND_076_DELETE), GUIDED);
  assert.deepEqual(prepared, [], "nothing in the refused command runs");
  // The model's actual next command overwrote public/ in place. It now runs.
  assert.notEqual(call("exec", ROUND_076_RETRY)?.block, true);
  assert.deepEqual(prepared, [ROUND_076_RETRY.command]);
  assert.notEqual(call("read", { path: `${PROJECT}/report.py` })?.block, true);
  assert.notEqual(call("write", { path: `${PROJECT}/test-results.txt`, content: "ok\n" })?.block, true);
  assert.deepEqual(aborted, []);
  assert.deepEqual(signalled, []);
  assert.doesNotMatch(JSON.stringify(guard.deliveryVerificationForRun("run-1")), /recursive deletion/);
});

test("deletions inside one project directory get the recoverable refusal", async (t) => {
  const root = workspace(t);
  for (const [tool, params] of [
    ["exec", { command: "rm -rf build/", workdir: "/workspace/site" }],
    ["exec", { command: "rm -rf ./dist", workdir: "/workspace/site" }],
    ["exec", { command: "rm -rf build dist node_modules && npm run build", workdir: "site" }],
    ["exec", { command: "cd /workspace/site && rm -rf build dist node_modules" }],
    ["exec", { command: "rm -rf /workspace/site/node_modules" }],
    ["exec", { command: "rm -rf build/* 2>/dev/null || true", workdir: "/workspace/site" }],
    ["exec", { command: "rm -fr site/build/lib site/missing" }],
    ["exec", { command: "rm -rf __pycache__ .pytest_cache", workdir: "/workspace/site" }],
    ["exec", { command: "rm -rf 'build'", workdir: "/workspace/site" }],
    ["exec", { command: `cd ${root}/site && rm -rf build` }],
    ["exec", { command: "rm -rf /workspace/Playground/game/dist" }],
    ["tool_call", { id: "openclaw:core:exec", args: { command: "rm -rf dist", workdir: "/workspace/site" } }],
  ]) {
    await t.test(JSON.stringify(params), () => {
      const { call, aborted, prepared } = deletionGuard(root);
      assert.deepEqual(call(tool, params), GUIDED);
      assert.deepEqual(prepared, []);
      assert.notEqual(call("exec", { command: "mkdir -p build && ls build", workdir: "/workspace/site" })?.block, true);
      assert.notEqual(call("read", { path: "site/index.html" })?.block, true);
      assert.deepEqual(aborted, []);
    });
  }
});

test("deletions of the workspace, a whole project, or anything outside still stop the turn", async (t) => {
  const root = workspace(t);
  for (const params of [
    { command: "rm -rf /workspace" },
    { command: "rm -rf .", workdir: "/workspace" },
    { command: "rm -rf *", workdir: "/workspace/site" },
    { command: "rm -rf /workspace/*" },
    // A whole project directory: the qualification incident behind this guard.
    { command: "rm -rf site", workdir: "/workspace" },
    { command: "rm -rf site" },
    { command: "rm -rf /workspace/site/" },
    { command: `rm -rf ${root}/site` },
    { command: "rm -rf /workspace/Playground/game" },
    { command: "rm -rf /workspace/Playground/*" },
    { command: "rm -rf ~", workdir: "/workspace/site" },
    { command: "rm -rf ~/projects", workdir: "/workspace/site" },
    { command: "rm -rf /", workdir: "/workspace/site" },
    { command: "rm -rf /etc", workdir: "/workspace/site" },
    { command: "rm -rf ..", workdir: "/workspace/site/build" },
    { command: "rm -rf ../dist", workdir: "/workspace/site/build" },
    { command: "rm -rf ../../*", workdir: "/workspace/site/build" },
    { command: "cd .. && rm -rf dist", workdir: "/workspace/site/build" },
    // Symlinks that leave the workspace, and protected directories.
    { command: "rm -rf escape/data", workdir: "/workspace/site" },
    { command: "rm -rf escape", workdir: "/workspace/site" },
    { command: "rm -rf /workspace/linked/data" },
    { command: "rm -rf .git", workdir: "/workspace/site" },
    { command: "rm -rf /workspace/.openclaw/sandbox-skills" },
    // Directory changes the parser cannot pin to the project.
    { command: "cd / && rm -rf etc", workdir: "/workspace/site" },
    { command: "cd /workspace/site/build; rm -rf lib" },
    { command: "false || cd /workspace/site && rm -rf build" },
    { command: "(cd /; rm -rf etc)", workdir: "/workspace/site" },
    { command: "CDPATH=/ cd etc && rm -rf x", workdir: "/workspace/site" },
    // Expansions, nested shells, wrappers and a second dangerous target.
    { command: "rm -rf $DIR/build", workdir: "/workspace/site" },
    { command: 'rm -rf "$(pwd)"', workdir: "/workspace/site" },
    { command: 'bash -c "cd /; rm -rf etc"', workdir: "/workspace/site" },
    { command: "rm -rf build; sudo rm -rf /", workdir: "/workspace/site" },
    { command: "rm -rf build && rm -rf /workspace/site", workdir: "/workspace/site" },
    { command: "rm -rf build && rm -r /", workdir: "/workspace/site" },
    { command: "rm -rf build && python3 -c \"import os; os.system('rm -rf /')\"", workdir: "/workspace/site" },
  ]) {
    await t.test(JSON.stringify(params), () => {
      const { call, aborted, signalled, prepared } = deletionGuard(root);
      assert.deepEqual(call("exec", params), TERMINAL);
      // The next tool is the abort boundary, exactly as before this change.
      assert.deepEqual(call("read", { path: "site/index.html" }), TERMINAL);
      assert.deepEqual(prepared, []);
      assert.deepEqual(signalled, ["run-1"]);
      assert.deepEqual(aborted, ["session-1"]);
    });
  }
});

test("a second recursive deletion or a substitute after the guidance stops the turn", async (t) => {
  const root = workspace(t);
  for (const [tool, params] of [
    ["exec", { command: "rm -rf build", workdir: "/workspace/site" }],
    ["exec", { command: "rm -rf dist", workdir: "/workspace/site" }],
    ["exec", { command: "rm -r build", workdir: "/workspace/site" }],
    ["exec", { command: "find build -delete", workdir: "/workspace/site" }],
    ["exec", { command: "python3 -c \"import shutil; shutil.rmtree('build')\"", workdir: "/workspace/site" }],
    ["exec", { command: "node -e \"require('fs').rmSync('build', {recursive: true})\"", workdir: "/workspace/site" }],
    ["tool_call", { id: "openclaw:core:exec", args: { command: "sh -c 'rm -r build'", workdir: "/workspace/site" } }],
  ]) {
    await t.test(JSON.stringify(params), () => {
      const { guard, call, aborted, prepared } = deletionGuard(root);
      assert.deepEqual(call("exec", { command: "rm -rf build", workdir: "/workspace/site" }), GUIDED);
      assert.deepEqual(call(tool, params), TERMINAL);
      assert.deepEqual(call("write", { path: "site/index.html", content: "" }), TERMINAL);
      assert.deepEqual(prepared, []);
      assert.deepEqual(aborted, ["session-1"]);
      assert.deepEqual(guard.deliveryVerificationForRun("run-1"), {
        status: "failed", text: RECURSIVE_DELETE_REQUIRES_OWNER_REASON,
      });
    });
  }
});

test("without a verifiable workspace root every unauthorized recursive deletion stays terminal", () => {
  const guard = createToolLoopGuard();
  guard.observeRun({ agentId: "pixel", runId: "run-1", sessionId: "session-1" }, "pixel",
    { prompt: "Update the site build output." });
  const params = { command: "rm -rf build", workdir: "/workspace/site" };
  assert.deepEqual(guard.beforeToolCall({ toolName: "exec", runId: "run-1", params },
    { agentId: "pixel", toolName: "exec", runId: "run-1", sessionId: "session-1" }, "pixel"), TERMINAL);
  assert.equal(recursiveDeleteStaysInProject(params, path.join(tmpdir(), "pixel-rmrf-missing-root")), false);
});

test("owner-authorized recursive deletion is unchanged", (t) => {
  const root = workspace(t);
  const { call, prepared } = deletionGuard(root, "Delete the directory /workspace/site/build recursively.");
  const params = { command: "rm -rf /workspace/site/build", workdir: "/workspace/site" };
  assert.notEqual(call("exec", params)?.block, true);
  assert.deepEqual(prepared, [params.command]);
});

test("ordinary commands after the guidance are not treated as substitutes", () => {
  for (const command of [
    "mkdir -p public && python3 build.py",
    "rm -f public/old.txt",
    "node -e \"require('fs').mkdirSync('public', {recursive: true})\"",
    ROUND_076_RETRY.command,
    "python3 -m unittest -v",
  ]) assert.equal(recursiveDeleteAlternate({ command }), false, command);
});
