#!/usr/bin/env node
import { copyFile, readFile, rename, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { isAbsolute } from "node:path";

const values = process.argv.slice(2);
const onboardingPath = values.shift();
const packPath = values.shift();
const placeholder = values.shift();
const targetsValue = values.shift();
const skipActions = [];
let confirmed = false;
while (values.length > 0) {
  const option = values.shift();
  if (option === "--confirm") { confirmed = true; continue; }
  if (option === "--skip-action") {
    const action = values.shift();
    if (!action) throw new Error("--skip-action requires one action ID");
    skipActions.push(action);
    continue;
  }
  throw new Error(`Unknown action-pack option: ${option}`);
}
if (!onboardingPath || !packPath || !placeholder || !targetsValue || !confirmed) {
  throw new Error("Usage: ./pixel ops-action-pack ONBOARDING.json PACK.json PLACEHOLDER TARGET[,TARGET...] [--skip-action ACTION ...] --confirm");
}
if (!isAbsolute(onboardingPath) || !isAbsolute(packPath)) throw new Error("Onboarding and action-pack paths must be absolute");
if (!/^[a-z][a-z0-9_-]{1,63}$/.test(placeholder)) throw new Error("Action-pack placeholder is unsafe");
const targets = [...new Set(targetsValue.split(","))];
if (targets.length === 0 || targets.some((target) => !/^[a-z][a-z0-9_-]{1,63}$/.test(target))) throw new Error("Action-pack targets are unsafe");
const onboarding = JSON.parse(await readFile(onboardingPath, "utf8"));
const policyPath = onboarding.operationsPolicyFile;
if (typeof policyPath !== "string" || !isAbsolute(policyPath)) throw new Error("Onboarding does not reference an absolute operationsPolicyFile");
const policy = JSON.parse(await readFile(policyPath, "utf8"));
if (policy?.schemaVersion !== 2) throw new Error("Action packs require an Operations schemaVersion 2 policy");
if (targets.some((target) => !Object.hasOwn(policy.targets ?? {}, target))) throw new Error("An action-pack target is absent from the Operations policy");
const pack = JSON.parse(await readFile(packPath, "utf8"));
if (pack?.schemaVersion !== 1 || pack.targetPlaceholder !== placeholder || typeof pack.actions !== "object") throw new Error("Action pack or placeholder is invalid");
const uniqueSkips = [...new Set(skipActions)];
if (uniqueSkips.some((name) => !Object.hasOwn(pack.actions, name))) throw new Error("A skipped action is absent from the action pack");
if ((pack.authorityGrants ?? []).some((grant) => (grant.actions ?? []).some((name) => uniqueSkips.includes(name)))) throw new Error("Cannot skip an action referenced by an action-pack authority grant");
const specification = { file: packPath, targets: { [placeholder]: targets }, ...(uniqueSkips.length > 0 ? { skipActions: uniqueSkips } : {}) };
const existing = Array.isArray(onboarding.operationsActionPacks) ? onboarding.operationsActionPacks : [];
onboarding.operationsActionPacks = [...existing.filter((item) => item?.file !== packPath), specification];
delete onboarding.operationsActionPackFiles;
const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "");
const backup = `${onboardingPath}.before-action-pack-${stamp}`;
const temporary = `${onboardingPath}.tmp-${process.pid}`;
await copyFile(onboardingPath, backup, constants.COPYFILE_EXCL);
await writeFile(temporary, `${JSON.stringify(onboarding, null, 2)}\n`, { flag: "wx", mode: 0o600 });
await rename(temporary, onboardingPath);
console.log(JSON.stringify({ configured: true, placeholder, targets, skippedActions: uniqueSkips, backupCreated: true }));
