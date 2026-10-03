import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateWorkDataRuntime } from "../scripts/lib/work-contract.mjs";

async function fixture() {
  const runtime = JSON.parse(await readFile(new URL("../deploy/work-runner/data-runtime.json", import.meta.url), "utf8"));
  const dockerfile = await readFile(new URL("../deploy/work-runner/Dockerfile", import.meta.url), "utf8");
  return { runtime, dockerfile };
}

test("Data Lab runtime binds exact engines, distributions, image labels, and offline installation", async () => {
  const { runtime, dockerfile } = await fixture();
  assert.deepEqual(validateWorkDataRuntime(runtime), []);
  for (const artifact of runtime.artifacts) {
    assert.match(dockerfile, new RegExp(artifact.sha256));
    assert.ok(dockerfile.includes(artifact.url));
  }
  for (const entry of runtime.systemPackages) assert.ok(dockerfile.includes(`${entry.name}=${entry.version}`));
  for (const [name, engine] of Object.entries(runtime.engines)) assert.ok(dockerfile.includes(`org.osmantic.pixel.${name}-version=\"${engine.version}\"`));
  assert.match(dockerfile, /RUN --network=none[\s\S]*import duckdb, polars, sqlite3, sys/);
  assert.match(dockerfile, /PYTHONDONTWRITEBYTECODE=1[\s\S]*python3 -B -c/);
  assert.match(dockerfile, /find \/opt\/pixel\/data-runtime -type d -name __pycache__ -prune -exec rm -rf \{\} \+/);
  assert.match(dockerfile, /rm -f \/var\/cache\/ldconfig\/aux-cache \/var\/log\/alternatives\.log \/var\/log\/apt\/history\.log \/var\/log\/apt\/term\.log \/var\/log\/dpkg\.log/);
  assert.match(dockerfile, /ENV PYTHONPATH=\/opt\/pixel\/data-runtime/);
});

test("Data Lab runtime rejects version substitution, duplicate artifacts, and noncanonical packages", async () => {
  const { runtime } = await fixture();
  const substituted = structuredClone(runtime);
  substituted.engines.duckdb.version = "1.5.4";
  assert.match(validateWorkDataRuntime(substituted).join("; "), /version differs from engine contract/);
  const duplicated = structuredClone(runtime);
  duplicated.artifacts[2] = structuredClone(duplicated.artifacts[1]);
  assert.match(validateWorkDataRuntime(duplicated).join("; "), /complete and canonically ordered|hashes must be unique/);
  const reordered = structuredClone(runtime);
  reordered.systemPackages.reverse();
  assert.match(validateWorkDataRuntime(reordered).join("; "), /complete and canonically ordered/);
});
