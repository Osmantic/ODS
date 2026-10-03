import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { chmod, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { canonical, validateWorkGoalDeclaration, validateWorkGoalDraft, validateWorkJob } from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const sha = (value) => createHash("sha256").update(value).digest("hex");
const CONFIG_BOUNDARY = "Owner-private fixed paths for inert Deep Work drafting only. The browser cannot supply, view, or alter these paths and gains no input admission, execution, lease, scheduling, credential, external-effect, scope-expansion, or completion authority.";
const CATALOG_BOUNDARY = "Owner-selected content-addressed local input references only. The catalog grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";

function octal(value, length) { return `${value.toString(8).padStart(length - 1, "0")}\0`; }

function archive(name, content) {
  const bytes = Buffer.from(content, "utf8"), header = Buffer.alloc(512);
  header.write(name, 0, 100, "utf8"); header.write(octal(0o644, 8), 100, 8, "ascii");
  header.write(octal(0, 8), 108, 8, "ascii"); header.write(octal(0, 8), 116, 8, "ascii");
  header.write(octal(bytes.length, 12), 124, 12, "ascii"); header.write(octal(0, 12), 136, 12, "ascii");
  header.fill(0x20, 148, 156); header[156] = 0x30; header.write("ustar\0", 257, 6, "latin1"); header.write("00", 263, 2, "ascii");
  let checksum = 0; for (const byte of header) checksum += byte;
  header.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii");
  const padding = (512 - (bytes.length % 512)) % 512;
  return Buffer.concat([header, bytes, Buffer.alloc(padding), Buffer.alloc(1024)]);
}

async function privateJson(path, value) {
  await writeFile(path, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function privateDirectory(path) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  if (process.platform !== "win32") await chmod(path, 0o700);
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

function request(port, path, { method = "GET", cookie = null, token = null, value = null, origin = null } = {}) {
  const body = value === null ? null : Buffer.from(JSON.stringify(value), "utf8");
  const headers = { Host: `127.0.0.1:${port}`, Connection: "close" };
  if (cookie !== null) headers.Cookie = cookie;
  if (token !== null) headers["X-Pixel-Review-Token"] = token;
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
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    call.once("error", reject);
    if (body !== null) call.write(body);
    call.end();
  });
}

async function waitForPage(port, child, stderr) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (child.exitCode !== null || child.signalCode !== null) throw new Error(`Pixel control exited before readiness: ${stderr()}`);
    try {
      const response = await request(port, "/");
      if (response.status === 200) return response;
    } catch { /* Listener is not ready yet. */ }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Pixel control did not become ready: ${stderr()}`);
}

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  await Promise.race([once(child, "exit"), new Promise((resolve) => setTimeout(resolve, 3000))]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await once(child, "exit");
  }
}

test("loopback authoring reviews, prepares, stages, and renders inactive supervision without exposing private material", {
  skip: process.platform === "win32" ? "supported-host private-file qualification requires POSIX" : false,
}, async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-control-work-draft-e2e-"));
  let child = null;
  t.after(async () => { if (child !== null) await stop(child); await rm(root, { recursive: true, force: true }); });
  const objects = join(root, "objects"), drafts = join(root, "drafts"), launches = join(root, "launches"), services = join(root, "services"), workState = join(root, "work-state"), workspaces = join(root, "workspaces"), bin = join(root, "bin"), controlState = join(root, "control-state");
  await Promise.all([objects, drafts, launches, services, workState, workspaces, bin, controlState].map(privateDirectory));
  const privateInput = archive("project/README.md", "inert admitted project snapshot\n");
  const contentHash = sha(privateInput);
  await writeFile(join(objects, `${contentHash}.tar`), privateInput, { mode: 0o600 });
  const policy = JSON.parse(await readFile(join(repo, "deploy", "work-broker", "policy.example.json"), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.localModel.prepared = true;
  policy.verifier.enabled = true;
  policy.profiles.scout.enabled = true;
  const executor = Buffer.from("pinned portal preparation OMP fixture\n", "utf8");
  const docker = Buffer.from("pinned portal preparation Docker fixture\n", "utf8");
  const executorPath = join(bin, "omp"), dockerPath = join(bin, "docker");
  await writeFile(executorPath, executor, { mode: 0o500 });
  await writeFile(dockerPath, docker, { mode: 0o500 });
  await chmod(executorPath, 0o500); await chmod(dockerPath, 0o500);
  policy.executor.sha256 = sha(executor);
  await installFixtureModelQualification(policy, root, new Date());
  const catalog = {
    $schema: "https://osmantic.com/pixel/schemas/work-input-catalog-v1.schema.json", schemaVersion: 1,
    catalogId: "inputcatalog-1786424400000-abcdef123456", createdAt: "2026-08-11T05:00:00.000Z",
    entries: [{
      id: "private-project", kind: "repository-snapshot", objectName: `${contentHash}.tar`, contentSha256: contentHash,
      bytes: privateInput.length, classification: "internal", mountMode: "read-only",
    }],
    boundary: CATALOG_BOUNDARY,
  };
  const policyPath = join(root, "work-policy.json"), catalogPath = join(root, "catalog.json"), configPath = join(root, "authoring.json"), launchConfigPath = join(root, "launch-control.json"), serviceConfigPath = join(root, "service-control.json"), environmentPath = join(root, "environment.json"), controlPolicyPath = join(root, "control-policy.json");
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/control-work-authoring-config-v1.schema.json", schemaVersion: 1,
    policyFile: policyPath, inputCatalogFile: catalogPath, objectStoreDirectory: objects, draftDirectory: drafts,
    maxDrafts: 1, boundary: CONFIG_BOUNDARY,
  };
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json", schemaVersion: 1,
    stateRoot: workState, policyPath, objectStore: objects, workspaceRoot: workspaces, executorPath,
    archiveLimits: { maxEntries: 1000, maxFileBytes: 1048576 },
    runtime: { dockerPath, backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: process.geteuid?.() ?? 10001, gid: process.getegid?.() ?? 10001 },
    boundary: "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.",
  };
  const launchConfig = {
    $schema: "https://osmantic.com/pixel/schemas/control-work-launch-config-v1.schema.json", schemaVersion: 1,
    environmentFile: environmentPath, draftDirectory: drafts, launchDirectory: launches, maxLaunches: 2,
    boundary: "Owner-private fixed paths for exact Deep Work launch preparation only. The browser cannot supply, view, or alter these paths and gains no staging, scheduling, execution, service, credential, external-effect, scope-expansion, or completion authority.",
  };
  const serviceConfig = {
    $schema: "https://osmantic.com/pixel/schemas/control-work-service-config-v1.schema.json", schemaVersion: 1,
    serviceDirectory: services, installRoot: "/opt/pixel", nodePath: "/usr/bin/node", flockPath: "/usr/bin/flock",
    serviceUser: "node", serviceGroup: "node", dockerGroup: "node", watchdogSeconds: 30, maxBundles: 2,
    boundary: "Owner-private fixed paths and identities for inactive Deep Work service rendering only. The browser cannot supply, view, or alter them and gains no installation, scheduling, activation, execution, lease, credential, external-effect, scope-expansion, or completion authority.",
  };
  const controlPolicy = {
    schemaVersion: 1,
    actions: { updateCheck: false, backupCreate: false, operationsPause: false, frontierPause: false, deepWorkPause: false, deepWorkDraft: true, deepWorkPrepare: true, deepWorkStage: true, deepWorkServiceRender: true },
    views: { frontierReviews: false }, backup: { directory: "/var/backups/pixel", ageRecipient: "age1disabled" },
  };
  await Promise.all([
    privateJson(policyPath, policy), privateJson(catalogPath, catalog), privateJson(configPath, config), privateJson(launchConfigPath, launchConfig), privateJson(serviceConfigPath, serviceConfig), privateJson(environmentPath, environment), privateJson(controlPolicyPath, controlPolicy),
  ]);

  const port = await unusedPort();
  const python = process.env.PIXEL_TEST_PYTHON ?? "python3";
  child = spawn(python, [
    join(repo, "control", "server.py"), "--root", repo, "--state", controlState,
    "--onboarding", join(root, "absent-onboarding.json"), "--policy", controlPolicyPath,
    "--work-authoring-config", configPath, "--work-launch-config", launchConfigPath, "--work-service-config", serviceConfigPath, "--port", String(port),
  ], { cwd: repo, stdio: ["ignore", "pipe", "pipe"] });
  let stdoutBytes = Buffer.alloc(0), stderrBytes = Buffer.alloc(0);
  child.stdout.on("data", (chunk) => { stdoutBytes = Buffer.concat([stdoutBytes, chunk]).subarray(-8192); });
  child.stderr.on("data", (chunk) => { stderrBytes = Buffer.concat([stderrBytes, chunk]).subarray(-8192); });
  const page = await waitForPage(port, child, () => stderrBytes.toString("utf8"));
  const cookie = page.headers["set-cookie"]?.[0]?.split(";", 1)[0];
  assert.match(cookie ?? "", /^pixel_control=[A-Za-z0-9_-]{43}$/u);
  for (let attempt = 0; attempt < 40 && !/#review=([A-Za-z0-9_-]{43})/u.test(stdoutBytes.toString("utf8")); attempt += 1) await new Promise((resolve) => setTimeout(resolve, 25));
  const token = /#review=([A-Za-z0-9_-]{43})/u.exec(stdoutBytes.toString("utf8"))?.[1];
  assert.match(token ?? "", /^[A-Za-z0-9_-]{43}$/u);

  const deniedView = await request(port, "/api/v1/deep-work/authoring", { cookie });
  assert.equal(deniedView.status, 403);
  const deniedDraftReviews = await request(port, "/api/v1/deep-work/drafts", { cookie });
  assert.equal(deniedDraftReviews.status, 403);
  const viewResponse = await request(port, "/api/v1/deep-work/authoring", { cookie, token });
  assert.equal(viewResponse.status, 200, viewResponse.body.toString("utf8"));
  const view = JSON.parse(viewResponse.body);
  assert.equal(view.state, "ready");
  assert.deepEqual(view.profiles.filter((entry) => entry.enabled).map((entry) => entry.kind), ["inspect"]);
  assert.equal(view.inputs[0].label, "Local repository 1");
  const inputHandle = view.inputs[0].handle;
  const draftRequest = {
    schemaVersion: 1, kind: "deep-work-draft", authoringRevision: view.revision,
    objective: "PRIVATE HTTP LONG GOAL CANARY", dataClassification: "internal",
    milestones: [
      { kind: "inspect", objective: "PRIVATE HTTP BASELINE CANARY", doneWhen: ["Return an independently checked baseline"], dependsOn: [], inputHandles: [inputHandle], effort: "standard" },
      { kind: "inspect", objective: "PRIVATE HTTP PATH A CANARY", doneWhen: ["Return independently checked path A evidence"], dependsOn: [1], inputHandles: [inputHandle], effort: "standard" },
      { kind: "inspect", objective: "PRIVATE HTTP PATH B CANARY", doneWhen: ["Return independently checked path B evidence"], dependsOn: [1], inputHandles: [inputHandle], effort: "standard" },
      { kind: "inspect", objective: "PRIVATE HTTP CONVERGENCE CANARY", doneWhen: ["Reconcile both independently checked paths"], dependsOn: [2, 3], inputHandles: [inputHandle], effort: "standard" },
    ],
  };
  const noToken = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, value: draftRequest });
  assert.equal(noToken.status, 403);
  const crossSite = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: draftRequest, origin: "https://attacker.invalid" });
  assert.equal(crossSite.status, 403);
  const smuggled = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: { ...draftRequest, outputPath: join(root, "escape") } });
  assert.equal(smuggled.status, 400);
  const downgraded = structuredClone(draftRequest);
  downgraded.dataClassification = "public";
  const downgrade = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: downgraded });
  assert.equal(downgrade.status, 400);

  const previewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: draftRequest });
  assert.equal(previewResponse.status, 200, previewResponse.body.toString("utf8"));
  const preview = JSON.parse(previewResponse.body);
  const publicBefore = JSON.stringify({ view, noToken: JSON.parse(noToken.body), crossSite: JSON.parse(crossSite.body), smuggled: JSON.parse(smuggled.body), downgrade: JSON.parse(downgrade.body), preview });
  assert.doesNotMatch(publicBefore, /PRIVATE HTTP|private-project|work-policy\.json|catalog\.json|authoring\.json|pixel-control-work-draft-e2e/u);
  const wrong = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: "0".repeat(64) } });
  assert.equal(wrong.status, 400);
  assert.deepEqual(await readdir(drafts), []);
  const executionResponse = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash } });
  assert.equal(executionResponse.status, 200, executionResponse.body.toString("utf8"));
  const execution = JSON.parse(executionResponse.body);
  assert.equal(execution.status, "succeeded");
  const names = await readdir(drafts);
  assert.deepEqual(names, [preview.actionId]);
  const output = join(drafts, preview.actionId);
  const declaration = JSON.parse(await readFile(join(output, "goal-declaration.json"), "utf8"));
  const jobs = JSON.parse(await readFile(join(output, "jobs.json"), "utf8"));
  const review = JSON.parse(await readFile(join(output, "goal-draft.json"), "utf8"));
  assert.deepEqual(validateWorkGoalDeclaration(declaration), []);
  assert.deepEqual(validateWorkGoalDraft(review), []);
  assert.equal(jobs.length, 4);
  assert.deepEqual(declaration.milestones.map((entry) => entry.dependsOn), [[], ["step-1"], ["step-1"], ["step-2", "step-3"]]);
  for (const job of jobs) {
    assert.deepEqual(validateWorkJob(job), []);
    assert.equal(job.profile, "scout");
    assert.equal(job.requestedCapabilities.modelRoute, "local-only");
    assert.equal(job.requestedCapabilities.externalEffects, false);
    assert.equal(job.inputs[0].id, "private-project");
  }
  assert.equal(review.authority.grantsExecution, false);
  assert.equal(review.authority.grantsScheduling, false);
  const draftReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(draftReviewsResponse.status, 200, draftReviewsResponse.body.toString("utf8"));
  const draftReviews = JSON.parse(draftReviewsResponse.body);
  assert.equal(draftReviews.state, "ready");
  assert.equal(draftReviews.drafts.length, 1);
  assert.equal(draftReviews.drafts[0].objective, "PRIVATE HTTP LONG GOAL CANARY");
  assert.equal(draftReviews.drafts[0].milestoneCount, 4);
  assert.deepEqual(draftReviews.drafts[0].milestones.map((entry) => entry.dependsOn), [[], [1], [1], [2, 3]]);
  assert.equal(draftReviews.drafts[0].reviewSha256, sha(canonical(review)));
  assert.equal(draftReviews.drafts[0].preparationState, "draft");
  assert.equal(draftReviews.drafts[0].canPrepare, true);
  assert.equal(draftReviews.drafts[0].launchPackage, null);
  assert.deepEqual(draftReviews.launch, { state: "ready", preparedUsed: 0, maxPrepared: 2 });
  assert.deepEqual(draftReviews.service, { state: "ready", renderedUsed: 0, maxRendered: 2 });
  assert.equal(draftReviews.drafts[0].authority.grantsExecution, false);
  assert.equal(draftReviews.privacy.pathsExposed, false);
  assert.equal(draftReviews.privacy.inputIdentitiesExposed, false);
  assert.doesNotMatch(JSON.stringify(draftReviews), /private-project|goal-draft\.json|work-policy\.json|catalog\.json|authoring\.json|pixel-control-work-draft-e2e/u);
  const prepareRequest = {
    schemaVersion: 1, kind: "deep-work-prepare", authoringRevision: draftReviews.revision,
    draftHandle: draftReviews.drafts[0].handle, reviewSha256: draftReviews.drafts[0].reviewSha256,
  };
  const prepareWithoutToken = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, value: prepareRequest });
  assert.equal(prepareWithoutToken.status, 403);
  const stalePrepare = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: { ...prepareRequest, reviewSha256: "0".repeat(64) } });
  assert.equal(stalePrepare.status, 400);
  const staleEnvironmentPreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: prepareRequest });
  assert.equal(staleEnvironmentPreviewResponse.status, 200, staleEnvironmentPreviewResponse.body.toString("utf8"));
  const staleEnvironmentPreview = JSON.parse(staleEnvironmentPreviewResponse.body);
  await privateJson(environmentPath, { ...environment, archiveLimits: { ...environment.archiveLimits, maxEntries: 999 } });
  const staleEnvironmentExecution = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: staleEnvironmentPreview.actionId, actionHash: staleEnvironmentPreview.actionHash } });
  assert.equal(staleEnvironmentExecution.status, 400);
  assert.deepEqual(await readdir(launches), []);
  await privateJson(environmentPath, environment);
  const preparePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: prepareRequest });
  assert.equal(preparePreviewResponse.status, 200, preparePreviewResponse.body.toString("utf8"));
  const preparePreview = JSON.parse(preparePreviewResponse.body);
  assert.equal(preparePreview.kind, "deep-work-prepare");
  assert.doesNotMatch(JSON.stringify(preparePreview), /PRIVATE HTTP|private-project|work-policy\.json|environment\.json|launch-control\.json|pixel-control-work-draft-e2e/u);
  const competingPreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: prepareRequest });
  assert.equal(competingPreviewResponse.status, 200, competingPreviewResponse.body.toString("utf8"));
  const competingPreview = JSON.parse(competingPreviewResponse.body);
  const prepareExecutionResponse = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: preparePreview.actionId, actionHash: preparePreview.actionHash } });
  assert.equal(prepareExecutionResponse.status, 200, prepareExecutionResponse.body.toString("utf8"));
  const prepareExecution = JSON.parse(prepareExecutionResponse.body);
  assert.equal(prepareExecution.status, "succeeded");
  const competingExecution = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: competingPreview.actionId, actionHash: competingPreview.actionHash } });
  assert.equal(competingExecution.status, 400);
  assert.deepEqual(await readdir(launches), [preparePreview.actionId]);
  assert.deepEqual((await readdir(join(launches, preparePreview.actionId))).sort(), ["assembly", "compiled-jobs", "launch-preparation.json"]);
  assert.deepEqual(await readdir(workState), [], "portal launch preparation must not create ready goal custody");
  const preparedDraftReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(preparedDraftReviewsResponse.status, 200);
  const preparedDraftReviews = JSON.parse(preparedDraftReviewsResponse.body);
  assert.equal(preparedDraftReviews.drafts[0].preparationState, "prepared-inactive");
  assert.equal(preparedDraftReviews.drafts[0].canPrepare, false);
  assert.equal(preparedDraftReviews.drafts[0].launchPackage.state, "prepared-inactive");
  assert.equal(preparedDraftReviews.drafts[0].launchPackage.childCount, 4);
  assert.equal(preparedDraftReviews.drafts[0].launchPackage.canStage, true);
  assert.equal(preparedDraftReviews.drafts[0].launchPackage.canRenderService, false);
  assert.equal(preparedDraftReviews.drafts[0].launchPackage.servicePackage, null);
  assert.deepEqual(preparedDraftReviews.launch, { state: "ready", preparedUsed: 1, maxPrepared: 2 });
  const duplicatePrepare = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: prepareRequest });
  assert.equal(duplicatePrepare.status, 400);
  const launchManifest = JSON.parse(await readFile(join(launches, preparePreview.actionId, "launch-preparation.json"), "utf8"));
  const compiledPlanPath = join(launches, preparePreview.actionId, "compiled-jobs", launchManifest.children[0].jobId, "plan.json");
  const compiledPlanBytes = await readFile(compiledPlanPath);
  await privateJson(compiledPlanPath, { tampered: true });
  const corruptedLaunchReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(corruptedLaunchReviewsResponse.status, 200);
  const corruptedLaunchReviews = JSON.parse(corruptedLaunchReviewsResponse.body);
  assert.equal(corruptedLaunchReviews.state, "ready", "launch corruption must not hide a still-valid retained draft");
  assert.deepEqual(corruptedLaunchReviews.launch, { state: "unavailable", preparedUsed: 0, maxPrepared: 0 });
  assert.equal(corruptedLaunchReviews.drafts[0].canPrepare, false);
  await writeFile(compiledPlanPath, compiledPlanBytes, { mode: 0o600 });
  const restoredLaunchReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(restoredLaunchReviewsResponse.status, 200);
  const restoredLaunchReviews = JSON.parse(restoredLaunchReviewsResponse.body);
  assert.equal(restoredLaunchReviews.drafts[0].preparationState, "prepared-inactive");
  const stageRequest = {
    schemaVersion: 1, kind: "deep-work-stage", authoringRevision: restoredLaunchReviews.revision,
    draftHandle: restoredLaunchReviews.drafts[0].handle, reviewSha256: restoredLaunchReviews.drafts[0].reviewSha256,
    packageHandle: restoredLaunchReviews.drafts[0].launchPackage.handle,
    manifestSha256: restoredLaunchReviews.drafts[0].launchPackage.manifestSha256,
  };
  const stageWithoutToken = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, value: stageRequest });
  assert.equal(stageWithoutToken.status, 403);
  const staleStageSelection = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: { ...stageRequest, manifestSha256: "0".repeat(64) } });
  assert.equal(staleStageSelection.status, 400);
  const changedPackagePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: stageRequest });
  assert.equal(changedPackagePreviewResponse.status, 200);
  const changedPackagePreview = JSON.parse(changedPackagePreviewResponse.body);
  await privateJson(compiledPlanPath, { tamperedAfterPreview: true });
  const changedPackageExecution = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: changedPackagePreview.actionId, actionHash: changedPackagePreview.actionHash } });
  assert.equal(changedPackageExecution.status, 400);
  assert.deepEqual(await readdir(workState), []);
  await writeFile(compiledPlanPath, compiledPlanBytes, { mode: 0o600 });
  const stagePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: stageRequest });
  assert.equal(stagePreviewResponse.status, 200, stagePreviewResponse.body.toString("utf8"));
  const stagePreview = JSON.parse(stagePreviewResponse.body);
  assert.equal(stagePreview.kind, "deep-work-stage");
  assert.doesNotMatch(JSON.stringify(stagePreview), /PRIVATE HTTP|private-project|work-policy\.json|environment\.json|launch-control\.json|pixel-control-work-draft-e2e/u);
  const stageExecutionResponse = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: stagePreview.actionId, actionHash: stagePreview.actionHash } });
  assert.equal(stageExecutionResponse.status, 200, stageExecutionResponse.body.toString("utf8"));
  const stageExecution = JSON.parse(stageExecutionResponse.body);
  assert.equal(stageExecution.status, "succeeded");
  assert.ok((await readdir(workState)).includes("goal-staging"));
  const stagedReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(stagedReviewsResponse.status, 200);
  const stagedReviews = JSON.parse(stagedReviewsResponse.body);
  assert.equal(stagedReviews.drafts[0].preparationState, "staged-inactive");
  assert.equal(stagedReviews.drafts[0].launchPackage.stageReceiptPresent, true);
  assert.equal(stagedReviews.drafts[0].launchPackage.canRenderService, true);
  assert.equal(stagedReviews.drafts[0].launchPackage.servicePackage, null);
  const restagePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: {
    ...stageRequest, packageHandle: stagedReviews.drafts[0].launchPackage.handle,
  } });
  assert.equal(restagePreviewResponse.status, 200);
  const restagePreview = JSON.parse(restagePreviewResponse.body);
  const restageExecutionResponse = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: restagePreview.actionId, actionHash: restagePreview.actionHash } });
  assert.equal(restageExecutionResponse.status, 200);
  assert.equal(JSON.parse(restageExecutionResponse.body).status, "succeeded");
  const stagingGoals = await readdir(join(workState, "goal-staging"));
  assert.equal(stagingGoals.length, 1);
  const stageReceiptPath = join(workState, "goal-staging", stagingGoals[0], "stage.json");
  const stageReceiptBytes = await readFile(stageReceiptPath);
  const stageReceipt = JSON.parse(stageReceiptBytes);
  const serviceRequest = {
    schemaVersion: 1, kind: "deep-work-service-render", authoringRevision: stagedReviews.revision,
    draftHandle: stagedReviews.drafts[0].handle, reviewSha256: stagedReviews.drafts[0].reviewSha256,
    packageHandle: stagedReviews.drafts[0].launchPackage.handle,
    manifestSha256: stagedReviews.drafts[0].launchPackage.manifestSha256,
  };
  const serviceWithoutToken = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, value: serviceRequest });
  assert.equal(serviceWithoutToken.status, 403);
  const staleServiceSelection = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: { ...serviceRequest, manifestSha256: "0".repeat(64) } });
  assert.equal(staleServiceSelection.status, 400);
  const changedServicePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: serviceRequest });
  assert.equal(changedServicePreviewResponse.status, 200);
  const changedServicePreview = JSON.parse(changedServicePreviewResponse.body);
  await privateJson(serviceConfigPath, { ...serviceConfig, watchdogSeconds: 31 });
  const changedServiceExecution = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: changedServicePreview.actionId, actionHash: changedServicePreview.actionHash } });
  assert.equal(changedServiceExecution.status, 400);
  assert.deepEqual(await readdir(services), []);
  await privateJson(serviceConfigPath, serviceConfig);
  const servicePreviewResponse = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: serviceRequest });
  assert.equal(servicePreviewResponse.status, 200, servicePreviewResponse.body.toString("utf8"));
  const servicePreview = JSON.parse(servicePreviewResponse.body);
  assert.equal(servicePreview.kind, "deep-work-service-render");
  assert.doesNotMatch(JSON.stringify(servicePreview), /PRIVATE HTTP|private-project|work-policy\.json|environment\.json|launch-control\.json|service-control\.json|pixel-control-work-draft-e2e/u);
  const serviceExecutionResponse = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: servicePreview.actionId, actionHash: servicePreview.actionHash } });
  assert.equal(serviceExecutionResponse.status, 200, serviceExecutionResponse.body.toString("utf8"));
  const serviceExecution = JSON.parse(serviceExecutionResponse.body);
  assert.equal(serviceExecution.status, "succeeded", JSON.stringify(serviceExecution));
  const serviceBundles = await readdir(services);
  assert.equal(serviceBundles.length, 1);
  const renderedReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(renderedReviewsResponse.status, 200);
  const renderedReviews = JSON.parse(renderedReviewsResponse.body);
  assert.deepEqual(renderedReviews.service, { state: "ready", renderedUsed: 1, maxRendered: 2 });
  assert.equal(renderedReviews.drafts[0].launchPackage.canRenderService, false);
  assert.equal(renderedReviews.drafts[0].launchPackage.servicePackage.state, "rendered-inactive");
  assert.equal(renderedReviews.drafts[0].launchPackage.servicePackage.executionModel, "durable-event-driven");
  assert.equal(renderedReviews.drafts[0].launchPackage.servicePackage.watchdogRole, "liveness-only");
  assert.equal(renderedReviews.drafts[0].launchPackage.servicePackage.canInstall, false);
  assert.equal(renderedReviews.drafts[0].launchPackage.servicePackage.canActivate, false);
  const duplicateService = await request(port, "/api/v1/actions/preview", { method: "POST", cookie, token, value: serviceRequest });
  assert.equal(duplicateService.status, 400);
  const serviceBundlePath = join(services, serviceBundles[0]);
  const serviceManifest = JSON.parse(await readFile(join(serviceBundlePath, "service-bundle.json"), "utf8"));
  const serviceUnitPath = join(serviceBundlePath, serviceManifest.serviceName);
  const serviceUnitBytes = await readFile(serviceUnitPath);
  await writeFile(serviceUnitPath, "tampered\n", { mode: 0o600 });
  const corruptedServiceReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(corruptedServiceReviewsResponse.status, 200);
  const corruptedServiceReviews = JSON.parse(corruptedServiceReviewsResponse.body);
  assert.equal(corruptedServiceReviews.service.state, "unavailable");
  assert.equal(corruptedServiceReviews.drafts[0].preparationState, "staged-inactive");
  assert.equal(corruptedServiceReviews.drafts[0].launchPackage.servicePackage, null);
  assert.equal(corruptedServiceReviews.drafts[0].launchPackage.canRenderService, false);
  await writeFile(serviceUnitPath, serviceUnitBytes, { mode: 0o600 });
  const recoveredServiceReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(recoveredServiceReviewsResponse.status, 200);
  assert.equal(JSON.parse(recoveredServiceReviewsResponse.body).drafts[0].launchPackage.servicePackage.state, "rendered-inactive");
  await privateJson(stageReceiptPath, { ...stageReceipt, configSha256: "0".repeat(64) });
  const corruptedStageReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(corruptedStageReviewsResponse.status, 200);
  const corruptedStageReviews = JSON.parse(corruptedStageReviewsResponse.body);
  assert.equal(corruptedStageReviews.launch.state, "unavailable");
  assert.equal(corruptedStageReviews.drafts[0].preparationState, "unavailable");
  assert.equal(corruptedStageReviews.drafts[0].launchPackage, null);
  await writeFile(stageReceiptPath, stageReceiptBytes, { mode: 0o600 });
  const recoveredStageReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(recoveredStageReviewsResponse.status, 200);
  assert.equal(JSON.parse(recoveredStageReviewsResponse.body).drafts[0].preparationState, "staged-inactive");
  if (process.platform !== "win32") {
    assert.equal((await stat(output)).mode & 0o777, 0o700);
    assert.equal((await stat(join(output, "goal-draft.json"))).mode & 0o777, 0o600);
  }
  const replay = await request(port, "/api/v1/actions/execute", { method: "POST", cookie, value: { schemaVersion: 1, actionId: preview.actionId, actionHash: preview.actionHash } });
  assert.equal(replay.status, 404);
  const fullViewResponse = await request(port, "/api/v1/deep-work/authoring", { cookie, token });
  assert.equal(fullViewResponse.status, 200);
  assert.equal(JSON.parse(fullViewResponse.body).state, "unavailable", "fixed private retention limit fails closed");
  const statusResponse = await request(port, "/api/v1/status", { cookie });
  assert.equal(statusResponse.status, 200);
  const status = JSON.parse(statusResponse.body);
  assert.equal(status.operatorActions.deepWorkDraft, false);
  assert.equal(status.operatorActions.deepWorkPrepare, false);
  assert.equal(status.operatorActions.deepWorkStage, true);
  assert.equal(status.operatorActions.deepWorkServiceRender, false);
  assert.doesNotMatch(JSON.stringify({ execution, prepareExecution, stageExecution, serviceExecution, status }), /PRIVATE HTTP|private-project|work-policy\.json|catalog\.json|authoring\.json|environment\.json|launch-control\.json|service-control\.json|pixel-control-work-draft-e2e/u);
  await privateJson(join(output, "goal-draft.json"), { ...review, unexpected: true });
  const tamperedReviewsResponse = await request(port, "/api/v1/deep-work/drafts", { cookie, token });
  assert.equal(tamperedReviewsResponse.status, 200);
  const tamperedReviews = JSON.parse(tamperedReviewsResponse.body);
  assert.equal(tamperedReviews.state, "unavailable");
  assert.deepEqual(tamperedReviews.drafts, []);
  await stop(child);
});
