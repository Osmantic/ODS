import { readFileSync } from "node:fs";

import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels } from "../../../deploy/work-runner/docker-boundary.mjs";

const example = JSON.parse(readFileSync(new URL("../../../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));

function prepared(overrides, bindings) {
  return { policy: { localModel: { ...example.localModel, ...overrides } }, ...(bindings === null ? {} : { bindings }) };
}

export function fixtureModelBackendLabelArguments(overrides = {}, bindings = null) {
  const value = prepared(overrides, bindings);
  return Object.entries(modelBackendIdentityLabels(value)).flatMap(([name, content]) => ["--label", `${name}=${content}`]);
}

export function fixtureModelBackendNetworkLabelArguments(overrides = {}, bindings = null) {
  const value = prepared(overrides, bindings);
  return Object.entries(modelBackendNetworkIdentityLabels(value)).flatMap(([name, content]) => ["--label", `${name}=${content}`]);
}
