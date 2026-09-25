import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import { renderGoalServiceUnits } from "../deploy/work-controller/goal-service-units.mjs";

const run = promisify(execFile);
const digest = (character) => character.repeat(64);

function goal() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Complete the supervised fixture.", dataClassification: "internal",
    milestones: [{ milestoneId: "build", jobId: "work-1786366800000-abcdef123456", jobSha256: digest("a"), profile: "builder", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 600, maxModelRequests: 10, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 16777216, maxFailures: 2 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

function config(root) {
  return {
    enabled: true,
    stateRoot: `${root}/state`, goalPath: `${root}/private/goal.json`, jobsPath: `${root}/private/jobs.json`,
    policyPath: `${root}/private/policy.json`, objectStore: `${root}/objects`, workspaceRoot: `${root}/workspaces`,
    executorPath: `${root}/bin/omp`, controller: { maxTransitions: 1 },
    runtime: { dockerPath: "/usr/bin/docker" },
  };
}

test("goal service units serialize bounded continuous work with event wakeups and a watchdog", () => {
  const value = renderGoalServiceUnits({
    configPath: "/etc/pixel-work/controller.json", config: config("/var/lib/pixel-work"), goal: goal(),
    installRoot: "/opt/pixel", serviceUser: "pixel-work", serviceGroup: "pixel-work", dockerGroup: "docker",
  });
  assert.equal(value.serviceName, "pixel-work-workgoal-1786366800001-abcdef123456.service");
  for (const anchor of [
    "--exclusive --nonblock /run/pixel-work-workgoal-1786366800001-abcdef123456/cycle.lock",
    "/goal-continuous-cli.mjs --config /etc/pixel-work/controller.json",
    "ExecStopPost=/usr/bin/flock --exclusive --wait 30",
    "ConditionPathExists=/run/docker.sock",
    "NoNewPrivileges=true", "PrivateNetwork=true", "ProtectHome=true", "ProtectSystem=strict",
    "BindReadOnlyPaths=/run/docker.sock", "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=",
    "ReadWritePaths=/var/lib/pixel-work/state /var/lib/pixel-work/workspaces", "TimeoutStopSec=5min", "Restart=no",
  ]) assert.match(value.service, new RegExp(anchor.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")));
  assert.doesNotMatch(value.service, /Requires=docker\.service|ConditionPathIsSocket/u);
  assert.match(value.timer, /OnUnitInactiveSec=30s/u);
  assert.match(value.timer, new RegExp(`Unit=${value.serviceName}`));
  assert.match(value.path, /PathChanged=\/var\/lib\/pixel-work\/state\/goal-checkpoints\/workgoal-1786366800001-abcdef123456\/records/u);
  assert.match(value.path, /PathChanged=\/var\/lib\/pixel-work\/state\/checkpoints\/work-1786366800000-abcdef123456\/records/u);
  assert.match(value.path, new RegExp(`Unit=${value.serviceName}`));
  assert.doesNotMatch(`${value.service}\n${value.timer}\n${value.path}`, /Complete the supervised fixture|abcdef123456abcdef/u);
  assert.match(value.boundary, /timer is only a stalled\/restart watchdog/u);
  assert.match(value.boundary, /Docker socket access remains a high-trust supervisor capability/u);
});

test("goal service unit rendering rejects privileged identities, home paths, overlap, and unsafe cadence", () => {
  const base = { configPath: "/etc/pixel-work/controller.json", config: config("/var/lib/pixel-work"), goal: goal(), installRoot: "/opt/pixel" };
  assert.throws(() => renderGoalServiceUnits({ ...base, serviceUser: "root" }), /privileged/);
  assert.throws(() => renderGoalServiceUnits({ ...base, configPath: "/home/operator/controller.json" }), /ProtectHome/);
  const overlap = structuredClone(base.config);
  overlap.workspaceRoot = `${overlap.stateRoot}/workspaces`;
  assert.throws(() => renderGoalServiceUnits({ ...base, config: overlap }), /disjoint/);
  const research = structuredClone(base.config);
  research.researchRuntime = { researchCourierQueueRoot: "/var/lib/pixel-work/research-queue", researchEndpoint: "http://127.0.0.1:8888" };
  const researchUnits = renderGoalServiceUnits({ ...base, config: research });
  assert.match(researchUnits.service, /ReadWritePaths=\/var\/lib\/pixel-work\/state \/var\/lib\/pixel-work\/workspaces \/var\/lib\/pixel-work\/research-queue/u);
  research.researchRuntime.researchCourierQueueRoot = `${research.stateRoot}/research-queue`;
  assert.throws(() => renderGoalServiceUnits({ ...base, config: research }), /disjoint/);
  const capability = structuredClone(base.config);
  capability.capabilityRuntime = {
    controllerPolicyPath: "/etc/pixel-work/capability-policy.json", allowedSignersPath: "/etc/pixel-work/capability-signers",
    sshKeygenPath: "/usr/bin/ssh-keygen", dockerConfigPath: "/var/lib/pixel-work/empty-docker", maxHealthAgeMs: 60000, bindings: [],
  };
  const capabilityUnits = renderGoalServiceUnits({ ...base, config: capability });
  for (const path of [capability.capabilityRuntime.controllerPolicyPath, capability.capabilityRuntime.allowedSignersPath, capability.capabilityRuntime.sshKeygenPath, capability.capabilityRuntime.dockerConfigPath]) assert.ok(capabilityUnits.service.includes(path));
  const knowledge = structuredClone(base.config);
  knowledge.knowledgeRuntime = { vaultRoot: "/var/lib/pixel-knowledge/vault", vaultId: "knowledgevault-abcdef123456", credentialSourcePath: "/etc/pixel-work-credentials/pixel-knowledge-vault-key", credentialName: "pixel-knowledge-vault-key", ownerId: "owner-one", clientId: "client-one", maxContextBytes: 16384 };
  const knowledgeUnits = renderGoalServiceUnits({ ...base, config: knowledge });
  assert.match(knowledgeUnits.service, /LoadCredential=pixel-knowledge-vault-key:\/etc\/pixel-work-credentials\/pixel-knowledge-vault-key/u);
  assert.equal(knowledgeUnits.service.match(/^NoNewPrivileges=true$/gmu)?.length, 1);
  assert.match(knowledgeUnits.service, /ReadWritePaths=\/var\/lib\/pixel-work\/state \/var\/lib\/pixel-work\/workspaces \/var\/lib\/pixel-knowledge\/vault/u);
  knowledge.knowledgeRuntime.credentialSourcePath = `${knowledge.stateRoot}/pixel-knowledge-vault-key`;
  assert.throws(() => renderGoalServiceUnits({ ...base, config: knowledge }), /credential source is inside a writable root/u);
  assert.throws(() => renderGoalServiceUnits({ ...base, intervalSeconds: 1 }), /interval/);
  assert.throws(() => renderGoalServiceUnits({ ...base, installRoot: "/opt/pixel path" }), /canonical/);
});

test("generated goal service and timer pass systemd verification", { skip: process.platform !== "linux" }, async (t) => {
  // The generated units reference the fixture root under ProtectHome protection, so the
  // temp root must never live under /home (a host with TMPDIR under /home would otherwise
  // trip the ProtectHome guard for an environment reason). Fall back to /tmp to keep the
  // gate reproducibly green.
  const forbiddenPrivateRoots = ["/home", "/root", "/run/user"];
  const base = forbiddenPrivateRoots.some((root) => tmpdir() === root || tmpdir().startsWith(`${root}/`)) ? "/tmp" : tmpdir();
  const root = await mkdtemp(join(base, "pixel-goal-systemd-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  for (const directory of ["state", "private", "objects", "workspaces", "bin", "install"]) await mkdir(join(root, directory), { mode: 0o700 });
  for (const file of ["goal.json", "jobs.json", "policy.json", "controller.json"]) await writeFile(join(root, "private", file), "{}\n", { mode: 0o600 });
  await writeFile(join(root, "bin", "omp"), "fixture\n", { mode: 0o500 });
  const installRoot = join(root, "install").replaceAll("\\", "/");
  const normalizedRoot = root.replaceAll("\\", "/");
  const value = renderGoalServiceUnits({
    configPath: `${normalizedRoot}/private/controller.json`, config: config(normalizedRoot), goal: goal(),
    installRoot, nodePath: "/usr/bin/node", serviceUser: "daemon", serviceGroup: "daemon", dockerGroup: "daemon",
  });
  const servicePath = join(root, value.serviceName);
  const timerPath = join(root, value.timerName);
  const pathPath = join(root, value.pathName);
  await writeFile(servicePath, value.service, { mode: 0o600 });
  await writeFile(timerPath, value.timer, { mode: 0o600 });
  await writeFile(pathPath, value.path, { mode: 0o600 });
  await chmod(servicePath, 0o600);
  await run("systemd-analyze", ["verify", servicePath, timerPath, pathPath], { timeout: 30000 });
});
