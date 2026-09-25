import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateWorkOmpRuntime, validateWorkPolicy } from "../scripts/lib/work-contract.mjs";

async function fixtures() {
  const runtime = JSON.parse(await readFile(new URL("../deploy/work-runner/omp-runtime.json", import.meta.url), "utf8"));
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  return { runtime, policy };
}

test("OMP runtime binds the measured failure edge and conservative profile floors", async () => {
  const { runtime, policy } = await fixtures();
  assert.deepEqual(validateWorkOmpRuntime(runtime), []);
  assert.deepEqual(validateWorkPolicy(policy), []);
  assert.equal(runtime.probe.failureCeilingMiB, 1040);
  assert.equal(runtime.probe.successFloorMiB, 1048);
  assert.equal(runtime.minimumWorkerMemoryMiB, 1536);
  assert.equal(policy.profiles.scout.minimumMemoryMiB, runtime.profileMinimumMemoryMiB.scout);
  assert.equal(policy.profiles.builder.minimumMemoryMiB, runtime.profileMinimumMemoryMiB.builder);
});

test("OMP runtime rejects erased headroom and understated profile floors", async () => {
  const { runtime } = await fixtures();
  const noHeadroom = structuredClone(runtime); noHeadroom.safetyHeadroomMiB = 0; noHeadroom.minimumWorkerMemoryMiB = 1048;
  assert.notDeepEqual(validateWorkOmpRuntime(noHeadroom), []);
  const understated = structuredClone(runtime); understated.profileMinimumMemoryMiB.scout = 1048;
  assert.notDeepEqual(validateWorkOmpRuntime(understated), []);
});
