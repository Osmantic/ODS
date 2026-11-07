// VM source-composition fixtures need the real state referenced by index.js.
// These are production constructors, never permissive/no-op admission mocks.
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {createIntegrationOutcomes} from '../plugin/integration-outcomes.mjs';
import {withPixelCronJobAdmission} from '../plugin/cron-delivery-default.mjs';

const entryUrl = process.env.PIXEL_PLUGIN_ENTRY
  ? pathToFileURL(path.resolve(process.env.PIXEL_PLUGIN_ENTRY)) : new URL('../plugin/index.js',import.meta.url);
const recoveryUrl = new URL('./integration-review-recovery.mjs',entryUrl);
const {createIntegrationReviewRecovery} = await import(recoveryUrl.href);

export function nativeIntegrationHookState() {
  return {integrationOutcomes:createIntegrationOutcomes(),withPixelCronJobAdmission,
    // Use the actual fail-closed constructor. These source-composition fixtures
    // have no model route; a missing module is an error, never a no-op floor.
    integrationReviewRecovery:createIntegrationReviewRecovery()};
}
