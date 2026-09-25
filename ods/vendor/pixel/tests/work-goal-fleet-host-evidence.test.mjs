import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, unlink, utimes, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { canonical, validateWorkGoalFleetHostEvidence, validateWorkGoalFleetHostProbe } from "../scripts/lib/work-contract.mjs";
import { buildGoalFleetHostEvidence, generateGoalFleetHostEvidence } from "../deploy/work-controller/goal-fleet-host-evidence-cli.mjs";

const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-11T12:00:00Z");
const goalId = "workgoal-1786366800001-abcdef123456";

function fleet() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-v1.schema.json", schemaVersion: 1,
    fleetId: "workfleet-1786366801000-abcdef123456", createdAt: "2026-08-10T13:00:01.000Z",
    discipline: { mode: "durable-round-robin", maxConcurrentTurns: 1, crashPolicy: "recover-active-before-next" },
    hostCapacity: { maxTurnSeconds: 3600, maxCpuCores: 4, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, maxRetainedArtifactBytes: 16777216 },
    goals: [{
      goalId, goalSha256: digest("a"), jobsSha256: digest("b"), controllerConfigSha256: digest("c"),
      resourceEnvelope: { maxTurnSeconds: 960, maxWorkerSeconds: 600, maxVerifierSeconds: 60, maxCleanupSeconds: 300, maxCpuCores: 4, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, maxRetainedArtifactBytes: 16777216 },
    }],
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable host-fleet schedule. It serializes bounded goal turns against exact resource envelopes but grants no job execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
}

function probe(root) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-probe-v1.schema.json", schemaVersion: 1,
    fleetPath: join(root, "fleet.json"), disposableDiskPath: root,
    systemReserve: { cpuCores: 2, memoryMiB: 4096, diskBytes: 1073741824 },
    sharedServices: [{
      serviceId: "local-model", unitName: "pixel-model.service", maxCpuCores: 2, maxMemoryMiB: 4096,
      maxDiskBytes: 1073741824, diskLimitEvidenceSha256: digest("d"),
    }],
    evidenceLifetimeSeconds: 3600,
    boundary: "Owner-reviewed live host probe configuration. It names exact shared services and conservative reserves but grants no execution, lease, service mutation, scope expansion, external effect, or completion authority.",
  };
}

const host = { cpuCores: 16, memoryMiB: 32768, disposableDiskBytes: 107374182400, fingerprintSource: "private-machine-id|linux|x64|16" };
const live = [{ unitName: "pixel-model.service", properties: "LoadState=loaded\nActiveState=active\nCPUQuotaPerSecUSec=2s\nMemoryMax=4294967296\n" }];

test("live host evidence subtracts exact system and bounded shared-service capacity", () => {
  const config = probe("/var/lib/pixel-work");
  assert.deepEqual(validateWorkGoalFleetHostProbe(config), []);
  const evidence = buildGoalFleetHostEvidence({ fleet: fleet(), probe: config, host, serviceProperties: live, now: new Date(baseTime), suffix: "810000000001" });
  assert.deepEqual(validateWorkGoalFleetHostEvidence(evidence), []);
  assert.deepEqual(evidence.measurement.admitted, { cpuCores: 12, memoryMiB: 24576, disposableDiskBytes: 105226698752 });
  assert.equal(evidence.measurement.sharedServices[0].unitName, "pixel-model.service");
  assert.match(evidence.measurement.sharedServices[0].limitEvidenceSha256, /^[a-f0-9]{64}$/u);
  assert.deepEqual(Object.values(evidence.authority), [false, false, false, false, false, false]);
});

test("host evidence generation is atomic, content-free, and read-only against live services", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-fleet-host-evidence-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const config = probe(root), configPath = join(root, "probe.json"), outputPath = join(root, "0000000.json");
  await writeFile(config.fleetPath, `${JSON.stringify(fleet())}\n`, { mode: 0o600 });
  await writeFile(configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  const calls = [];
  const dependencies = {
    platform: "linux", now: new Date(baseTime), suffix: "820000000001",
    hostObserver: async (path) => { assert.equal(path, root); return host; },
    systemctlRunner: async (unitName) => {
      calls.push(unitName);
      return { code: 0, stdout: live[0].properties, stderr: "" };
    },
  };
  const receipt = await generateGoalFleetHostEvidence(["create", "--config", configPath, "--output", outputPath], dependencies);
  assert.deepEqual(calls, ["pixel-model.service"]);
  assert.equal(receipt.outputName, "0000000.json");
  assert.ok(!JSON.stringify(receipt).includes(root));
  const evidence = JSON.parse(await readFile(outputPath, "utf8"));
  assert.deepEqual(validateWorkGoalFleetHostEvidence(evidence), []);
  await assert.rejects(generateGoalFleetHostEvidence(["create", "--config", configPath, "--output", outputPath], dependencies), /already exists/);
});

test("host evidence refresh retains a fresh head and appends an exact renewal without controller drift", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-fleet-host-renewal-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"), evidenceDirectory = join(stateRoot, "host-evidence");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(evidenceDirectory, { mode: 0o700 });
  if (process.platform !== "win32") { await chmod(root, 0o700); await chmod(stateRoot, 0o700); await chmod(evidenceDirectory, 0o700); }
  const config = probe(root), configPath = join(root, "probe.json"), genesisPath = join(evidenceDirectory, "0000000.json");
  await writeFile(config.fleetPath, `${JSON.stringify(fleet())}\n`, { mode: 0o600 });
  await writeFile(configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  const dependencies = {
    platform: "linux", now: new Date(baseTime), suffix: "840000000001",
    hostObserver: async () => host,
    systemctlRunner: async () => ({ code: 0, stdout: live[0].properties, stderr: "" }),
  };
  const genesis = await generateGoalFleetHostEvidence(["create", "--config", configPath, "--output", genesisPath], dependencies);
  const controller = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-controller-v2.schema.json", schemaVersion: 2, enabled: true,
    stateRoot, fleetPath: config.fleetPath, hostProbePath: configPath, hostProbeSha256: createHash("sha256").update(canonical(config)).digest("hex"),
    hostEvidenceDirectory: evidenceDirectory, hostEvidenceGenesisSha256: genesis.evidenceSha256,
    goals: [{ goalId, controllerConfigPath: join(root, "goal-controller.json") }],
    boundary: "Owner-private fleet wiring only. It binds one immutable fleet and exact live host probe to exact per-goal controller configurations and grants no execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const controllerPath = join(root, "fleet-controller.json");
  await writeFile(controllerPath, `${JSON.stringify(controller)}\n`, { mode: 0o600 });
  let probes = 0;
  const retained = await generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], {
    ...dependencies, now: new Date(baseTime + 1000), systemctlRunner: async () => { probes += 1; throw new Error("must not probe"); },
  });
  assert.equal(retained.action, "current-evidence-retained"); assert.equal(probes, 0);
  const renewed = await generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], {
    ...dependencies, now: new Date(baseTime + 1800001), suffix: "840000000002",
  });
  assert.equal(renewed.action, "refreshed-current-evidence");
  assert.deepEqual(await readdir(evidenceDirectory), ["0000000.json", "0000001.json"]);
  const next = JSON.parse(await readFile(join(evidenceDirectory, "0000001.json"), "utf8"));
  assert.equal(next.sequence, 1); assert.equal(next.previousEvidenceSha256, genesis.evidenceSha256);

  const racing = await Promise.allSettled(Array.from({ length: 16 }, (_, index) => generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], {
    ...dependencies, now: new Date(baseTime + 3600002), suffix: (0x850000000000n + BigInt(index)).toString(16),
  })));
  assert.equal(racing.filter((attempt) => attempt.status === "fulfilled" && attempt.value.action === "refreshed-current-evidence").length, 1);
  const unexpectedRaceErrors = racing.filter((attempt) => attempt.status === "rejected" && !/sequence already exists|recovery delay has not elapsed|publication is in progress/u.test(attempt.reason.message));
  assert.deepEqual(unexpectedRaceErrors.map((attempt) => attempt.reason.message), []);
  assert.deepEqual(await readdir(evidenceDirectory), ["0000000.json", "0000001.json", "0000002.json"]);

  const headPath = join(evidenceDirectory, "0000002.json"), exactHead = await readFile(headPath);
  const tampered = JSON.parse(exactHead.toString("utf8")); tampered.hostFingerprintSha256 = digest("f");
  await writeFile(headPath, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
  await assert.rejects(generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], { ...dependencies, now: new Date(baseTime + 5400003) }), /host identity changed/u);
  await writeFile(headPath, exactHead, { mode: 0o600 });
  const hardLink = join(root, "linked-head.json"); await link(headPath, hardLink);
  await assert.rejects(generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], { ...dependencies, now: new Date(baseTime + 5400003) }), /single-link/u);
  await unlink(hardLink);

  const linkedTemporary = join(evidenceDirectory, ".host-evidence-aaaaaaaaaaaaaaaa");
  await link(headPath, linkedTemporary); await utimes(linkedTemporary, new Date(baseTime), new Date(baseTime));
  const linkedRecovery = await generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], {
    ...dependencies, now: new Date(baseTime + 3600003), publicationRecoveryMinimumAgeMs: 0,
  });
  assert.equal(linkedRecovery.action, "current-evidence-retained"); assert.equal(linkedRecovery.publicationRecoveryAction, "recovered-interrupted-publication");
  const orphanTemporary = join(evidenceDirectory, ".host-evidence-bbbbbbbbbbbbbbbb");
  await writeFile(orphanTemporary, "orphan", { mode: 0o600 }); await utimes(orphanTemporary, new Date(baseTime), new Date(baseTime));
  const orphanRecovery = await generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], {
    ...dependencies, now: new Date(baseTime + 3600004), publicationRecoveryMinimumAgeMs: 0,
  });
  assert.equal(orphanRecovery.action, "current-evidence-retained"); assert.equal(orphanRecovery.publicationRecoveryAction, "recovered-interrupted-publication");
  assert.ok((await readdir(evidenceDirectory)).every((name) => !name.startsWith(".host-evidence-")));
});

test("host evidence refuses unlimited or under-reserved live service limits", () => {
  const config = probe("/var/lib/pixel-work");
  const unlimited = [{ ...live[0], properties: live[0].properties.replace("2s", "infinity") }];
  assert.throws(() => buildGoalFleetHostEvidence({ fleet: fleet(), probe: config, host, serviceProperties: unlimited, now: new Date(baseTime), suffix: "830000000001" }), /absent, infinite, or invalid/);
  const excessiveMemory = [{ ...live[0], properties: live[0].properties.replace("4294967296", "8589934592") }];
  assert.throws(() => buildGoalFleetHostEvidence({ fleet: fleet(), probe: config, host, serviceProperties: excessiveMemory, now: new Date(baseTime), suffix: "830000000002" }), /exceeds its reviewed reserve/);
  const duplicate = structuredClone(config);
  duplicate.sharedServices.push(structuredClone(duplicate.sharedServices[0]));
  assert.ok(validateWorkGoalFleetHostProbe(duplicate).some((error) => /unique/u.test(error)));
});
