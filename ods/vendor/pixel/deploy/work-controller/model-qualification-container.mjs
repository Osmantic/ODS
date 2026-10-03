import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { WorkModelQualificationRunnerError, main as runQualification } from "./model-qualification-runner.mjs";
import { WorkModelQualificationBridgeError, startModelQualificationBridge } from "./model-qualification-bridge.mjs";

export const MODEL_QUALIFICATION_CONFIG_PATH = "/qualification/config.json";
export const MODEL_QUALIFICATION_OUTPUT_PATH = "/evidence/model-qualification.json";
const boundary = "Content-free exact local-model qualification container result only. It uses one private internal model route and grants no credential, external network, execution, external-effect, completion, publication, deployment, acceptance, or promotion authority.";

export class WorkModelQualificationContainerError extends Error {}

function fail(message) {
  throw new WorkModelQualificationContainerError(message);
}

export async function runModelQualificationContainer(argv = process.argv.slice(2), dependencies = {}) {
  if (!Array.isArray(argv) || argv.length !== 0) fail("qualification container accepts no arguments");
  const startBridge = dependencies.startBridge ?? startModelQualificationBridge;
  const runner = dependencies.runQualification ?? runQualification;
  let bridge;
  try {
    bridge = await startBridge();
    const result = await runner([
      "--config", MODEL_QUALIFICATION_CONFIG_PATH,
      "--output", MODEL_QUALIFICATION_OUTPUT_PATH,
    ]);
    return Object.freeze({
      ...result,
      privateNetwork: true,
      credentialsUsed: false,
      externalNetwork: false,
      boundary,
    });
  } finally {
    if (bridge) await new Promise((resolveClose, reject) => bridge.close((error) => error ? reject(error) : resolveClose()));
  }
}

export async function main(argv = process.argv.slice(2)) {
  const result = await runModelQualificationContainer(argv);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.status !== "qualified") process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof WorkModelQualificationContainerError
      || error instanceof WorkModelQualificationRunnerError
      || error instanceof WorkModelQualificationBridgeError;
    process.stderr.write(`pixel-work-model-qualification-container: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const modelQualificationContainerBoundary = boundary;
