import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { productionMaintenancePrimitives } from "./model-qualification-maintenance.mjs";
import { validateModelCampaignMaintenanceConfiguration } from "./model-campaign-maintenance.mjs";
import { deriveCampaignGuardianLeasePath } from "./maintenance-recovery-guardian-lease.mjs";
import { assertNoActiveCampaignRecoveryJournal } from "./maintenance-recovery-journal.mjs";
import { renderCampaignGuardianUnit } from "./maintenance-campaign-recovery-guardian-unit.mjs";
import { CAMPAIGN_RECOVERY_ENGINE_READY } from "./maintenance-campaign-recovery-guardian.mjs";
import {
  buildSupervisorContext,
  classifyIsEnabled,
  parseArgs,
  superviseInstallWith,
  superviseRemoveWith,
  superviseReviewWith,
  supervisorCli,
  validateUnitName,
} from "./maintenance-recovery-guardian-supervisor.mjs";

export { WorkMaintenanceRecoverySupervisorError } from "./maintenance-recovery-guardian-supervisor.mjs";

// Campaign read path: strict owner-private UTF-8/JSON config validated by the
// campaign contract, binding the exact raw-byte config SHA. Reuses the same
// primitive as the qualification supervisor; only the label/validator differ.
async function campaignReadConfig(configPath, expectedOwnerUid) {
  const record = await productionMaintenancePrimitives.readPrivateJsonRecord(configPath, "private campaign maintenance configuration", expectedOwnerUid);
  const configuration = validateModelCampaignMaintenanceConfiguration(record.value);
  return { configuration, bytes: record.bytes };
}

// Distinct campaign identity everywhere. The shared supervisor engine
// (superviseReviewWith/superviseInstallWith/superviseRemoveWith/supervisorCli)
// is reused unchanged; every campaign identity is carried by this descriptor.
// Qualification artifacts are never consumable by campaign paths or vice versa
// even when render/config hashes coincide.
export const CAMPAIGN_SUPERVISOR_DESCRIPTOR = Object.freeze({
  label: "maintenance-campaign-recovery-guardian-supervise",
  cliLabel: "maintenance-campaign-recovery-guardian",
  configLabel: "private campaign maintenance configuration",
  configPathLabel: "campaign maintenance configuration path",
  reviewOperation: "pixel-campaign-maintenance-recovery-guardian-supervise-review",
  installConfirmOperation: "campaign-install",
  removeConfirmOperation: "campaign-remove",
  unitName: "pixel-campaign-maintenance-recovery.service",
  defaultGuardianModule: "maintenance-campaign-recovery-guardian.mjs",
  leasePathFor: deriveCampaignGuardianLeasePath,
  assertNoActiveJournal: assertNoActiveCampaignRecoveryJournal,
  defaultRenderUnit: renderCampaignGuardianUnit,
  defaultReadConfig: campaignReadConfig,
  ready: CAMPAIGN_RECOVERY_ENGINE_READY,
  emitEngineState: true,
});

export async function superviseCampaignReview(options = {}) {
  return superviseReviewWith(CAMPAIGN_SUPERVISOR_DESCRIPTOR, options);
}

export async function superviseCampaignInstall(options = {}) {
  return superviseInstallWith(CAMPAIGN_SUPERVISOR_DESCRIPTOR, options);
}

export async function superviseCampaignRemove(options = {}) {
  return superviseRemoveWith(CAMPAIGN_SUPERVISOR_DESCRIPTOR, options);
}

export async function buildCampaignContext(options = {}) {
  return buildSupervisorContext(CAMPAIGN_SUPERVISOR_DESCRIPTOR, options);
}

export async function main(argv = process.argv.slice(2)) {
  return supervisorCli(CAMPAIGN_SUPERVISOR_DESCRIPTOR, argv);
}

export { classifyIsEnabled, parseArgs, validateUnitName };

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then((code) => { process.exitCode = code; });
}
