import { createHash } from "node:crypto";

import {
  canonical, validateWorkModelBackendHaltReceipt, validateWorkModelBackendHaltReview,
} from "../../scripts/lib/work-contract.mjs";
import {
  validateModelBackendHaltIdentity, validateModelBackendHaltNetworkIdentity,
} from "../work-runner/docker-boundary.mjs";
import { withModelBackendCoordination } from "../work-runner/model-backend-coordination.mjs";
import { execModelBackendDocker, inspectModelBackendDockerMaybe } from "./model-backend-lifecycle.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const reviewBoundary = "Content-free exact-confirmation review for a stop-only emergency halt of one statically identity-bound Pixel model backend. It reads no model bytes, requires no image, retains all resources for forensics, and grants no future authority.";
const receiptBoundary = "Content-free evidence of one exact-confirmed stop-only emergency backend halt. It reads no model bytes, requires no image, retains the container and network for forensics, removes nothing, and grants no future authority.";
const reviewPrivacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, deviceIdentifiers: false, paths: false, hashesExceptConfirmation: false, credentials: false, prompts: false, responses: false });
const receiptPrivacy = Object.freeze({ modelIdentifier: false, backendIdentifier: false, deviceIdentifiers: false, paths: false, hashes: false, credentials: false, prompts: false, responses: false });
const reviewAuthority = Object.freeze({ grantsExecution: false, startsContainer: false, stopsContainer: false, removesContainer: false, removesNetwork: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const receiptAuthority = Object.freeze({ grantsExecution: false, changesNetwork: false, startsContainer: false, stopsContainer: false, removesContainer: false, pullsImage: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });

export class WorkModelBackendHaltError extends Error {}
function fail(message) { throw new WorkModelBackendHaltError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function runtime(prepared) { return { ...prepared.environment.runtime, backendAlias: "pixel-local-model" }; }
function identityPrepared(prepared) { return { policy: prepared.policy, bindings: prepared.staticIdentity.bindings }; }

export function modelBackendStaticIdentitySha256(staticIdentity) {
  if (!staticIdentity || staticIdentity.operation !== "pixel-work-model-backend-static-identity") fail("model backend static identity is invalid");
  return sha(staticIdentity);
}

async function observe(prepared, inspect) {
  const targetRuntime = runtime(prepared), identity = identityPrepared(prepared);
  const container = await inspect("container", prepared.environment.runtime.backendContainerName);
  const network = await inspect("network", prepared.environment.runtime.backendNetworkName);
  if (!container) {
    const networkSnapshotSha256 = sha(network ? { id: network.Id ?? null, name: network.Name ?? null, containers: network.Containers ?? null } : null);
    return {
      container: null, network, state: "absent", identityBound: false, hardenedBoundaryObserved: false,
      privateNetworkBound: false, activeWorkerPeersObserved: Boolean(network && Object.keys(network.Containers ?? {}).length),
      fingerprint: sha({ staticIdentitySha256: prepared.staticIdentitySha256, container: null, networkSnapshotSha256 }),
    };
  }
  const target = validateModelBackendHaltIdentity(container, identity, targetRuntime);
  let privateNetworkBound = false, peerIds = [];
  if (network) try {
    const observed = validateModelBackendHaltNetworkIdentity(network, identity, targetRuntime);
    privateNetworkBound = observed.privateBoundaryObserved && observed.peerSetValid;
    peerIds = observed.peerIds;
  } catch { peerIds = Object.keys(network.Containers ?? {}).filter((id) => /^[a-f0-9]{64}$/u.test(id)).sort(); }
  const activeWorkerPeersObserved = peerIds.some((id) => id !== container.Id)
    || Boolean(network && Object.keys(network.Containers ?? {}).some((id) => id !== container.Id && !/^[a-f0-9]{64}$/u.test(id)));
  const networkSnapshotSha256 = sha(network ? { id: network.Id ?? null, name: network.Name ?? null, containers: network.Containers ?? null } : null);
  return {
    container, network, state: target.running ? "running" : "already-halted", identityBound: true,
    hardenedBoundaryObserved: target.hardenedBoundaryObserved,
    privateNetworkBound: privateNetworkBound && target.expectedPrivateNetworkObserved,
    activeWorkerPeersObserved,
    fingerprint: sha({ staticIdentitySha256: prepared.staticIdentitySha256, containerId: container.Id, running: target.running, networkSnapshotSha256 }),
  };
}

function haltSha256(prepared, observation) {
  if (!SHA_RE.test(prepared?.staticIdentitySha256 ?? "") || !SHA_RE.test(observation?.fingerprint ?? "")) fail("model backend halt identity is invalid");
  return sha({ schemaVersion: 1, operation: "pixel-work-model-backend-halt", staticIdentitySha256: prepared.staticIdentitySha256, observationSha256: observation.fingerprint });
}

function publicObservation(observation) {
  return {
    state: observation.state, identityBound: observation.identityBound,
    hardenedBoundaryObserved: observation.hardenedBoundaryObserved,
    privateNetworkBound: observation.privateNetworkBound,
    activeWorkerPeersObserved: observation.activeWorkerPeersObserved,
  };
}

export async function buildModelBackendHaltReview(prepared, { executor = execModelBackendDocker, inspector } = {}) {
  const command = prepared.staticIdentity.dockerPath;
  const env = { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" };
  const inspect = inspector ?? ((kind, target) => inspectModelBackendDockerMaybe(executor, command, env, kind, target));
  const observation = await observe(prepared, inspect), confirmation = haltSha256(prepared, observation);
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-halt-review-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-halt-review", status: "confirmation-required", haltSha256: confirmation,
    confirmation: { option: "--confirm-halt-sha256", sha256: confirmation }, observation: publicObservation(observation),
    effects: {
      mayStopContainer: observation.state === "running", mayInterruptActiveWorkers: observation.activeWorkerPeersObserved,
      retainsContainer: true, retainsNetwork: true, readsModelBytes: false, requiresImage: false, removesResources: false,
      pullsImage: false, changesPolicy: false, grantsExternalEffects: false,
    },
    privacy: { ...reviewPrivacy }, authority: { ...reviewAuthority }, boundary: reviewBoundary,
  };
  const errors = validateWorkModelBackendHaltReview(value);
  if (errors.length) fail(`model backend halt review is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

async function invokeStop(executor, command, env, containerId) {
  if (typeof executor !== "function") fail("model backend halt Docker executor is invalid");
  const result = await executor(command, ["container", "stop", "--time", "30", containerId], { env, timeoutMs: 60000, maxBuffer: 4 * 1024 * 1024 });
  if (!result || typeof result.stdout !== "string" || typeof result.stderr !== "string") fail("model backend halt Docker executor returned an invalid result");
}

function haltReceipt(state, observation, now) {
  const absent = state === "already-absent";
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-halt-receipt-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-backend-halt", generatedAt: now.toISOString(), state,
    checks: { staticIdentityBound: !absent, reviewObservationBound: true, coordinationHeld: true, postflightBound: true, modelBytesRead: false, imageRequired: false, forensicResourcesRetained: !absent },
    observation: {
      hardenedBoundaryObserved: observation.hardenedBoundaryObserved,
      privateNetworkBound: observation.privateNetworkBound,
      activeWorkerPeersObserved: observation.activeWorkerPeersObserved,
    },
    changes: { containerStopped: state === "halted", containerRemoved: false, networkRemoved: false, imagePulled: false },
    privacy: { ...receiptPrivacy }, authority: { ...receiptAuthority }, boundary: receiptBoundary,
  };
  const errors = validateWorkModelBackendHaltReceipt(value);
  if (errors.length) fail(`model backend halt receipt is invalid: ${errors[0]}`);
  return Object.freeze(value);
}

export async function haltModelBackend(prepared, options = {}) {
  const coordinator = options.coordinator ?? withModelBackendCoordination;
  if (typeof coordinator !== "function") fail("model backend halt coordination is unavailable");
  return coordinator(prepared.environment.stateRoot, () => haltModelBackendCoordinated(prepared, options), options.coordinationOptions ?? {});
}

async function haltModelBackendCoordinated(prepared, { confirmation, executor = execModelBackendDocker, inspector, postflight, now = new Date() } = {}) {
  if (!SHA_RE.test(confirmation ?? "") || !(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || typeof postflight !== "function") fail("model backend halt confirmation, clock, or postflight is invalid");
  const command = prepared.staticIdentity.dockerPath;
  const env = { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" };
  const inspect = inspector ?? ((kind, target) => inspectModelBackendDockerMaybe(executor, command, env, kind, target));
  let observation = await observe(prepared, inspect);
  if (haltSha256(prepared, observation) !== confirmation) fail("confirmation differs from the current emergency halt observation");
  let measured = await postflight();
  if (!measured || measured.staticIdentitySha256 !== prepared.staticIdentitySha256) fail("model backend static private inputs changed during emergency halt");
  observation = await observe(prepared, inspect);
  if (haltSha256(prepared, observation) !== confirmation) fail("model backend runtime changed during emergency halt preflight");
  if (observation.state === "absent") return haltReceipt("already-absent", observation, now);
  if (observation.state === "already-halted") return haltReceipt("already-halted", observation, now);
  try { await invokeStop(executor, command, env, observation.container.Id); }
  catch (error) {
    const reconciled = await observe(prepared, inspect);
    if (reconciled.state !== "already-halted") throw error;
  }
  const halted = await observe(prepared, inspect);
  if (halted.state !== "already-halted" || halted.container?.Id !== observation.container.Id) fail("emergency halt did not retain the exact stopped backend");
  measured = await postflight();
  if (!measured || measured.staticIdentitySha256 !== prepared.staticIdentitySha256) fail("model backend static private inputs changed after emergency halt");
  return haltReceipt("halted", observation, now);
}
