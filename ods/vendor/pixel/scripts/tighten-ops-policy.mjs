#!/usr/bin/env node
import { copyFile, readFile, rename, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { isAbsolute, resolve } from "node:path";

const [policyValue, confirmation, ...extra] = process.argv.slice(2);
if (!policyValue || confirmation !== "--confirm" || extra.length > 0) {
  throw new Error("Usage: ./pixel ops-policy-tighten POLICY-V2.json --confirm");
}
if (!isAbsolute(policyValue)) throw new Error("Operations policy path must be absolute");
const policyPath = resolve(policyValue);
const policy = JSON.parse(await readFile(policyPath, "utf8"));
if (policy?.schemaVersion !== 2 || !Array.isArray(policy?.authority?.grants)) {
  throw new Error("Policy tightening requires an Operations schemaVersion 2 policy with authority grants");
}
const removed = policy.authority.grants.filter((grant) => grant?.compatibilityV1 === true).map((grant) => grant.id);
if (removed.length === 0) throw new Error("Policy contains no v1 compatibility grants to remove");
policy.authority.grants = policy.authority.grants.filter((grant) => grant?.compatibilityV1 !== true);
delete policy.authority.migrationNotice;
policy.compatibilityGrantsRemovedAt = new Date().toISOString();
const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "");
const backup = `${policyPath}.before-tighten-${stamp}`;
const temporary = `${policyPath}.tmp-${process.pid}`;
await copyFile(policyPath, backup, constants.COPYFILE_EXCL);
await writeFile(temporary, `${JSON.stringify(policy, null, 2)}\n`, { flag: "wx", mode: 0o600 });
await rename(temporary, policyPath);
console.log(JSON.stringify({ tightened: true, compatibilityGrantsRemoved: removed, backupCreated: true }));
