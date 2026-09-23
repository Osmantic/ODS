#!/usr/bin/env node
import { copyFile, readFile, rename, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { isAbsolute, resolve } from "node:path";

const values = process.argv.slice(2);
const inputValue = values.shift();
const outputValue = values.shift();
const environments = new Map();
let onboardingValue;
let confirmed = false;
while (values.length > 0) {
  const option = values.shift();
  if (option === "--confirm") { confirmed = true; continue; }
  if (option === "--environment") {
    const assignment = values.shift() ?? "";
    const separator = assignment.indexOf("=");
    if (separator < 1) throw new Error("--environment requires TARGET=ENVIRONMENT");
    environments.set(assignment.slice(0, separator), assignment.slice(separator + 1));
    continue;
  }
  if (option === "--update-onboarding") {
    onboardingValue = values.shift();
    if (!onboardingValue) throw new Error("--update-onboarding requires an absolute path");
    continue;
  }
  throw new Error(`Unknown migration option: ${option}`);
}
if (!inputValue || !outputValue || !confirmed) {
  throw new Error("Usage: ./pixel ops-policy-migrate INPUT.json OUTPUT.json [--environment TARGET=ENVIRONMENT ...] [--update-onboarding ONBOARDING.json] --confirm");
}
const input = resolve(inputValue);
const output = resolve(outputValue);
if (!isAbsolute(inputValue) || !isAbsolute(outputValue) || input === output) {
  throw new Error("Migration paths must be distinct absolute paths");
}
let onboarding;
let onboardingPath;
if (onboardingValue !== undefined) {
  if (!isAbsolute(onboardingValue)) throw new Error("Onboarding migration path must be absolute");
  onboardingPath = resolve(onboardingValue);
  onboarding = JSON.parse(await readFile(onboardingPath, "utf8"));
  if (onboarding?.operationsPolicyFile !== input) throw new Error("Onboarding does not reference the migration input policy");
}
const policy = JSON.parse(await readFile(input, "utf8"));
if (policy?.schemaVersion !== 1 || typeof policy.targets !== "object" || typeof policy.actions !== "object") {
  throw new Error("Input must be an Operations schemaVersion 1 policy");
}
const tiers = new Set(["read", "staging", "managed", "change"]);
const autoTiers = Array.isArray(policy.autoTiers) ? policy.autoTiers.filter((tier) => tiers.has(tier)) : ["read"];
delete policy.autoTiers;
policy.schemaVersion = 2;
policy.migratedFromSchemaVersion = 1;
for (const target of Object.values(policy.targets)) target.environment ??= "unclassified";
const allowedEnvironments = new Set(["development", "test", "staging", "production", "lab", "unclassified"]);
for (const [target, environment] of environments) {
  if (!Object.hasOwn(policy.targets, target) || !allowedEnvironments.has(environment)) throw new Error(`Invalid target environment assignment: ${target}=${environment}`);
  policy.targets[target].environment = environment;
}
const effects = { read: "observe", staging: "stage", managed: "manage", change: "change", "break-glass": "change" };
for (const action of Object.values(policy.actions)) {
  action.effect ??= effects[action.tier];
  action.defaultAuthority ??= action.tier === "read" ? "observe" : "propose";
  action.idempotent ??= action.tier === "read";
  action.reversible ??= false;
}
policy.authority = {
  defaultLevel: "propose",
  grants: autoTiers.map((tier) => ({
    id: `legacy-auto-${tier}`,
    level: "bounded-auto",
    actions: ["*"],
    targets: ["*"],
    tiers: [tier],
    environments: ["development", "test", "staging", "production", "lab", "unclassified"],
    allowProduction: true,
    compatibilityV1: true
  })),
  migrationNotice: "Replace compatibility grants with explicit action- and target-scoped grants after review."
};
await writeFile(output, `${JSON.stringify(policy, null, 2)}\n`, { flag: "wx", mode: 0o600 });
let onboardingUpdated = false;
if (onboarding && onboardingPath) {
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "");
  const backup = `${onboardingPath}.before-ops-v2-${stamp}`;
  const temporary = `${onboardingPath}.tmp-${process.pid}`;
  onboarding.operationsPolicyFile = output;
  await copyFile(onboardingPath, backup, constants.COPYFILE_EXCL);
  await writeFile(temporary, `${JSON.stringify(onboarding, null, 2)}\n`, { flag: "wx", mode: 0o600 });
  await rename(temporary, onboardingPath);
  onboardingUpdated = true;
}
console.log(JSON.stringify({ migrated: true, input, output, schemaVersion: 2, compatibilityGrantCount: autoTiers.length, onboardingUpdated }));
