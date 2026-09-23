import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import { renderGoalFleetServiceUnits } from "../deploy/work-controller/goal-fleet-service-units.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const run = promisify(execFile);
const digest = (character) => character.repeat(64);
const goalId = "workgoal-1786366800001-abcdef123456";
const fleetId = "workfleet-1786366801000-abcdef123456";

function fleet() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-v1.schema.json", schemaVersion: 1, fleetId, createdAt: "2026-08-10T13:00:01.000Z",
    discipline: { mode: "durable-round-robin", maxConcurrentTurns: 1, crashPolicy: "recover-active-before-next" },
    hostCapacity: { maxTurnSeconds: 3600, maxCpuCores: 8, maxMemoryMiB: 16384, maxDiskBytes: 10737418240, maxRetainedArtifactBytes: 16777216 },
    goals: [{
      goalId, goalSha256: digest("a"), jobsSha256: digest("b"), controllerConfigSha256: digest("c"),
      resourceEnvelope: { maxTurnSeconds: 960, maxWorkerSeconds: 600, maxVerifierSeconds: 60, maxCleanupSeconds: 300, maxCpuCores: 4, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, maxRetainedArtifactBytes: 16777216 },
    }],
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable host-fleet schedule. It serializes bounded goal turns against exact resource envelopes but grants no job execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
}

function inputs(root = "/var/lib/pixel-work") {
  const controllerConfigPath = "/etc/pixel-work/goals/controller.json";
  const hostProbe = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-probe-v1.schema.json", schemaVersion: 1,
    fleetPath: "/etc/pixel-work/fleet.json", disposableDiskPath: `${root}/disposable`,
    systemReserve: { cpuCores: 2, memoryMiB: 4096, diskBytes: 1073741824 },
    sharedServices: [{ serviceId: "local-model", unitName: "pixel-model.service", maxCpuCores: 2, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, diskLimitEvidenceSha256: digest("d") }],
    evidenceLifetimeSeconds: 3600,
    boundary: "Owner-reviewed live host probe configuration. It names exact shared services and conservative reserves but grants no execution, lease, service mutation, scope expansion, external effect, or completion authority.",
  };
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-controller-v2.schema.json", schemaVersion: 2, enabled: true,
    stateRoot: `${root}/state`, fleetPath: "/etc/pixel-work/fleet.json", hostProbePath: "/etc/pixel-work/host-probe.json",
    hostProbeSha256: createHash("sha256").update(canonical(hostProbe)).digest("hex"), hostEvidenceDirectory: `${root}/state/host-evidence`, hostEvidenceGenesisSha256: digest("e"), goals: [{ goalId, controllerConfigPath }],
    boundary: "Owner-private fleet wiring only. It binds one immutable fleet and exact live host probe to exact per-goal controller configurations and grants no execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const controller = {
    enabled: true, stateRoot: config.stateRoot, goalPath: "/etc/pixel-work/goals/goal.json", jobsPath: "/etc/pixel-work/goals/jobs.json",
    policyPath: "/etc/pixel-work/goals/policy.json", objectStore: `${root}/objects`, workspaceRoot: `${root}/workspaces/one`,
    executorPath: "/opt/omp/bin/omp", controller: { maxTransitions: 1 }, runtime: { dockerPath: "/usr/bin/docker" },
  };
  return { configPath: "/etc/pixel-work/fleet-controller.json", config, fleet: fleet(), hostProbe, goalControllers: [{ goalId, configPath: controllerConfigPath, config: controller }], installRoot: "/opt/pixel" };
}

test("fleet service uses one lock for cycle and exact stop cleanup and blocks legacy timers", () => {
  const value = renderGoalFleetServiceUnits(inputs());
  assert.equal(value.serviceName, `pixel-work-${fleetId}.service`);
  for (const anchor of [
    `--wait 30 /run/pixel-work-${fleetId}/cycle.lock /usr/bin/node /opt/pixel/deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs --config`,
    `--wait 300 /run/pixel-work-${fleetId}/cycle.lock /usr/bin/node /opt/pixel/deploy/work-controller/goal-fleet-cleanup-cli.mjs`,
    `ConditionPathExists=!/etc/systemd/system/pixel-work-${goalId}.service`,
    `ConditionPathExists=!/etc/systemd/system/pixel-work-${goalId}.timer`,
    "NoNewPrivileges=true", "PrivateNetwork=true", "ProtectSystem=strict", "TimeoutStartSec=3660s", "TimeoutStopSec=5min",
  ]) assert.match(value.service, new RegExp(anchor.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")));
  assert.match(value.timer, /OnUnitInactiveSec=30s/u);
  assert.deepEqual(value.legacyUnitNames, [`pixel-work-${goalId}.service`, `pixel-work-${goalId}.timer`]);
  assert.match(value.boundary, /supported-host endurance remains a release gate/u);
  assert.doesNotMatch(`${value.service}\n${value.timer}`, /private objective|aaaaaaaaaaaaaaaa/u);
});

test("fleet service rejects overlapping custody, privileged identity, and unsafe cadence", () => {
  const base = inputs();
  assert.throws(() => renderGoalFleetServiceUnits({ ...base, serviceUser: "root" }), /privileged/);
  assert.throws(() => renderGoalFleetServiceUnits({ ...base, configPath: "/home/operator/fleet.json" }), /ProtectHome/);
  const overlap = structuredClone(base);
  overlap.goalControllers[0].config.workspaceRoot = `${base.config.stateRoot}/workspaces`;
  assert.throws(() => renderGoalFleetServiceUnits(overlap), /disjoint/);
  assert.throws(() => renderGoalFleetServiceUnits({ ...base, intervalSeconds: 1 }), /interval/);
});

test("generated fleet service and timer pass systemd verification", { skip: process.platform !== "linux" }, async (t) => {
  // The fixture installs into a live-looking root under ProtectHome protection, so the temp
  // root must never live under /home (a host with TMPDIR under /home would otherwise trip the
  // ProtectHome guard for an environment reason). Fall back to /tmp to keep the gate green.
  const forbiddenPrivateRoots = ["/home", "/root", "/run/user"];
  const base = forbiddenPrivateRoots.some((root) => tmpdir() === root || tmpdir().startsWith(`${root}/`)) ? "/tmp" : tmpdir();
  const root = await mkdtemp(join(base, "pixel-goal-fleet-systemd-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  // Keep the fixture compatible with ProtectHome even when the checkout itself
  // lives below /home on a supported Linux host.
  const installRoot = root.replaceAll("\\", "/");
  const value = renderGoalFleetServiceUnits({ ...inputs("/var/lib/pixel-work"), installRoot, nodePath: "/usr/bin/node" });
  const servicePath = join(root, value.serviceName);
  const timerPath = join(root, value.timerName);
  await writeFile(servicePath, value.service, { mode: 0o600 });
  await writeFile(timerPath, value.timer, { mode: 0o600 });
  await chmod(servicePath, 0o600);
  await run("systemd-analyze", ["verify", servicePath, timerPath], { timeout: 30000 });
});
