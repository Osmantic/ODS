import { createHash } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { dockerBuilderContract } from "../work-runner/docker-builder.mjs";
import { dockerDataLabContract } from "../work-runner/docker-data-lab.mjs";
import { dockerResearcherContract } from "../work-runner/docker-researcher.mjs";
import { dockerScoutContract } from "../work-runner/docker-scout.mjs";

function sha(value) {
  return createHash("sha256").update(canonical(value), "utf8").digest("hex");
}

export function localModelPromptContract() {
  return structuredClone({
    schemaVersion: 1,
    profiles: {
      scout: dockerScoutContract.systemPrompt,
      builder: dockerBuilderContract.systemPrompt,
      "data-lab": dockerDataLabContract.systemPrompt,
      researcher: dockerResearcherContract.systemPrompt,
    },
  });
}

export function localModelToolContract() {
  return structuredClone({
    schemaVersion: 1,
    profiles: {
      scout: { tools: dockerScoutContract.ompTools },
      builder: { tools: dockerBuilderContract.ompTools },
      "data-lab": { tools: dockerDataLabContract.ompTools },
      researcher: {
        tools: dockerResearcherContract.ompAllowedTools,
        trustedExtension: dockerResearcherContract.trustedExtension,
        researchTransport: dockerResearcherContract.researchTransport,
      },
    },
  });
}

export function localModelPromptContractSha256() { return sha(localModelPromptContract()); }
export function localModelToolContractSha256() { return sha(localModelToolContract()); }

export const localModelRuntimeContractVersion = 1;
