import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  renderGuardianUnitWith,
  systemdSafeAbs,
  WorkMaintenanceRecoveryUnitError,
} from "./maintenance-recovery-guardian-unit.mjs";
import { validateModelCampaignMaintenanceConfiguration } from "./model-campaign-maintenance.mjs";

// Frozen campaign unit descriptor. The shared renderer engine
// (renderGuardianUnitWith) is reused unchanged; only the campaign identity,
// config contract, and least-privilege writable-path derivation differ.
// M3 grants the campaign unit write access ONLY to the campaign custody-state
// directory (dirname of the custody lock) needed for lease/journal state. It
// never grants outputRoot or broad source/materialization paths write access.
const CAMPAIGN_UNIT_DESCRIPTOR = Object.freeze({
  configLabel: "private campaign maintenance configuration",
  configPathLabel: "campaign maintenance configuration path",
  nodeLabel: "campaign guardian node executable",
  guardianLabel: "campaign guardian module path",
  custodyLabel: "campaign custody lock path",
  validateConfig: validateModelCampaignMaintenanceConfiguration,
  deriveWritablePaths: (configuration) => {
    const custodyLock = systemdSafeAbs(configuration.custody?.lockPath, "campaign custody lock path");
    return [dirname(custodyLock)].sort();
  },
  unitDescription: (configPath) => `Pixel campaign maintenance recovery guardian for ${configPath}`,
  supervisionCommand: "watch",
});

export async function renderCampaignGuardianUnit(options = {}) {
  return renderGuardianUnitWith(CAMPAIGN_UNIT_DESCRIPTOR, options);
}

function fail(message) { throw new WorkMaintenanceRecoveryUnitError(message); }

export async function main(argv = process.argv.slice(2)) {
  if (!Array.isArray(argv) || argv.length !== 7 || argv[0] !== "render" || argv[1] !== "--config" || argv[3] !== "--node" || argv[5] !== "--guardian") {
    fail("Usage: maintenance-campaign-recovery-guardian-unit.mjs render --config PRIVATE_JSON --node NODE --guardian GUARDIAN");
  }
  const rendered = await renderCampaignGuardianUnit({ configPath: argv[2], nodePath: argv[4], guardianPath: argv[6] });
  process.stdout.write(rendered.unit);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-campaign-maintenance-recovery-guardian-unit: ${error instanceof WorkMaintenanceRecoveryUnitError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
