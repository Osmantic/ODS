import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  runVerifierCommand, validateVerifierCommandCheck, VerifierCommandError,
} from "../deploy/work-runner/verifier-command.mjs";
import { verifierSupervisorInternals } from "../deploy/work-runner/verifier-supervisor.mjs";

const digest = (character) => character.repeat(64);

test("patch-integrity verification rejects an unchanged Builder candidate", () => {
  assert.equal(verifierSupervisorInternals.patchIntegrityStatus({ changes: 0 }), "fail");
  assert.equal(verifierSupervisorInternals.patchIntegrityStatus({ changes: 1 }), "pass");
  assert.throws(() => verifierSupervisorInternals.patchIntegrityStatus({ changes: -1 }), /change count is invalid/u);
});

function check(overrides = {}) {
  return {
    id: "fixed-test", kind: "command", criterionIndexes: [0], workingDirectory: "source",
    argv: ["/bin/sh", "-c", "exit 0"], timeoutSeconds: 2, maxOutputBytes: 1024,
    ...overrides,
  };
}

test("verifier command contract rejects shell interpolation surfaces and malformed mappings", () => {
  assert.equal(validateVerifierCommandCheck(check()), true);
  for (const mutate of [
    (value) => { value.argv[0] = "sh"; },
    (value) => { value.argv.push("line\nbreak"); },
    (value) => { value.workingDirectory = "../outside"; },
    (value) => { value.criterionIndexes = [0, 0]; },
    (value) => { value.extra = true; },
  ]) {
    const hostile = structuredClone(check());
    mutate(hostile);
    assert.throws(() => validateVerifierCommandCheck(hostile), VerifierCommandError);
  }
});

test("verifier emits content-free pass/fail evidence and enforces output and time ceilings", { skip: process.platform === "win32" }, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-verifier-command-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = join(root, "source");
  await mkdir(source, { mode: 0o700 });
  await chmod(root, 0o700);
  await writeFile(join(source, "fixture.txt"), "PRIVATE_VERIFIER_CANARY\n", { mode: 0o600 });
  const options = { workspaceRoot: root, planSha256: digest("1"), candidateSha256: digest("2") };

  const passed = await runVerifierCommand(check({ argv: ["/bin/sh", "-c", "test -f fixture.txt"] }), options);
  assert.equal(passed.status, "pass");
  assert.equal(passed.exitCode, 0);
  assert.doesNotMatch(JSON.stringify(passed), /PRIVATE_VERIFIER_CANARY|fixture\.txt/);

  const failed = await runVerifierCommand(check({ argv: ["/bin/sh", "-c", "printf PRIVATE_VERIFIER_CANARY >&2; exit 7"] }), options);
  assert.equal(failed.status, "fail");
  assert.equal(failed.exitCode, 7);
  assert.equal(failed.stderr.bytes, Buffer.byteLength("PRIVATE_VERIFIER_CANARY"));
  assert.doesNotMatch(JSON.stringify(failed), /PRIVATE_VERIFIER_CANARY/);

  const limited = await runVerifierCommand(check({ argv: ["/bin/sh", "-c", "i=0; while [ $i -lt 256 ]; do printf x; i=$((i+1)); done"], maxOutputBytes: 32 }), options);
  assert.equal(limited.status, "fail");
  assert.equal(limited.outputLimitExceeded, true);

  const timed = await runVerifierCommand(check({ argv: ["/bin/sh", "-c", "sleep 5"], timeoutSeconds: 1 }), options);
  assert.equal(timed.status, "fail");
  assert.equal(timed.timedOut, true);
});
