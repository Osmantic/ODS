import assert from "node:assert/strict";
import { once } from "node:events";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { publishGoalOperatorStatus } from "../deploy/work-controller/goal-operator-status.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";

const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const digest = (character) => character.repeat(64);

async function privateJson(path, value) {
  await writeFile(path, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function unusedPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

function request(port, path, { method = "GET", cookie = null, value = null, origin = null } = {}) {
  const body = value === null ? null : Buffer.from(JSON.stringify(value), "utf8");
  const headers = { Host: `127.0.0.1:${port}`, Connection: "close" };
  if (cookie !== null) headers.Cookie = cookie;
  if (body !== null) {
    headers["Content-Type"] = "application/json";
    headers["Content-Length"] = String(body.length);
    headers.Origin = origin ?? `http://127.0.0.1:${port}`;
    headers["Sec-Fetch-Site"] = "same-origin";
  }
  return new Promise((resolve, reject) => {
    const call = http.request({ hostname: "127.0.0.1", port, path, method, headers, agent: false }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({
        status: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }));
    });
    call.once("error", reject);
    if (body !== null) call.write(body);
    call.end();
  });
}

async function waitForPage(port, child, stderr) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Pixel control exited before readiness: ${stderr()}`);
    try {
      const response = await request(port, "/");
      if (response.status === 200) return response;
    } catch { /* The listener is not ready yet. */ }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Pixel control did not become ready: ${stderr()}`);
}

async function stop(child) {
  const exited = () => child.exitCode !== null || child.signalCode !== null;
  if (exited()) return;
  child.kill();
  await Promise.race([once(child, "exit"), new Promise((resolve) => setTimeout(resolve, 3000))]);
  if (!exited()) {
    child.kill("SIGKILL");
    await once(child, "exit");
  }
}

test("local control HTTP pause, resume, and safe cancel bind exact durable goal state without leaking private source", {
  skip: process.platform === "win32" ? "supported-host subprocess and private-file qualification requires POSIX" : false,
}, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-control-deep-work-e2e-"));
  let child = null;
  t.after(async () => { if (child !== null) await stop(child); await rm(root, { recursive: true, force: true }); });
  const stateRoot = join(root, "goal-state");
  const controlState = join(root, "control-state");
  await mkdir(stateRoot, { mode: 0o700 });
  await mkdir(controlState, { mode: 0o700 });
  if (process.platform !== "win32") {
    await chmod(stateRoot, 0o700);
    await chmod(controlState, 0o700);
  }
  const now = new Date();
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE_HTTP_PAUSE_CHILD_CANARY", acceptanceCriteria: ["A verified local report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE_HTTP_PAUSE_GOAL_CANARY", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 60, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 65536, maxFailures: 1 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  const goalPath = join(root, "goal.json");
  const jobsPath = join(root, "jobs.json");
  const workPolicyPath = join(root, "work-policy.json");
  const configPath = join(root, "controller.json");
  const statusPath = join(stateRoot, "operator-status", "status.json");
  const controlPolicyPath = join(root, "control-policy.json");
  const controller = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot, goalPath, jobsPath, policyPath: workPolicyPath, objectStore: join(root, "objects"), workspaceRoot: join(root, "workspace"), executorPath: join(root, "executor"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 },
    runtime: { dockerPath: join(root, "docker"), backendNetworkName: "pixel-test", backendContainerName: "pixel-model", networkSubnet: "172.30.10.0/29", workerIp: "172.30.10.2", proxyIp: "172.30.10.3", uid: 1000, gid: 1000 },
    controller: { maxTransitions: 1 },
  };
  const controlPolicy = {
    schemaVersion: 1,
    actions: {
      updateCheck: false, backupCreate: false, operationsPause: false, frontierPause: false,
      deepWorkPause: true, deepWorkResume: true, deepWorkCancel: true,
    },
    views: { frontierReviews: false },
    backup: { directory: "/var/backups/pixel", ageRecipient: "age1disabled" },
  };
  await Promise.all([
    privateJson(goalPath, goal), privateJson(jobsPath, [job]), privateJson(workPolicyPath, {}),
    privateJson(configPath, controller), privateJson(controlPolicyPath, controlPolicy),
  ]);
  await initializeGoalLedger({ stateRoot, goal, jobs: [job], now, suffix: "100000000001" });
  await publishGoalOperatorStatus({ stateRoot, goal, jobs: [job], now: new Date(now.getTime() + 1), secret: Buffer.alloc(32, 19), suffix: "100000000002" });

  const port = await unusedPort();
  const python = process.env.PIXEL_TEST_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
  child = spawn(python, [
    join(repo, "control", "server.py"), "--root", repo, "--state", controlState,
    "--onboarding", join(root, "absent-onboarding.json"), "--policy", controlPolicyPath,
    "--work-status", statusPath, "--work-controller-config", configPath, "--port", String(port),
  ], { cwd: repo, stdio: ["ignore", "pipe", "pipe"] });
  let stderrBytes = Buffer.alloc(0);
  child.stderr.on("data", (chunk) => { stderrBytes = Buffer.concat([stderrBytes, chunk]).subarray(-8192); });
  child.stdout.resume();
  const page = await waitForPage(port, child, () => stderrBytes.toString("utf8"));
  const cookie = page.headers["set-cookie"]?.[0]?.split(";", 1)[0];
  assert.match(cookie ?? "", /^pixel_control=[A-Za-z0-9_-]{43}$/u);

  const initial = await request(port, "/api/v1/status", { cookie });
  assert.equal(initial.status, 200);
  const initialStatus = JSON.parse(initial.body);
  assert.equal(initialStatus.operatorActions.deepWorkPause, true);
  assert.equal(initialStatus.operatorActions.deepWorkResume, false);
  assert.equal(initialStatus.operatorActions.deepWorkCancel, true);

  const hostile = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, value: { schemaVersion: 1, kind: "deep-work-pause", path: configPath },
  });
  assert.equal(hostile.status, 400);
  assert.equal((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).head.state, "ready");

  const crossSite = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, origin: "https://attacker.invalid", value: { schemaVersion: 1, kind: "deep-work-pause" },
  });
  assert.equal(crossSite.status, 403);
  const previewResponse = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, value: { schemaVersion: 1, kind: "deep-work-pause" },
  });
  assert.equal(previewResponse.status, 200);
  const preview = JSON.parse(previewResponse.body);
  assert.match(preview.actionHash, /^[a-f0-9]{64}$/u);
  assert.match(preview.effect, /bounded step that already started may finish/u);

  const wrongConfirmation = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: digest("0") },
  });
  assert.equal(wrongConfirmation.status, 400);
  assert.equal((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).head.state, "ready");

  const executionResponse = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash },
  });
  assert.equal(executionResponse.status, 200, executionResponse.body.toString("utf8"));
  const execution = JSON.parse(executionResponse.body);
  assert.equal(execution.status, "succeeded");
  const ledger = await recoverGoalLedger({ stateRoot, goal, jobs: [job] });
  assert.equal(ledger.head.state, "paused");
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused"]);
  assert.equal(ledger.head.progress.jobsStarted, 0);

  const replay = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash },
  });
  assert.equal(replay.status, 404);
  assert.deepEqual((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused"]);

  const pausedStatusResponse = await request(port, "/api/v1/status", { cookie });
  assert.equal(pausedStatusResponse.status, 200);
  const pausedStatus = JSON.parse(pausedStatusResponse.body);
  assert.equal(pausedStatus.operatorActions.deepWorkPause, false);
  assert.equal(pausedStatus.operatorActions.deepWorkResume, true);
  assert.equal(pausedStatus.operatorActions.deepWorkCancel, true);

  const hostileResume = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, value: { schemaVersion: 1, kind: "deep-work-resume", checkpoint: digest("1") },
  });
  assert.equal(hostileResume.status, 400);
  const resumePreviewResponse = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, value: { schemaVersion: 1, kind: "deep-work-resume" },
  });
  assert.equal(resumePreviewResponse.status, 200, resumePreviewResponse.body.toString("utf8"));
  const resumePreview = JSON.parse(resumePreviewResponse.body);
  assert.match(resumePreview.actionHash, /^[a-f0-9]{64}$/u);
  assert.doesNotMatch(JSON.stringify(resumePreview), /checkpointSha256|reviewSha256|PRIVATE_HTTP/u);
  const resumeExecutionResponse = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: resumePreview.actionId, actionHash: resumePreview.actionHash },
  });
  assert.equal(resumeExecutionResponse.status, 200, resumeExecutionResponse.body.toString("utf8"));
  const resumeExecution = JSON.parse(resumeExecutionResponse.body);
  assert.equal(resumeExecution.status, "succeeded");
  assert.deepEqual((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused", "ready"]);

  const cancelPreviewResponse = await request(port, "/api/v1/actions/preview", {
    method: "POST", cookie, value: { schemaVersion: 1, kind: "deep-work-cancel" },
  });
  assert.equal(cancelPreviewResponse.status, 200, cancelPreviewResponse.body.toString("utf8"));
  const cancelPreview = JSON.parse(cancelPreviewResponse.body);
  assert.match(cancelPreview.actionHash, /^[a-f0-9]{64}$/u);
  assert.match(cancelPreview.effect, /cannot stop or hide a live worker/u);
  assert.doesNotMatch(JSON.stringify(cancelPreview), /goalCheckpointSha256|reviewSha256|PRIVATE_HTTP/u);
  const cancelWrong = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: cancelPreview.actionId, actionHash: digest("2") },
  });
  assert.equal(cancelWrong.status, 400);
  assert.equal((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).head.state, "ready");
  const cancelExecutionResponse = await request(port, "/api/v1/actions/execute", {
    method: "POST", cookie, value: { schemaVersion: 1, actionId: cancelPreview.actionId, actionHash: cancelPreview.actionHash },
  });
  assert.equal(cancelExecutionResponse.status, 200, cancelExecutionResponse.body.toString("utf8"));
  const cancelExecution = JSON.parse(cancelExecutionResponse.body);
  assert.equal(cancelExecution.status, "succeeded");
  assert.deepEqual((await recoverGoalLedger({ stateRoot, goal, jobs: [job] })).checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused", "ready", "cancelled"]);

  const deepWorkResponse = await request(port, "/api/v1/deep-work", { cookie });
  const finalStatusResponse = await request(port, "/api/v1/status", { cookie });
  assert.equal(deepWorkResponse.status, 200);
  assert.equal(finalStatusResponse.status, 200);
  const deepWork = JSON.parse(deepWorkResponse.body);
  const finalStatus = JSON.parse(finalStatusResponse.body);
  assert.equal(deepWork.goal.state, "cancelled");
  assert.equal(deepWork.goal.nextAction, "terminal");
  assert.equal(finalStatus.operatorActions.deepWorkPause, false);
  assert.equal(finalStatus.operatorActions.deepWorkResume, false);
  assert.equal(finalStatus.operatorActions.deepWorkCancel, false);
  const publicEvidence = JSON.stringify({ initialStatus, hostile: JSON.parse(hostile.body), crossSite: JSON.parse(crossSite.body), preview, execution, pausedStatus, hostileResume: JSON.parse(hostileResume.body), resumePreview, resumeExecution, cancelPreview, cancelWrong: JSON.parse(cancelWrong.body), cancelExecution, deepWork, finalStatus });
  assert.doesNotMatch(publicEvidence, /PRIVATE_HTTP_PAUSE|controller\.json|work-policy\.json|goal-state|pixel-control-deep-work-e2e/u);
  await stop(child);
});
