import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import test from "node:test";

import { renderDeepWorkSoakServiceUnits } from "../deploy/work-controller/deep-work-soak-service-units.mjs";

const run = promisify(execFile);

function config(root = "/var/lib/pixel-deep-work/campaign") {
  return {
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak", campaignId: "deepworksoak-1786366800001-abcdef123456",
    schedule: { intervalSeconds: 3600, minimumGapSeconds: 3300, maximumGapSeconds: 7200, minimumElapsedSeconds: 172800, milestones: 24, expectedCycles: 49 },
    paths: {
      root, stateRoot: `${root}/state`, objectStore: `${root}/objects`, goalPath: `${root}/contracts/goal.json`,
      jobsPath: `${root}/contracts/jobs.json`, policyPath: `${root}/contracts/policy.json`, invocationLedgerRoot: `${root}/invocations`,
    },
  };
}

test("multi-day soak units schedule exact UTC-hour fresh processes inside a networkless least-privilege sandbox", () => {
  const campaign = config();
  const units = renderDeepWorkSoakServiceUnits({
    configPath: `${campaign.paths.root}/campaign.json`, config: campaign, configSha256: "a".repeat(64), installRoot: "/opt/pixel",
  });
  assert.equal(units.serviceName, "pixel-deep-work-deepworksoak-1786366800001-abcdef123456.service");
  for (const anchor of [
    "--exclusive --nonblock /run/pixel-deep-work-deepworksoak-1786366800001-abcdef123456/cycle.lock",
    "/usr/bin/env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 INVOCATION_ID=${INVOCATION_ID}",
    "/scripts/deep-work-multi-day-soak.mjs cycle --config /var/lib/pixel-deep-work/campaign/campaign.json",
    `--confirm-config-sha256 ${"a".repeat(64)}`,
    "NoNewPrivileges=true", "PrivateNetwork=true", "ProtectClock=true", "ProtectHome=true", "ProtectSystem=strict",
    "ProtectProc=invisible", "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=", "DevicePolicy=closed",
    "ReadWritePaths=/var/lib/pixel-deep-work/campaign/state /var/lib/pixel-deep-work/campaign/invocations",
    "InaccessiblePaths=-/root -/home -/run/user -/run/docker.sock -/var/run/docker.sock",
    "MemoryMax=512M", "TasksMax=128", "CPUQuota=50%", "Restart=no",
  ]) assert.ok(units.service.includes(anchor), anchor);
  for (const anchor of ["OnCalendar=*-*-* *:00:00 UTC", "Persistent=true", "AccuracySec=1s", "RandomizedDelaySec=0", `Unit=${units.serviceName}`]) assert.ok(units.timer.includes(anchor), anchor);
  assert.doesNotMatch(`${units.service}\n${units.timer}`, /EnvironmentFile|SupplementaryGroups|docker\.service|BindReadOnlyPaths|OMP|TOKEN|API_KEY/u);
  assert.match(units.boundary, /grants no work lease/u);
});

test("multi-day soak unit rendering rejects privileged users, home paths, malformed cadence, path drift, and writable overlap", () => {
  const campaign = config();
  const base = { configPath: `${campaign.paths.root}/campaign.json`, config: campaign, configSha256: "a".repeat(64), installRoot: "/opt/pixel" };
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, serviceUser: "root" }), /privileged/u);
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, installRoot: "/home/operator/pixel" }), /ProtectHome/u);
  const cadence = structuredClone(campaign); cadence.schedule.intervalSeconds = 30;
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, config: cadence }), /campaign contract/u);
  const drift = structuredClone(campaign); drift.paths.goalPath = `${campaign.paths.root}/goal.json`;
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, config: drift }), /campaign contract/u);
  const overlap = structuredClone(campaign); overlap.paths.invocationLedgerRoot = overlap.paths.stateRoot;
  overlap.paths.root = "/var/lib/pixel-deep-work";
  assert.throws(() => renderDeepWorkSoakServiceUnits({ configPath: "/var/lib/pixel-deep-work/campaign.json", config: overlap, configSha256: "a".repeat(64), installRoot: "/opt/pixel" }), /campaign contract|disjoint/u);
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, installRoot: campaign.paths.stateRoot }), /writable and protected/u);
  assert.throws(() => renderDeepWorkSoakServiceUnits({ ...base, nodePath: "/usr/bin/node --inspect" }), /canonical safe Linux path/u);
});

test("generated multi-day soak service and timer pass systemd verification", { skip: process.platform !== "linux" }, async (t) => {
  // The generated units reference the fixture root under ProtectHome protection, so the
  // temp root must never live under /home (a host with TMPDIR under /home would otherwise
  // trip the ProtectHome guard for an environment reason). Fall back to /tmp to keep the
  // gate reproducibly green.
  const forbiddenPrivateRoots = ["/home", "/root", "/run/user"];
  const base = forbiddenPrivateRoots.some((root) => tmpdir() === root || tmpdir().startsWith(`${root}/`)) ? "/tmp" : tmpdir();
  const root = await mkdtemp(join(base, "pixel-soak-systemd-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const campaignRoot = `${root.replaceAll("\\", "/")}/campaign`;
  for (const directory of ["campaign", "campaign/contracts", "campaign/objects", "campaign/state", "campaign/invocations"]) await mkdir(join(root, directory), { mode: 0o700 });
  await writeFile(join(root, "campaign", "campaign.json"), "{}\n", { mode: 0o600 });
  const units = renderDeepWorkSoakServiceUnits({
    configPath: `${campaignRoot}/campaign.json`, config: config(campaignRoot), installRoot: "/opt/pixel",
    configSha256: "a".repeat(64),
    nodePath: "/usr/bin/node", serviceUser: "daemon", serviceGroup: "daemon",
  });
  const servicePath = join(root, units.serviceName), timerPath = join(root, units.timerName);
  await writeFile(servicePath, units.service, { mode: 0o600 }); await writeFile(timerPath, units.timer, { mode: 0o600 });
  await chmod(servicePath, 0o600); await chmod(timerPath, 0o600);
  await run("systemd-analyze", ["verify", servicePath, timerPath], { timeout: 30000 });
});
