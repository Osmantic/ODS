import { createGoalBuilderDriver } from "./goal-builder-driver.mjs";
import { createGoalBuilderPreparer } from "./goal-builder-preparer.mjs";
import { createGoalBuilderResolver } from "./goal-builder-resolver.mjs";
import { createGoalCapabilityResolver } from "./goal-capability-runtime.mjs";
import { createGoalKnowledgeResolver, validateGoalKnowledgeBindings } from "./goal-knowledge-runtime.mjs";
import {
  createGoalCandidateDriver, createGoalCandidatePreparer, createGoalCandidateResolver,
} from "./goal-candidate-adapter.mjs";

const supportedProfiles = new Set(["scout", "builder", "researcher", "data-lab"]);

export class GoalProfileRouterError extends Error {}

function fail(message) { throw new GoalProfileRouterError(message); }

function overrides(value, label) {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label} overrides are invalid`);
  return value;
}

export function createGoalProfileRouter({
  config, goal, jobs, policy, capabilityPolicy = null, knowledgeMasterKey = null, builderOverrides, candidateOverrides,
} = {}) {
  if (!config || !goal || !Array.isArray(jobs) || !policy || jobs.length < 1 || jobs.some((job) => !supportedProfiles.has(job?.profile))) fail("goal profile router configuration is invalid or contains an unsupported profile");
  if (Boolean(config.capabilityRuntime) !== Boolean(capabilityPolicy)) fail("goal profile router capability policy presence differs from its runtime configuration");
  validateGoalKnowledgeBindings({ config, jobs });
  if (Boolean(config.knowledgeRuntime) !== Boolean(knowledgeMasterKey)) fail("goal profile router knowledge credential presence differs from its runtime configuration");
  const builder = overrides(builderOverrides, "Builder");
  const candidate = overrides(candidateOverrides, "candidate");
  const common = {
    stateRoot: config.stateRoot, goal, jobs, policy, objectStore: config.objectStore,
    workspaceRoot: config.workspaceRoot, executorPath: config.executorPath, archiveLimits: config.archiveLimits,
  };
  const builderResolver = builder.resolver ?? createGoalBuilderResolver({ stateRoot: config.stateRoot, goal, jobs, policy, objectStore: config.objectStore, ...(builder.clock ? { clock: builder.clock } : {}), ...(builder.resolverSuffix ? { suffix: builder.resolverSuffix } : {}) });
  const builderPrepare = builder.prepare ?? createGoalBuilderPreparer({ ...common, ...(builder.clock ? { clock: builder.clock } : {}) });
  const candidateResolver = candidate.resolver ?? createGoalCandidateResolver({ stateRoot: config.stateRoot, goal, jobs, policy, objectStore: config.objectStore, ...(candidate.clock ? { clock: candidate.clock } : {}), ...(candidate.resolverSuffix ? { suffix: candidate.resolverSuffix } : {}) });
  const candidatePrepare = candidate.prepare ?? createGoalCandidatePreparer({ ...common, ...(candidate.clock ? { clock: candidate.clock } : {}) });
  for (const [label, callback] of Object.entries({ builderResolver, builderPrepare, candidateResolver, candidatePrepare })) if (typeof callback !== "function") fail(`goal profile router ${label} is invalid`);
  const capabilityResolver = config.capabilityRuntime ? createGoalCapabilityResolver({ config, jobs, policy: capabilityPolicy }) : null;
  const knowledgeResolver = config.knowledgeRuntime ? createGoalKnowledgeResolver({ config, jobs, masterKey: knowledgeMasterKey }) : null;
  const lifecycleOptions = Object.freeze({ stateRoot: config.stateRoot, ...config.runtime, ...(capabilityResolver ? { capabilityResolver } : {}), ...(knowledgeResolver ? { knowledgeResolver } : {}) });
  const researchLifecycleOptions = Object.freeze({ ...lifecycleOptions, ...(config.researchRuntime ?? {}) });
  const builderDriver = builder.driver ?? createGoalBuilderDriver({
    stateRoot: config.stateRoot, prepare: builderPrepare, lifecycleOptions,
    ...(builder.clock ? { clock: builder.clock } : {}), ...(builder.suffixes ? { suffixes: builder.suffixes } : {}),
    ...(builder.cleanup ? { cleanup: builder.cleanup } : {}),
  });
  const candidateDriver = candidate.driver ?? createGoalCandidateDriver({
    stateRoot: config.stateRoot, prepare: candidatePrepare,
    lifecycleOptions: ({ context }) => context.job.profile === "researcher" ? researchLifecycleOptions : lifecycleOptions,
    ...(candidate.clock ? { clock: candidate.clock } : {}), ...(candidate.suffixes ? { suffixes: candidate.suffixes } : {}),
    ...(candidate.candidateRunner ? { candidateRunner: candidate.candidateRunner } : {}),
    ...(candidate.cleanup ? { cleanup: candidate.cleanup } : {}),
    ...(candidate.discard ? { discard: candidate.discard } : {}),
  });
  if (typeof builderDriver !== "function" || typeof candidateDriver !== "function") fail("goal profile router drivers are invalid");
  const profile = (job) => {
    if (!supportedProfiles.has(job?.profile)) fail("goal profile router received an unsupported child");
    return job.profile;
  };
  return Object.freeze({
    resolveChildRun: (context) => profile(context?.job) === "builder" ? builderResolver(context) : candidateResolver(context),
    driveChild: (context) => profile(context?.job) === "builder" ? builderDriver(context) : candidateDriver(context),
    profileFor: profile,
    lifecycleOptions,
  });
}

export const goalProfileRouterBoundary = "Exact-profile supervised router only. It selects an existing Builder or Scout/Researcher/Data Lab adapter from the immutable job profile; it cannot rewrite the job, lease, criteria, authority, or completion evidence.";
