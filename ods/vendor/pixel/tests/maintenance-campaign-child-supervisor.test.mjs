import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import {
  deriveCampaignChildUnitName,
} from "../deploy/work-controller/maintenance-campaign-child-launcher.mjs";
import {
  deriveInnerIdentity,
  launchBoundedChild,
  parseSupervisorArgs,
  validateCampaignChildContract,
  validateCampaignChildReceipt,
} from "../deploy/work-controller/maintenance-campaign-child-supervisor.mjs";

const execute = promisify(execFile);
const ROOT = resolve(new URL("..", import.meta.url).pathname);
const SUPERVISOR = join(ROOT, "deploy/work-controller/maintenance-campaign-child-supervisor.mjs");
const FIXTURE = join(ROOT, "tests/fixtures/campaign-child-fixture.mjs");
const digest = (c) => c.repeat(64);
const sha256 = (v) => createHash("sha256").update(v).digest("hex");
const uid = process.geteuid?.() ?? 1000;
const linux = process.platform !== "win32";

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
}

// Exact descendant identity proof: /proc/<pid>/cmdline must be the exact test
// fixture plus the expected mode (NUL-separated, with the trailing NUL). No
// substring matching. Returns true only on an exact match. The exact argv is
// [process.execPath, FIXTURE, mode, ""] (the trailing NUL is the final empty
// element); any extra or missing argv entry is rejected.
function descendantIdentityMatches(parts, mode) {
  return Array.isArray(parts)
    && parts.length === 4
    && parts[0] === process.execPath
    && parts[1] === FIXTURE
    && parts[2] === mode
    && parts[3] === "";
}

async function readCmdline(pid) {
  try { return (await readFile(`/proc/${pid}/cmdline`, "utf8")).split("\0"); } catch { return null; }
}

function processIsAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error?.code === "ESRCH") return false;
    throw error;
  }
}

// Register exact-PID cleanup immediately after reading the PID. Before any
// cleanup signal, re-verify /proc/<pid>/cmdline is the exact test fixture plus
// the expected mode; if identity is absent or changed, refuse to signal. Never
// uses pgrep/pkill, substring matching, or broad cleanup. readCmdline and kill
// are dependency-injected seams so the refusal path can be proven deterministically
// without spawning any real process.
async function cleanupExactDescendant(pid, mode, seams = {}) {
  const read = seams.readCmdline ?? readCmdline;
  const kill = seams.kill ?? ((target, signal) => process.kill(target, signal));
  const parts = await read(pid);
  if (!descendantIdentityMatches(parts, mode)) return false; // identity absent or changed; refuse
  try { kill(pid, "SIGKILL"); } catch {}
  return true;
}

function registerDescendantCleanup(t, pid, mode) {
  t.after(async () => {
    await cleanupExactDescendant(pid, mode);
  });
}

function makeInnerPython(overrides = {}) {
  return {
    path: process.execPath,
    argv: [FIXTURE, "progress"],
    cwd: ROOT,
    env: { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "/tmp" },
    timeoutMilliseconds: 20000,
    maxStdoutBytes: 1024 * 1024,
    maxStderrBytes: 1024 * 1024,
    ...overrides,
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-child-supervisor-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const nonce = digest("7");
  const contractPath = join(root, `pixel-campaign-child-${nonce}.contract.json`);
  const receiptPath = join(root, `pixel-campaign-child-${nonce}.receipt.json`);
  const resultPath = join(root, `pixel-campaign-child-${nonce}.result`);
  const diagnosticPath = join(root, `pixel-campaign-child-${nonce}.diagnostic`);
  return { root, nonce, contractPath, receiptPath, resultPath, diagnosticPath };
}

test("unit name derivation is exact, full-nonce, and never truncated", () => {
  const nonce = digest("a");
  assert.equal(deriveCampaignChildUnitName(nonce), `pixel-campaign-child-${nonce}.service`);
  assert.equal(deriveCampaignChildUnitName(nonce).length, "pixel-campaign-child-".length + 64 + ".service".length);
  assert.throws(() => deriveCampaignChildUnitName("a".repeat(63)), /nonce/);
  assert.throws(() => deriveCampaignChildUnitName(nonce.toUpperCase()), /nonce/);
  assert.throws(() => deriveCampaignChildUnitName("g".repeat(64)), /nonce/);
});

test("supervisor CLI parsing is strict and unclosed", () => {
  assert.deepEqual(parseSupervisorArgs(["run", "--nonce", digest("a"), "--contract", "/c", "--receipt", "/r"]), { nonce: digest("a"), contract: "/c", receipt: "/r" });
  assert.throws(() => parseSupervisorArgs(["run", "--nonce", digest("a"), "--contract", "/c"]), /Usage/);
  assert.throws(() => parseSupervisorArgs(["run", "--nonce", "nope", "--contract", "/c", "--receipt", "/r"]), /nonce/);
  assert.throws(() => parseSupervisorArgs(["start", "--nonce", digest("a"), "--contract", "/c", "--receipt", "/r"]), /Usage/);
});

test("contract validation is exact and rejects substitution", () => {
  const innerPython = makeInnerPython();
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: digest("a"), campaignOperationSha256: digest("b"),
    innerPython, innerIdentity: { executableSha256: digest("c"), argvSha256: digest("d"), contractSha256: digest("e") },
    expectedOwnerUid: uid,
    resultPath: "/r", diagnosticPath: "/d", receiptPath: "/p",
    supervisor: { path: "/usr/bin/node", sha256: digest("f"), unitName: `pixel-campaign-child-${digest("a")}.service` },
  };
  const validated = validateCampaignChildContract(contract, uid);
  assert.equal(validated.nonce, digest("a"));
  assert.throws(() => validateCampaignChildContract({ ...contract, nonce: digest("0") }, uid), /nonce/);
  assert.throws(() => validateCampaignChildContract({ ...contract, supervisor: { ...contract.supervisor, unitName: `pixel-campaign-child-${digest("0")}.service` } }, uid), /unit name/);
  assert.throws(() => validateCampaignChildContract({ ...contract, expectedOwnerUid: uid + 1 }, uid), /owner/);
});

test("bounded child launch captures bounded stdout and normal exit", async () => {
  const inner = makeInnerPython();
  const result = await launchBoundedChild(inner);
  assert.equal(result.exitCode, 0);
  assert.equal(result.signal, null);
  assert.equal(result.timedOut, false);
  assert.equal(result.overflow, null);
  assert.equal(result.stdoutBytes, Buffer.byteLength(JSON.stringify({ operation: "pixel-portal-outcome-battery-campaign-progress", campaignId: "outcomebattery-" + "a".repeat(24), profile: "builder", evaluationRegime: "matched-budget", partition: "tuning", runtimeCondition: "cold-first-request", status: "pass", requiredPairs: 1, completedPairs: 1, tuningBaselineFrozen: false, tuningBaselineFreezeSha256: null })));
  assert.match(result.stdoutSha256, /^[a-f0-9]{64}$/u);
});

test("bounded child launch reports exit code 2 as child-exit-2 signal path", async () => {
  const inner = makeInnerPython({ argv: [FIXTURE, "exit2"] });
  const result = await launchBoundedChild(inner);
  assert.equal(result.exitCode, 2);
  assert.equal(result.stderrBytes, Buffer.byteLength("exit-2-diagnostic"));
});

test("bounded child launch enforces stdout overflow before unbounded growth and terminates the child", async () => {
  const inner = makeInnerPython({ argv: [FIXTURE, "overflow"], maxStdoutBytes: 64 * 1024 });
  const result = await launchBoundedChild(inner);
  assert.equal(result.overflow, "stdout");
  assert.ok(result.stdoutBytes <= 64 * 1024, `bounded stdout must not exceed the bound: ${result.stdoutBytes}`);
});

test("bounded child launch enforces stderr overflow and terminates the child", async () => {
  const inner = makeInnerPython({ argv: [FIXTURE, "stderr-overflow"], maxStderrBytes: 64 * 1024 });
  const result = await launchBoundedChild(inner);
  assert.equal(result.overflow, "stderr");
  assert.ok(result.stderrBytes <= 64 * 1024, `bounded stderr must not exceed the bound: ${result.stderrBytes}`);
});

test("bounded child launch enforces the hard timeout and terminates the child", async () => {
  const inner = makeInnerPython({ argv: [FIXTURE, "timeout"], timeoutMilliseconds: 250 });
  const result = await launchBoundedChild(inner);
  assert.equal(result.timedOut, true);
});

test("superviseCampaignChild end-to-end writes owner-private receipt/result/diagnostic and exit 0", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  const { stdout } = await execute(process.execPath, [SUPERVISOR, "run", "--nonce", fx.nonce, "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT });
  const receipt = JSON.parse(stdout);
  const validated = validateCampaignChildReceipt(receipt, uid);
  assert.equal(validated.nonce, fx.nonce);
  assert.equal(validated.outcome.exitCode, 0);
  assert.equal(validated.outcome.timedOut, false);
  assert.equal(validated.outcome.outputOverflow, null);
  const resultBytes = await readFile(fx.resultPath);
  assert.equal(resultBytes.length, validated.stdout.bytes);
  assert.equal(sha256(resultBytes), validated.stdout.sha256);
  if (linux) {
    assert.equal((await lstat(fx.resultPath)).mode & 0o077, 0);
    assert.equal((await lstat(fx.receiptPath)).mode & 0o077, 0);
  }
});

test("superviseCampaignChild fails closed when the supervisor module identity is substituted", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: "/usr/bin/node", sha256: digest("f"), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  await assert.rejects(
    execute(process.execPath, [SUPERVISOR, "run", "--nonce", fx.nonce, "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT }),
    /supervisor identity is substituted/,
  );
});

test("superviseCampaignChild fails closed when the nonce does not match the contract", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  await assert.rejects(
    execute(process.execPath, [SUPERVISOR, "run", "--nonce", digest("9"), "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT }),
    /nonce does not match/,
  );
});

test("superviseCampaignChild persists a timeout outcome receipt without leaking content", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython({ argv: [FIXTURE, "timeout"], timeoutMilliseconds: 300 });
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  const { stdout } = await execute(process.execPath, [SUPERVISOR, "run", "--nonce", fx.nonce, "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT });
  const receipt = JSON.parse(stdout);
  assert.equal(receipt.outcome.timedOut, true);
  assert.equal(JSON.stringify(receipt).includes("diag"), false);
});

// ---------------------------------------------------------------------------
// M5 repair tests: P1 synchronous spawn failure, P0 bounded descendant
// termination, P1 receipt outcome coherence, P0 no-clobber supervisor publish.
// ---------------------------------------------------------------------------

test("P1 synchronous spawn throw returns a structured spawnError result, not a ReferenceError", async () => {
  const inner = makeInnerPython();
  const throwingSpawn = () => { throw new Error("spawn exploded synchronously"); };
  const result = await launchBoundedChild(inner, throwingSpawn);
  assert.equal(result.spawnError, true);
  assert.equal(result.exitCode, null);
  assert.equal(result.signal, null);
  assert.equal(result.timedOut, false);
  assert.equal(result.overflow, null);
  assert.equal(result.stdoutBytes, 0);
  assert.equal(result.stderrBytes, 0);
  assert.match(result.stdoutSha256, /^[a-f0-9]{64}$/u);
  assert.match(result.stderrSha256, /^[a-f0-9]{64}$/u);
});

test("P0 process-group SIGKILL reaps the same-group descendant (exact PID, not pgrep substring)", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-child-desc-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const pidFile = join(root, "descendant.pid");
  const inner = makeInnerPython({ argv: [FIXTURE, "fork-descendant", pidFile], timeoutMilliseconds: 400, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const started = Date.now();
  const result = await launchBoundedChild(inner, undefined, { killGraceMs: 300 });
  const elapsed = Date.now() - started;
  assert.equal(result.timedOut, true, "the forked descendant must not prevent the timeout outcome");
  assert.ok(elapsed < 4000, `supervisor must return within bound plus grace: ${elapsed}ms`);
  assert.ok(result.stdoutBytes <= 64 * 1024, `output must remain bounded: ${result.stdoutBytes}`);
  // The descendant stays in the inner child's process group, so the
  // process-group SIGKILL must reap the exact descendant PID. Register exact
  // cleanup immediately after reading the PID, before any assertion that could
  // fail, so a leaked descendant is never left behind.
  const descendantPid = Number((await readFile(pidFile, "utf8")).trim());
  assert.ok(Number.isSafeInteger(descendantPid) && descendantPid > 1, "the descendant must write its exact PID");
  registerDescendantCleanup(t, descendantPid, "descendant-hold");
  // Give the process-group SIGKILL a moment to reap the descendant.
  await new Promise((r) => setTimeout(r, 300));
  const alive = processIsAlive(descendantPid);
  assert.equal(alive, false, `the exact same-group descendant PID ${descendantPid} must be gone`);
});

test("P0 adversarial: a detached descendant that calls setsid cannot be killed by the supervisor alone (cgroup backstop)", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-child-detached-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const pidFile = join(root, "detached.pid");
  const inner = makeInnerPython({ argv: [FIXTURE, "detached-descendant", pidFile], timeoutMilliseconds: 1500, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const started = Date.now();
  const result = await launchBoundedChild(inner, undefined, { killGraceMs: 300 });
  const elapsed = Date.now() - started;
  assert.equal(result.timedOut, true, "the supervisor must still resolve its own bounded timeout");
  assert.ok(elapsed < 4000, `supervisor must return within bound plus grace: ${elapsed}ms`);
  const detachedPid = Number((await readFile(pidFile, "utf8")).trim());
  assert.ok(Number.isSafeInteger(detachedPid) && detachedPid > 1, "the detached descendant must write its exact PID");
  // Register exact cleanup immediately after reading the PID, before any
  // assertion that could fail, so a leaked detached descendant is never left
  // behind.
  registerDescendantCleanup(t, detachedPid, "detached-hold");
  // Give the process-group SIGKILL a moment; the detached descendant survives
  // because it called setsid and left the inner child's process group.
  await new Promise((r) => setTimeout(r, 300));
  const alive = processIsAlive(detachedPid);
  assert.equal(alive, true, "the supervisor alone cannot kill a detached setsid descendant; the cgroup backstop is required");
});

test("P1 receipt validation rejects contradictory success with a signal", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  const receipt = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-receipt", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerIdentity: { executableSha256: digest("1"), argvSha256: digest("2"), contractSha256: digest("3") },
    outcome: { exitCode: 0, signal: "SIGKILL", timedOut: false, outputOverflow: null, spawnError: false },
    stdout: { bytes: 0, sha256: sha256(Buffer.alloc(0)) },
    stderr: { bytes: 0, sha256: sha256(Buffer.alloc(0)) },
    terminalTime: "2026-08-20T19:00:00.000Z",
  };
  assert.throws(() => validateCampaignChildReceipt(receipt, uid), /contradictory/);
});

test("P1 receipt validation rejects a timeout that also claims an exit code", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const receipt = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-receipt", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerIdentity: { executableSha256: digest("1"), argvSha256: digest("2"), contractSha256: digest("3") },
    outcome: { exitCode: 1, signal: null, timedOut: true, outputOverflow: null, spawnError: false },
    stdout: { bytes: 0, sha256: sha256(Buffer.alloc(0)) },
    stderr: { bytes: 0, sha256: sha256(Buffer.alloc(0)) },
    terminalTime: "2026-08-20T19:00:00.000Z",
  };
  assert.throws(() => validateCampaignChildReceipt(receipt, uid), /contradictory/);
});

test("P0 supervisor no-clobber publish never replaces an existing receipt target", async (t) => {
  const fx = await fixture(t);
  const innerPython = makeInnerPython();
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  // Pre-create the receipt target as an attacker regular file.
  await privateWrite(fx.receiptPath, { attacker: true });
  const before = await readFile(fx.receiptPath, "utf8");
  await assert.rejects(
    execute(process.execPath, [SUPERVISOR, "run", "--nonce", fx.nonce, "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT }),
    /already exists|not an owner-private|EEXIST/,
  );
  const after = await readFile(fx.receiptPath, "utf8");
  assert.equal(after, before, "the preexisting receipt target must remain unchanged");
});

// ---------------------------------------------------------------------------
// P0: forced timeout/overflow outcomes are canonical (exitCode=null,
// signal=null, exactly one of timedOut/outputOverflow, spawnError=false) and
// validated before publication. Test the real timeout and both overflow paths
// through superviseCampaignChild and validate the actual published receipts.
// ---------------------------------------------------------------------------

async function runSupervised(fx, innerPython) {
  const innerIdentity = await deriveInnerIdentity(innerPython);
  const supervisorBytes = await readFile(SUPERVISOR);
  const contract = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-contract", operation: "pixel-work-model-campaign-maintenance",
    nonce: fx.nonce, campaignOperationSha256: digest("b"),
    innerPython, innerIdentity, expectedOwnerUid: uid,
    resultPath: fx.resultPath, diagnosticPath: fx.diagnosticPath, receiptPath: fx.receiptPath,
    supervisor: { path: resolve(SUPERVISOR), sha256: sha256(supervisorBytes), unitName: `pixel-campaign-child-${fx.nonce}.service` },
  };
  await privateWrite(fx.contractPath, contract);
  const { stdout } = await execute(process.execPath, [SUPERVISOR, "run", "--nonce", fx.nonce, "--contract", fx.contractPath, "--receipt", fx.receiptPath], { cwd: ROOT });
  const receipt = JSON.parse(stdout);
  // The published receipt must itself validate (canonical and coherent).
  const validated = validateCampaignChildReceipt(receipt, uid);
  // The on-disk receipt must equal the stdout receipt and validate too.
  const onDisk = JSON.parse(await readFile(fx.receiptPath, "utf8"));
  assert.deepEqual(onDisk, receipt, "published receipt must match the stdout receipt");
  validateCampaignChildReceipt(onDisk, uid);
  return validated;
}

test("P0 real timeout through superviseCampaignChild publishes a canonical validated receipt", async (t) => {
  const fx = await fixture(t);
  const receipt = await runSupervised(fx, makeInnerPython({ argv: [FIXTURE, "timeout"], timeoutMilliseconds: 300, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 }));
  assert.equal(receipt.outcome.exitCode, null);
  assert.equal(receipt.outcome.signal, null);
  assert.equal(receipt.outcome.timedOut, true);
  assert.equal(receipt.outcome.outputOverflow, null);
  assert.equal(receipt.outcome.spawnError, false);
});

test("P0 real stdout overflow through superviseCampaignChild publishes a canonical validated receipt", async (t) => {
  const fx = await fixture(t);
  const receipt = await runSupervised(fx, makeInnerPython({ argv: [FIXTURE, "overflow"], timeoutMilliseconds: 5000, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 }));
  assert.equal(receipt.outcome.exitCode, null);
  assert.equal(receipt.outcome.signal, null);
  assert.equal(receipt.outcome.timedOut, false);
  assert.equal(receipt.outcome.outputOverflow, "stdout");
  assert.equal(receipt.outcome.spawnError, false);
  assert.ok(receipt.stdout.bytes <= 64 * 1024, "stdout bytes must remain bounded");
});

test("P0 real stderr overflow through superviseCampaignChild publishes a canonical validated receipt", async (t) => {
  const fx = await fixture(t);
  const receipt = await runSupervised(fx, makeInnerPython({ argv: [FIXTURE, "stderr-overflow"], timeoutMilliseconds: 5000, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 }));
  assert.equal(receipt.outcome.exitCode, null);
  assert.equal(receipt.outcome.signal, null);
  assert.equal(receipt.outcome.timedOut, false);
  assert.equal(receipt.outcome.outputOverflow, "stderr");
  assert.equal(receipt.outcome.spawnError, false);
  assert.ok(receipt.stderr.bytes <= 64 * 1024, "stderr bytes must remain bounded");
});

// ---------------------------------------------------------------------------
// P0: grace resolves independently of close. A fake child whose close event
// never fires and whose pipe destroy does not synthesize it must still resolve
// within timeout+grace with the canonical first-cause forced outcome.
// ---------------------------------------------------------------------------
test("P0 grace resolves the forced outcome directly when close never arrives", async () => {
  const { EventEmitter } = await import("node:events");
  const inner = makeInnerPython({ timeoutMilliseconds: 120, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.pid = 0; // no positive PID: must never signal a real process group
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    child.destroy = () => {};
    // Deliberately never emit 'close' or 'error'; pipe destroy must not
    // synthesize a close either (the fake pipes are inert EventEmitters).
    return child;
  };
  const started = Date.now();
  const result = await launchBoundedChild(inner, fakeSpawn, { killGraceMs: 120 });
  const elapsed = Date.now() - started;
  assert.equal(result.timedOut, true, "the timeout must be the first-cause forced outcome");
  assert.equal(result.exitCode, null);
  assert.equal(result.signal, null);
  assert.equal(result.overflow, null);
  assert.equal(result.spawnError, false);
  assert.ok(elapsed >= 120 && elapsed < 1200, `must resolve within timeout+grace: ${elapsed}ms`);
});

// ---------------------------------------------------------------------------
// P0: true first-cause forced outcome. An output overflow that happens before
// the wall-clock timeout must remain the canonical cause even if the timeout
// fires before close or the grace settlement. The timeout callback must never
// overwrite an existing forced cause. Each case settles directly by grace with
// exact canonical fields and proves the first cause is retained. The fake child
// uses no positive PID (0) so no real process group can ever be signalled.
// ---------------------------------------------------------------------------
test("P0 stdout overflow first, then timeout, no close: overflow is the canonical first cause", async () => {
  const { EventEmitter } = await import("node:events");
  const inner = makeInnerPython({ timeoutMilliseconds: 120, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.pid = 0;
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    child.destroy = () => {};
    // Overflow stdout first, then the wall-clock timeout fires later; never close.
    setImmediate(() => {
      child.stdout.emit("data", Buffer.alloc(128 * 1024, 0x41));
    });
    // Simulate the timeout firing after the overflow has already set the force.
    setTimeout(() => {}, 40);
    return child;
  };
  const started = Date.now();
  const result = await launchBoundedChild(inner, fakeSpawn, { killGraceMs: 120 });
  const elapsed = Date.now() - started;
  assert.equal(result.overflow, "stdout", "stdout overflow must remain the canonical first cause");
  assert.equal(result.timedOut, false, "a later timeout must not overwrite the overflow cause");
  assert.equal(result.exitCode, null);
  assert.equal(result.signal, null);
  assert.equal(result.spawnError, false);
  // Date.now() can quantize a monotonic timer deadline just below its nominal
  // millisecond value. Preserve a meaningful lower bound without making the
  // safety test depend on one millisecond of wall-clock precision.
  assert.ok(elapsed >= 110 && elapsed < 1200, `must settle by grace: ${elapsed}ms`);
});

test("P0 stderr overflow first, then timeout, no close: stderr overflow is the canonical first cause", async () => {
  const { EventEmitter } = await import("node:events");
  const inner = makeInnerPython({ timeoutMilliseconds: 120, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.pid = 0;
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    child.destroy = () => {};
    setImmediate(() => {
      child.stderr.emit("data", Buffer.alloc(128 * 1024, 0x42));
    });
    // The wall-clock timeout fires later, after the overflow has set the force.
    setTimeout(() => {}, 40);
    return child;
  };
  const started = Date.now();
  const result = await launchBoundedChild(inner, fakeSpawn, { killGraceMs: 120 });
  const elapsed = Date.now() - started;
  assert.equal(result.overflow, "stderr", "stderr overflow must remain the canonical first cause");
  assert.equal(result.timedOut, false, "a later timeout must not overwrite the overflow cause");
  assert.equal(result.exitCode, null);
  assert.equal(result.signal, null);
  assert.equal(result.spawnError, false);
  assert.ok(elapsed >= 110 && elapsed < 1200, `must settle by grace: ${elapsed}ms`);
});

test("P0 timeout first, then later stream data, no close: timeout is the canonical first cause", async () => {
  const { EventEmitter } = await import("node:events");
  const inner = makeInnerPython({ timeoutMilliseconds: 120, maxStdoutBytes: 64 * 1024, maxStderrBytes: 64 * 1024 });
  const fakeSpawn = () => {
    const child = new EventEmitter();
    child.pid = 0;
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.kill = () => {};
    child.destroy = () => {};
    // The timeout fires first (at 120ms); later stream data arrives before the
    // grace deadline but must not change the canonical timeout cause.
    setTimeout(() => {
      child.stdout.emit("data", Buffer.alloc(1024, 0x43));
    }, 140);
    return child;
  };
  const started = Date.now();
  const result = await launchBoundedChild(inner, fakeSpawn, { killGraceMs: 120 });
  const elapsed = Date.now() - started;
  assert.equal(result.timedOut, true, "the timeout must be the canonical first cause");
  assert.equal(result.overflow, null, "later stream data must not create an overflow cause");
  assert.equal(result.exitCode, null);
  assert.equal(result.signal, null);
  assert.equal(result.spawnError, false);
  assert.ok(elapsed >= 110 && elapsed < 1200, `must settle by grace: ${elapsed}ms`);
});

// ---------------------------------------------------------------------------
// P1: identity-mismatched cleanup refuses to signal. The exact-PID cleanup
// helper must refuse to signal when /proc/<pid>/cmdline is absent or changed.
// ---------------------------------------------------------------------------
test("P1 identity-mismatched cleanup refuses to signal (deterministic, no process)", async () => {
  // A PID whose cmdline is absent must be refused without any signal.
  let killed = 0;
  const absent = await cleanupExactDescendant(2147483647, "descendant-hold", {
    readCmdline: async () => null,
    kill: () => { killed += 1; },
  });
  assert.equal(absent, false, "cleanup must refuse to signal an absent identity");
  assert.equal(killed, 0, "cleanup must never signal an absent identity");
  // A PID whose cmdline does not match the exact fixture+mode must be refused.
  const mismatched = await cleanupExactDescendant(4242, "descendant-hold", {
    readCmdline: async () => [process.execPath, "-e", "setInterval(()=>{},1000)", ""],
    kill: () => { killed += 1; },
  });
  assert.equal(mismatched, false, "cleanup must refuse to signal a mismatched identity");
  assert.equal(killed, 0, "cleanup must never signal a mismatched identity");
  // A cmdline with extra argv entries must also be refused (exact-array proof).
  const extra = await cleanupExactDescendant(4243, "descendant-hold", {
    readCmdline: async () => [process.execPath, FIXTURE, "descendant-hold", "", "extra"],
    kill: () => { killed += 1; },
  });
  assert.equal(extra, false, "cleanup must refuse to signal a cmdline with extra argv");
  assert.equal(killed, 0, "cleanup must never signal a cmdline with extra argv");
  // A cmdline missing the trailing NUL element must be refused.
  const missingNul = await cleanupExactDescendant(4244, "descendant-hold", {
    readCmdline: async () => [process.execPath, FIXTURE, "descendant-hold"],
    kill: () => { killed += 1; },
  });
  assert.equal(missingNul, false, "cleanup must refuse to signal a cmdline missing the trailing NUL");
  assert.equal(killed, 0, "cleanup must never signal a cmdline missing the trailing NUL");
});

test("P1 exact-identity cleanup signals only after the exact argv proof", async () => {
  let killed = 0;
  const exact = await cleanupExactDescendant(4245, "descendant-hold", {
    readCmdline: async () => [process.execPath, FIXTURE, "descendant-hold", ""],
    kill: () => { killed += 1; },
  });
  assert.equal(exact, true, "cleanup must signal when the exact argv matches");
  assert.equal(killed, 1, "cleanup must signal exactly once on an exact match");
});

test("P1 exact-array identity proof rejects extra argv entries", () => {
  const exact = [process.execPath, FIXTURE, "descendant-hold", ""];
  assert.equal(descendantIdentityMatches(exact, "descendant-hold"), true);
  assert.equal(descendantIdentityMatches([...exact, "extra"], "descendant-hold"), false, "extra argv must be rejected");
  assert.equal(descendantIdentityMatches(exact.slice(0, 3), "descendant-hold"), false, "missing trailing NUL must be rejected");
  assert.equal(descendantIdentityMatches([process.execPath, FIXTURE, "other-mode", ""], "descendant-hold"), false, "wrong mode must be rejected");
  assert.equal(descendantIdentityMatches(null, "descendant-hold"), false, "absent cmdline must be rejected");
});
