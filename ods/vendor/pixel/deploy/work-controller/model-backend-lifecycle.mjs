import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { request } from "node:http";
import { setTimeout as delay } from "node:timers/promises";

import {
  canonical, validateWorkModelBackendLifecycleReceipt, validateWorkModelBackendLifecycleReview,
} from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import {
  modelBackendIdentityLabels, validateImageInspect, validateModelBackendInspect, validateModelBackendNetworkInspect,
} from "../work-runner/docker-boundary.mjs";
import { withModelBackendCoordination } from "../work-runner/model-backend-coordination.mjs";
import {
  validateConfiguredModelBackendContainerInspect, validateConfiguredModelBackendInspect,
  validateConfiguredModelBackendNetworkInspect,
} from "./model-backend-runtime.mjs";

const ID_RE = /^[a-f0-9]{64}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const START_DIAGNOSTIC_TAIL_LINES = 400;
const START_DIAGNOSTIC_STREAM_BYTES = 128 * 1024;
const START_DIAGNOSTIC_BOUNDARY = "Owner-private bounded diagnostic from one failed exact local-model backend start. It may contain backend startup paths and messages, never grants authority, and must remain in owner custody; capture is best-effort and can never delay or weaken exact rollback.";
const reviewBoundary = "Content-free exact-confirmation review for one contained local-model lifecycle intent. The review itself changes nothing; execution must remeasure the model, revalidate the pinned image and exact private inputs, refuse foreign resources or peers, and never pull an image or grant credentials, external effects, or completion authority.";
const receiptBoundary = "Content-free evidence of one exact-confirmed contained local-model lifecycle reconciliation. It reports only completed local Docker changes and exact safety checks; it contains no private identity or content and grants no future execution, network, container, device, credential, external-effect, or completion authority.";
const privacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, acceleratorIdentifier: false, deviceIdentifiers: false, paths: false, hashes: false, credentials: false, prompts: false, responses: false });
const reviewPrivacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, acceleratorIdentifier: false, deviceIdentifiers: false, paths: false, hashesExceptConfirmation: false, credentials: false, prompts: false, responses: false });
const authority = Object.freeze({ grantsExecution: false, changesNetwork: false, startsContainer: false, stopsContainer: false, removesContainer: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const reviewAuthority = Object.freeze({ grantsExecution: false, createsNetwork: false, startsContainer: false, stopsContainer: false, removesContainer: false, removesNetwork: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });

export class WorkModelBackendLifecycleError extends Error {}
function fail(message) { throw new WorkModelBackendLifecycleError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function runtime(prepared) { return { ...prepared.environment.runtime, backendAlias: "pixel-local-model" }; }
function exactPrepared(prepared) { return { policy: prepared.policy, bindings: prepared.launch.bindings, plan: { model: { backendImageDigest: prepared.policy.localModel.imageDigest, id: prepared.policy.localModel.id } } }; }
function exactInputs(prepared) { return { configuration: prepared.configuration, environment: prepared.environment, policy: prepared.policy, artifactManifest: prepared.artifactManifest, runtimeCacheArtifactManifest: prepared.runtimeCacheArtifactManifest, launch: prepared.launch }; }

function validateCreatedRollbackTarget(container, createdContainerId, prepared) {
  const expectedLabels = modelBackendIdentityLabels({ policy: prepared.policy, bindings: prepared.launch.bindings });
  const actualLabels = container?.Config?.Labels ?? {};
  if (
    container?.Id !== createdContainerId
    || container?.Name !== `/${prepared.environment.runtime.backendContainerName}`
    || container?.Config?.Image !== prepared.policy.localModel.imageRef
    || Object.entries(expectedLabels).some(([name, value]) => actualLabels[name] !== value)
  ) fail("newly created model backend rollback target differs from the exact returned identity");
  return true;
}

export function modelBackendLifecycleSha256(intent, launchBundleSha256) {
  if (!["start", "stop"].includes(intent) || !SHA_RE.test(launchBundleSha256 ?? "")) fail("model backend lifecycle identity is invalid");
  return sha({ schemaVersion: 1, operation: `pixel-work-model-backend-${intent}`, launchBundleSha256 });
}

export function buildModelBackendLifecycleReview(intent, launchBundleSha256) {
  const lifecycleSha256 = modelBackendLifecycleSha256(intent, launchBundleSha256), start = intent === "start";
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-lifecycle-review-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-lifecycle-review", intent, status: "confirmation-required", launchBundleSha256, lifecycleSha256,
    confirmation: { option: "--confirm-lifecycle-sha256", sha256: lifecycleSha256 },
    effects: { mayCreateNetwork: start, mayCreateContainer: start, mayStartContainer: start, mayStopContainer: !start, mayRemoveContainer: !start, mayRemoveNetwork: !start, pullsImage: false, changesPolicy: false, grantsExternalEffects: false },
    privacy: { ...reviewPrivacy }, authority: { ...reviewAuthority }, boundary: reviewBoundary,
  };
  const errors = validateWorkModelBackendLifecycleReview(value);
  if (errors.length) fail(`model backend lifecycle review is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

export function execModelBackendDocker(command, args, options = {}) {
  return new Promise((resolve, reject) => execFile(command, args, {
    cwd: "/", env: options.env, encoding: "utf8", windowsHide: true, shell: false,
    timeout: options.timeoutMs ?? 60000, maxBuffer: options.maxBuffer ?? 4 * 1024 * 1024,
  }, (error, stdout, stderr) => {
    if (error) {
      const failure = new WorkModelBackendLifecycleError("model backend Docker operation failed closed");
      failure.exitCode = error.code; failure.stderr = typeof stderr === "string" ? stderr : ""; reject(failure); return;
    }
    resolve({ stdout: stdout ?? "", stderr: stderr ?? "" });
  }));
}

async function invoke(executor, command, args, env, timeoutMs = 60000, maxBuffer = 4 * 1024 * 1024) {
  if (typeof executor !== "function" || !Array.isArray(args) || !args.every((value) => typeof value === "string" && !value.includes("\0"))) fail("model backend Docker executor input is invalid");
  const result = await executor(command, args, { env, timeoutMs, maxBuffer });
  if (!result || typeof result.stdout !== "string" || typeof result.stderr !== "string") fail("model backend Docker executor returned an invalid result");
  return result;
}

function boundedDiagnosticText(value, maximum = START_DIAGNOSTIC_STREAM_BYTES) {
  const original = Buffer.from(typeof value === "string" ? value : "", "utf8");
  let start = Math.max(0, original.length - maximum);
  while (start < original.length && (original[start] & 0xc0) === 0x80) start += 1;
  const retained = original.subarray(start);
  return {
    text: retained.toString("utf8"), bytes: retained.length,
    sha256: createHash("sha256").update(retained).digest("hex"), truncated: retained.length !== original.length,
  };
}

function privateStateText(value, maximum) { return boundedDiagnosticText(typeof value === "string" ? value : "", maximum).text; }

async function captureModelBackendStartDiagnostic(prepared, container, executor, command, env, primaryError) {
  let captureStatus = "captured", observed = { stdout: "", stderr: "" };
  try {
    observed = await invoke(
      executor, command, ["container", "logs", "--tail", String(START_DIAGNOSTIC_TAIL_LINES), "--timestamps", container.Id],
      env, 30000, 4 * START_DIAGNOSTIC_STREAM_BYTES,
    );
  } catch { captureStatus = "unavailable"; }
  const stdout = boundedDiagnosticText(observed.stdout), stderr = boundedDiagnosticText(observed.stderr), state = container.State ?? {};
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-backend-start-diagnostic",
    launchBundleSha256: prepared.launchBundleSha256, containerId: container.Id,
    failure: {
      class: privateStateText(primaryError?.name, 128) || "Error",
      message: privateStateText(primaryError?.message, 4096) || "model backend start failed",
    },
    state: {
      status: privateStateText(state.Status, 128), running: state.Running === true, restarting: state.Restarting === true,
      oomKilled: state.OOMKilled === true, exitCode: Number.isSafeInteger(state.ExitCode) ? state.ExitCode : null,
      error: privateStateText(state.Error, 4096), startedAt: privateStateText(state.StartedAt, 128), finishedAt: privateStateText(state.FinishedAt, 128),
    },
    logs: { captureStatus, tailLines: START_DIAGNOSTIC_TAIL_LINES, stdout, stderr },
    privacy: { ownerPrivateRequired: true, mayContainPaths: true, mayContainBackendMessages: true, promptsOrResponsesExpected: false, credentialsExpected: false },
    authority: { grantsExecution: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: START_DIAGNOSTIC_BOUNDARY,
  });
}

function attachModelBackendStartDiagnostic(error, diagnostic) {
  if (!error || typeof error !== "object") return error;
  try { Object.defineProperty(error, "modelBackendStartDiagnostic", { value: diagnostic, configurable: true }); } catch {}
  return error;
}

export async function inspectModelBackendDockerMaybe(executor, command, env, kind, target) {
  try {
    const { stdout } = await invoke(executor, command, [kind, "inspect", target], env, 30000);
    let decoded;
    try { decoded = parseStrictJson(stdout, `model backend Docker ${kind} inspection`); } catch { fail(`model backend Docker ${kind} inspection is not strict JSON`); }
    if (!Array.isArray(decoded) || decoded.length !== 1 || !decoded[0] || typeof decoded[0] !== "object" || Array.isArray(decoded[0])) fail(`model backend Docker ${kind} inspection is not singular`);
    return decoded[0];
  } catch (error) {
    if (error instanceof WorkModelBackendLifecycleError && error.exitCode === 1
      && /(?:no such (?:object|container|network|image)|(?:container|network|image) .* not found)/iu.test(error.stderr ?? "")) return null;
    throw error;
  }
}

function returnedId(result, label) {
  const value = result.stdout.trim();
  if (!ID_RE.test(value)) fail(`${label} did not return one full Docker identity`);
  return value;
}

function requireImage(image, prepared) {
  if (!image) fail("the exact pinned local-model image is unavailable; lifecycle never pulls images");
  validateImageInspect(image, prepared.policy.localModel.imageRef, prepared.policy.localModel.imageDigest);
  return image;
}

function validateEmptyNetwork(network, prepared) {
  validateModelBackendNetworkInspect(network, runtime(prepared), []);
  validateConfiguredModelBackendNetworkInspect(network, null, exactInputs(prepared), { requireAddress: false, requireAttachment: false });
  return true;
}

function validateDormant(container, image, network, prepared) {
  validateConfiguredModelBackendContainerInspect(container, image, exactInputs(prepared));
  if (container.State?.Running !== false || container.State?.Paused === true || container.State?.Restarting === true || container.State?.Dead === true || !["created", "exited"].includes(container.State?.Status)) fail("model backend container is not in an exact restartable dormant state");
  validateModelBackendNetworkInspect(network, runtime(prepared), []);
  validateConfiguredModelBackendNetworkInspect(network, container, exactInputs(prepared), { requireAddress: false });
  return true;
}

function validateRunning(container, image, network, prepared, requireEffectivePorts = true) {
  validateModelBackendInspect(container, image, exactPrepared(prepared), runtime(prepared));
  validateModelBackendNetworkInspect(network, runtime(prepared), [{ id: container.Id, name: prepared.environment.runtime.backendContainerName }]);
  validateConfiguredModelBackendInspect(container, image, network, exactInputs(prepared), { requireEffectivePorts });
  return true;
}

export function probeModelBackendHealth({ host, port = 8080, timeoutMs = 2000 }) {
  return new Promise((resolve) => {
    const parts = typeof host === "string" && /^(?:0|[1-9][0-9]{0,2})(?:\.(?:0|[1-9][0-9]{0,2})){3}$/u.test(host) ? host.split(".").map(Number) : [];
    if (parts.length !== 4 || parts.some((part) => part > 255) || !Number.isSafeInteger(port) || port < 1 || port > 65535 || !Number.isFinite(timeoutMs) || timeoutMs < 1 || timeoutMs > 2000) { resolve(false); return; }
    let settled = false, bytes = 0;
    const finish = (value) => { if (!settled) { settled = true; resolve(value); } };
    const operation = request({ host, port, path: "/health", method: "GET", agent: false, timeout: timeoutMs, headers: { Accept: "application/json", Connection: "close" } }, (response) => {
      const declared = Number(response.headers["content-length"]);
      if (Number.isFinite(declared) && declared > 8192) { operation.destroy(); finish(false); return; }
      response.on("data", (chunk) => { bytes += chunk.length; if (bytes > 8192) operation.destroy(); });
      response.on("end", () => finish(response.statusCode === 200 && bytes <= 8192));
      response.on("aborted", () => finish(false)); response.on("error", () => finish(false));
    });
    operation.on("timeout", () => operation.destroy()); operation.on("error", () => finish(false)); operation.end();
  });
}

function receipt(intent, state, changes, resumedPartialState, now, readinessPassed) {
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-lifecycle-receipt-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-lifecycle", intent, generatedAt: now.toISOString(), state,
    checks: { artifactBound: true, launchBound: true, imageBound: true, runtimeBound: true, readinessPassed, noUnexpectedPeers: true, postflightBound: true },
    changes: { ...changes, imagePulled: false }, recovery: { resumedPartialState, rollbackAttempted: false, rollbackComplete: false },
    privacy: { ...privacy }, authority: { ...authority }, boundary: receiptBoundary,
  };
  const errors = validateWorkModelBackendLifecycleReceipt(value);
  if (errors.length) fail(`model backend lifecycle receipt is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

async function awaitReady(prepared, image, inspect, probe, sleeper, monotonic) {
  const timeoutMs = prepared.configuration.readiness.startupTimeoutSeconds * 1000;
  const tick = () => { const value = monotonic(); if (!Number.isFinite(value) || value < 0) fail("model backend monotonic clock is invalid"); return value; };
  const deadline = tick() + timeoutMs;
  const attempts = Math.ceil(timeoutMs / prepared.configuration.readiness.probeIntervalMilliseconds);
  let readinessObservedWithoutExactRuntime = 0;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (tick() >= deadline) break;
    const container = await inspect("container", prepared.environment.runtime.backendContainerName);
    const network = await inspect("network", prepared.environment.runtime.backendNetworkName);
    if (!container || !network) fail("model backend disappeared during readiness observation");
    if (container.State?.Running === true) {
      // Docker can report a running container before its effective loopback
      // publication appears in NetworkSettings. Validate every other exact
      // boundary immediately, then require the publication before accepting
      // the independently observed readiness event.
      validateRunning(container, image, network, prepared, false);
      const healthTest = image.Config?.Healthcheck?.Test;
      let readinessPassed = false;
      if (Array.isArray(healthTest) && healthTest.length > 0 && healthTest[0] !== "NONE") {
        readinessPassed = container.State?.Health?.Status === "healthy";
        if (container.State?.Health?.Status === "unhealthy") fail("model backend reported an unhealthy readiness event");
      } else {
        const attachment = container.NetworkSettings.Networks[prepared.environment.runtime.backendNetworkName];
        const published = prepared.configuration.publishLoopbackPort;
        const expectedPublication = published === null ? null : [{ HostIp: "127.0.0.1", HostPort: String(published) }];
        const effectivePublication = published !== null
          && canonical(container.NetworkSettings?.Ports?.["8080/tcp"] ?? null) === canonical(expectedPublication);
        // An internal-only Docker bridge may retain HostConfig.PortBindings
        // while silently omitting the effective host mapping. Probe the exact
        // private attachment in that state only to diagnose backend readiness;
        // it can never satisfy a declared loopback-publication contract.
        const host = published === null || !effectivePublication ? attachment.IPAddress : "127.0.0.1";
        const port = published === null || !effectivePublication ? 8080 : published;
        const remaining = Math.max(1, deadline - tick());
        readinessPassed = await probe({ host, port, timeoutMs: Math.min(2000, prepared.configuration.readiness.probeIntervalMilliseconds, remaining) });
      }
      if (readinessPassed) {
        try { validateRunning(container, image, network, prepared, true); return { container, network }; }
        catch {
          readinessObservedWithoutExactRuntime += 1;
          if (readinessObservedWithoutExactRuntime >= 2) fail("model backend readiness was observed but requested loopback port was not effectively bound by Docker");
        }
      } else readinessObservedWithoutExactRuntime = 0;
    } else if (container.State?.Restarting !== true) {
      validateDormant(container, image, network, prepared);
      fail("model backend stopped before reporting its ready event");
    }
    const remaining = deadline - tick();
    if (attempt + 1 < attempts && remaining > 0) await sleeper(Math.min(prepared.configuration.readiness.probeIntervalMilliseconds, remaining));
  }
  if (readinessObservedWithoutExactRuntime > 0) fail("model backend readiness was observed but requested loopback port was not effectively bound by Docker");
  fail("model backend readiness event was not observed before its safety deadline");
}

async function exactPostflight(postflight, expectedSha256) {
  if (typeof postflight !== "function") fail("model backend lifecycle postflight revalidation is unavailable");
  const result = await postflight();
  if (!result || result.launchBundleSha256 !== expectedSha256) fail("model backend private inputs changed during lifecycle reconciliation");
}

async function exactRunningPostflight(prepared, image, inspect) {
  const container = await inspect("container", prepared.environment.runtime.backendContainerName);
  const network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  if (!container || !network) fail("model backend disappeared during final runtime attestation");
  validateRunning(container, image, network, prepared);
}

function emptyChanges() { return { networkCreated: false, containerCreated: false, containerStarted: false, containerStopped: false, containerRemoved: false, networkRemoved: false }; }

async function coordinated(prepared, options, operation) {
  const coordinator = options.coordinator ?? withModelBackendCoordination;
  if (typeof coordinator !== "function" || typeof prepared?.environment?.stateRoot !== "string") fail("model backend lifecycle coordination is unavailable");
  return coordinator(prepared.environment.stateRoot, operation, options.coordinationOptions ?? {});
}

export async function startModelBackend(prepared, options = {}) {
  return coordinated(prepared, options, () => startModelBackendUncoordinated(prepared, options));
}

async function startModelBackendUncoordinated(prepared, { confirmation, executor = execModelBackendDocker, inspector, readinessProbe = probeModelBackendHealth, sleeper = delay, monotonic = () => performance.now(), postflight, now = new Date() } = {}) {
  const expected = modelBackendLifecycleSha256("start", prepared?.launchBundleSha256);
  if (confirmation !== expected || !(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || typeof monotonic !== "function") fail("model backend start confirmation or clock is invalid");
  const command = prepared.launch.container.command, env = prepared.launch.container.env;
  const inspect = inspector ?? ((kind, target) => inspectModelBackendDockerMaybe(executor, command, env, kind, target));
  const changes = emptyChanges(); let createdContainerId = null, createdNetworkId = null, startedExisting = false, resumedPartialState = false;
  const image = requireImage(await inspect("image", prepared.policy.localModel.imageRef), prepared);
  let network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  let container = await inspect("container", prepared.environment.runtime.backendContainerName);
  try {
    if (container && !network) fail("an exact-named model backend container exists without its private network");
    if (network && !container) { validateEmptyNetwork(network, prepared); resumedPartialState = true; }
    if (container) {
      if (container.State?.Running === true) {
        validateRunning(container, image, network, prepared, false);
        await awaitReady(prepared, image, inspect, readinessProbe, sleeper, monotonic);
        await exactPostflight(postflight, prepared.launchBundleSha256);
        await exactRunningPostflight(prepared, image, inspect);
        return receipt("start", "ready-already-running", changes, false, now, true);
      }
      validateDormant(container, image, network, prepared); resumedPartialState = true;
    }
    if (!network) {
      let result = null;
      try { result = await invoke(executor, prepared.launch.network.command, prepared.launch.network.args, prepared.launch.network.env); }
      catch (error) { network = await inspect("network", prepared.environment.runtime.backendNetworkName); if (!network) throw error; resumedPartialState = true; }
      if (result) { createdNetworkId = returnedId(result, "model backend network creation"); changes.networkCreated = true; }
      network = await inspect("network", prepared.environment.runtime.backendNetworkName);
      if (!network || createdNetworkId && network.Id !== createdNetworkId) fail("created model backend network identity differs from Docker");
      validateEmptyNetwork(network, prepared);
    }
    if (!container) {
      let result = null;
      try { result = await invoke(executor, prepared.launch.container.command, prepared.launch.container.args, prepared.launch.container.env); }
      catch (error) { container = await inspect("container", prepared.environment.runtime.backendContainerName); if (!container) throw error; resumedPartialState = true; }
      if (result) { createdContainerId = returnedId(result, "model backend container creation"); changes.containerCreated = true; }
      container = await inspect("container", prepared.environment.runtime.backendContainerName);
      network = await inspect("network", prepared.environment.runtime.backendNetworkName);
      if (!container || createdContainerId && container.Id !== createdContainerId) fail("created model backend container identity differs from Docker");
      validateDormant(container, image, network, prepared);
    }
    await invoke(executor, command, ["container", "start", container.Id], env, 60000);
    changes.containerStarted = true; startedExisting = createdContainerId === null;
    await awaitReady(prepared, image, inspect, readinessProbe, sleeper, monotonic);
    await exactPostflight(postflight, prepared.launchBundleSha256);
    await exactRunningPostflight(prepared, image, inspect);
    return receipt("start", "ready-started", changes, resumedPartialState, now, true);
  } catch (error) {
    try {
      const diagnosticTarget = createdContainerId ?? (startedExisting ? prepared.environment.runtime.backendContainerName : null);
      if (diagnosticTarget) {
        const current = await inspect("container", diagnosticTarget);
        if (current) {
          if (createdContainerId) validateCreatedRollbackTarget(current, createdContainerId, prepared);
          else validateConfiguredModelBackendContainerInspect(current, image, exactInputs(prepared), { requireEffectivePorts: false });
          attachModelBackendStartDiagnostic(error, await captureModelBackendStartDiagnostic(prepared, current, executor, command, env, error));
        }
      }
    } catch {}
    try {
      if (createdContainerId) {
        const current = await inspect("container", createdContainerId);
        if (current) { validateCreatedRollbackTarget(current, createdContainerId, prepared); await invoke(executor, command, ["container", "rm", "--force", createdContainerId], env, 60000); }
      } else if (startedExisting) {
        const current = await inspect("container", prepared.environment.runtime.backendContainerName);
        if (current?.State?.Running === true) { validateConfiguredModelBackendContainerInspect(current, image, exactInputs(prepared), { requireEffectivePorts: false }); await invoke(executor, command, ["container", "stop", "--time", "30", current.Id], env, 60000); }
      }
      const containerRemains = await inspect("container", prepared.environment.runtime.backendContainerName);
      if (createdNetworkId && !containerRemains) {
        const current = await inspect("network", createdNetworkId);
        if (current) { validateEmptyNetwork(current, prepared); await invoke(executor, command, ["network", "rm", createdNetworkId], env, 60000); }
      }
    } catch {
      const rollbackFailure = new WorkModelBackendLifecycleError("model backend start failed and exact rollback could not be completed");
      if (error?.modelBackendStartDiagnostic) attachModelBackendStartDiagnostic(rollbackFailure, error.modelBackendStartDiagnostic);
      throw rollbackFailure;
    }
    throw error;
  }
}

export async function stopModelBackend(prepared, options = {}) {
  return coordinated(prepared, options, () => stopModelBackendUncoordinated(prepared, options));
}

async function stopModelBackendUncoordinated(prepared, { confirmation, executor = execModelBackendDocker, inspector, postflight, now = new Date() } = {}) {
  const expected = modelBackendLifecycleSha256("stop", prepared?.launchBundleSha256);
  if (confirmation !== expected || !(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("model backend stop confirmation or clock is invalid");
  const command = prepared.launch.container.command, env = prepared.launch.container.env;
  const inspect = inspector ?? ((kind, target) => inspectModelBackendDockerMaybe(executor, command, env, kind, target));
  const changes = emptyChanges();
  const image = requireImage(await inspect("image", prepared.policy.localModel.imageRef), prepared);
  let network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  let container = await inspect("container", prepared.environment.runtime.backendContainerName);
  if (!network && !container) { await exactPostflight(postflight, prepared.launchBundleSha256); return receipt("stop", "already-absent", changes, false, now, false); }
  if (container && !network) fail("an exact-named model backend container exists without its private network");
  const resumedPartialState = !container || container.State?.Running !== true;
  if (container?.State?.Running === true) validateRunning(container, image, network, prepared, false);
  else if (container) validateDormant(container, image, network, prepared);
  else validateEmptyNetwork(network, prepared);
  await exactPostflight(postflight, prepared.launchBundleSha256);
  network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  container = await inspect("container", prepared.environment.runtime.backendContainerName);
  if (!network) fail("model backend changed during stop preflight");
  if (container?.State?.Running === true) validateRunning(container, image, network, prepared, false);
  else if (container) validateDormant(container, image, network, prepared);
  else validateEmptyNetwork(network, prepared);
  if (container?.State?.Running === true) {
    await invoke(executor, command, ["container", "stop", "--time", "30", container.Id], env, 60000); changes.containerStopped = true;
    container = await inspect("container", container.Id); network = await inspect("network", prepared.environment.runtime.backendNetworkName);
    if (!container || !network) fail("model backend disappeared inconsistently after stop");
  }
  if (container) {
    validateDormant(container, image, network, prepared);
    await invoke(executor, command, ["container", "rm", container.Id], env, 60000); changes.containerRemoved = true;
    if (await inspect("container", container.Id)) fail("model backend container remains after exact removal");
  } else validateEmptyNetwork(network, prepared);
  network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  if (network) {
    validateEmptyNetwork(network, prepared);
    await invoke(executor, command, ["network", "rm", network.Id], env, 60000); changes.networkRemoved = true;
    if (await inspect("network", network.Id)) fail("model backend network remains after exact removal");
  }
  return receipt("stop", "removed", changes, resumedPartialState, now, false);
}
