// Test helper that mimics the exact supervised guardian watch invocation
// ("node <helper> watch --config <configPath>") and publishes its own campaign
// guardian lease (bound to the exact reviewed campaign operation and immutable
// journal identity) from its own live /proc identity, then stays alive. It is
// used only to prove a real separate-process campaign watcher lease is accepted
// by the proof path.
import { readFile } from "node:fs/promises";
import { refreshCampaignGuardianLease } from "../../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";

const argv = process.argv;
const configPath = argv[argv.indexOf("--config") + 1];
const custodyLockPath = process.env.CUSTODY_LOCK_PATH;
const uid = Number(process.env.UID);
const configSha256 = process.env.CONFIG_SHA;
const unit = process.env.UNIT;
const custodyIdentitySha256 = process.env.CUSTODY_IDENTITY_SHA;
const guardianModuleSha256 = process.env.GUARDIAN_MODULE_SHA;
const expectedNodePath = process.env.EXPECTED_NODE;
const campaignOperationSha256 = process.env.CAMPAIGN_OPERATION_SHA;
const journalIdentitySha256 = process.env.JOURNAL_IDENTITY_SHA;
// The reviewed expected render sha is supplied through a file (a constant path in
// the unit bytes) so the installed unit bytes are not self-referential.
const expectedUnitSha256 = process.env.EXPECTED_UNIT_SHA_FILE
  ? (await readFile(process.env.EXPECTED_UNIT_SHA_FILE, "utf8")).trim()
  : process.env.EXPECTED_UNIT_SHA;
// Prove identity with a non-ready lease first, then mark ready only after that
// write succeeds (mirrors the real watcher's fail-closed readiness contract).
await refreshCampaignGuardianLease(custodyLockPath, uid, configSha256, { unit, custodyIdentitySha256, guardianModuleSha256, expectedUnitSha256, expectedNodePath, campaignOperationSha256, journalIdentitySha256, ready: false });
await refreshCampaignGuardianLease(custodyLockPath, uid, configSha256, { unit, custodyIdentitySha256, guardianModuleSha256, expectedUnitSha256, expectedNodePath, campaignOperationSha256, journalIdentitySha256, ready: true });
process.stdout.write("READY\n");
process.stdout.on("error", () => process.exit(0));
setInterval(() => {}, 1000);
