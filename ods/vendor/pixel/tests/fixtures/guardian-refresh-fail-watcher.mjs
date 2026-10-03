// Test helper that mimics the exact supervised guardian watch invocation
// ("node <helper> watch --config <configPath>") and its fail-closed refresh
// contract. It publishes its own ready lease from its own live /proc identity,
// then a SUBSEQUENT refresh fails through a fixture-only injected dependency
// (a refresh wrapper that throws on the post-ready call). Like the real
// watcher, a failed refresh durably removes the watcher's own exact lease and
// exits nonzero so an old ready lease can never continue authorizing.
//
// The ordering is made deterministic with an owner-private acknowledgement
// handshake rather than a fixed pre-failure delay: after publishing the ready
// lease the fixture waits boundedly for a singular owner-private ack file that
// the parent writes only once it has observed and validated the ready lease.
// If the ack never arrives the fixture fails closed and removes its own lease.
import { readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { refreshGuardianLease, removeGuardianLease } from "../../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";
import { assertOwnerPrivateAckPath } from "./guardian-ack-contract.mjs";

const argv = process.argv;
const configPath = argv[argv.indexOf("--config") + 1];
const custodyLockPath = process.env.CUSTODY_LOCK_PATH;
const uid = Number(process.env.UID);
const configSha256 = process.env.CONFIG_SHA;
const unit = process.env.UNIT;
const custodyIdentitySha256 = process.env.CUSTODY_IDENTITY_SHA;
const guardianModuleSha256 = process.env.GUARDIAN_MODULE_SHA;
const expectedNodePath = process.env.EXPECTED_NODE;
const expectedUnitSha256 = process.env.EXPECTED_UNIT_SHA_FILE
  ? (await readFile(process.env.EXPECTED_UNIT_SHA_FILE, "utf8")).trim()
  : process.env.EXPECTED_UNIT_SHA;
const ackPath = assertOwnerPrivateAckPath(process.env.ACK_FILE, dirname(custodyLockPath));
const expectedAckToken = process.env.ACK_TOKEN ?? "";
const ACK_TIMEOUT_MS = 30000;
const leaseOptions = { unit, custodyIdentitySha256, guardianModuleSha256, expectedUnitSha256, expectedNodePath };

// Fixture-only injected dependency: the first two calls (non-ready, ready)
// succeed; the post-ready refresh call fails and must fail closed.
let refreshCount = 0;
const refresh = async (ready) => {
  refreshCount += 1;
  if (process.env.FAIL_ON_REFRESH === "1" && refreshCount >= 3) {
    throw new Error("fixture-injected refresh failure");
  }
  return refreshGuardianLease(custodyLockPath, uid, configSha256, { ...leaseOptions, ready });
};

await refresh(false);
await refresh(true);
process.stdout.write("READY\n");
process.stdout.on("error", () => process.exit(0));
// Deterministic handshake: wait boundedly for the parent's owner-private ack
// (written only after it has observed and validated the ready lease), then
// perform the injected failing refresh.
const ackDeadline = Date.now() + ACK_TIMEOUT_MS;
let acked = false;
while (Date.now() < ackDeadline) {
  try {
    const content = (await readFile(ackPath, "utf8")).trim();
    if (content === expectedAckToken) { acked = true; break; }
  } catch (error) {
    if (!error || error.code !== "ENOENT") break;
  }
  await delay(50);
}
if (!acked) {
  try { await removeGuardianLease(custodyLockPath, uid); } catch { /* best-effort durable removal */ }
  process.stderr.write("fixture ready-ack never arrived; failing closed and removing the lease\n");
  process.exit(4);
}
try {
  await refresh(true);
} catch (error) {
  try { await removeGuardianLease(custodyLockPath, uid); } catch { /* best-effort durable removal */ }
  process.stderr.write(`fixture refresh failed: ${String(error?.message ?? error)}\n`);
  process.exit(3);
}
setInterval(() => {}, 1000);
