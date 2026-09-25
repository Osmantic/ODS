import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { validateWorkBuilderRuntime } from "../scripts/lib/work-contract.mjs";

async function fixture() {
  const runtime = JSON.parse(await readFile(new URL("../deploy/work-runner/builder-runtime.json", import.meta.url), "utf8"));
  const dockerfile = await readFile(new URL("../deploy/work-runner/Dockerfile", import.meta.url), "utf8");
  const agent = await readFile(new URL("../deploy/work-runner/builder-agent.md", import.meta.url));
  const entrypoint = await readFile(new URL("../deploy/work-runner/builder-entrypoint.sh", import.meta.url));
  return { runtime, dockerfile, agent, entrypoint };
}

const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");

test("Builder runtime binds functional OMP Python LSP and debugger dependencies", async () => {
  const { runtime, dockerfile, agent, entrypoint } = await fixture();
  assert.deepEqual(validateWorkBuilderRuntime(runtime), []);
  assert.equal(runtime.minimumMemoryMiB, 2048);
  for (const artifact of runtime.artifacts) {
    assert.ok(dockerfile.includes(artifact.url));
    assert.ok(dockerfile.includes(artifact.sha256));
  }
  for (const entry of runtime.systemPackages) assert.ok(dockerfile.includes(`${entry.name}=${entry.version}`));
  assert.ok(dockerfile.includes(`org.osmantic.pixel.builder-runtime=\"${runtime.contract}\"`));
  assert.ok(dockerfile.includes(`org.osmantic.pixel.gdb-version=\"${runtime.features.gdb.runtimeVersion}\"`));
  assert.ok(dockerfile.includes(`org.osmantic.pixel.pyright-version=\"${runtime.features.pyright.runtimeVersion}\"`));
  assert.ok(dockerfile.includes(`org.osmantic.pixel.debugpy-version=\"${runtime.features.debugpy.runtimeVersion}\"`));
  assert.match(dockerfile, /test "\$\(gdb --version \| head -n 1\)" = "GNU gdb \(Debian 13\.1-3\) 13\.1"/);
  assert.match(dockerfile, /test "\$\(python --version 2>&1\)" = "Python 3\.11\.2"/);
  assert.match(dockerfile, /pyright --version\)" = "pyright 1\.1\.411"/);
  assert.match(dockerfile, /find_spec\("debugpy\.adapter"\)/);
  assert.equal(sha256(agent), runtime.agentSurface.definitionSha256);
  assert.equal(sha256(entrypoint), runtime.agentSurface.entrypointSha256);
  assert.ok(dockerfile.includes(`COPY --chmod=0444 deploy/work-runner/builder-agent.md ${runtime.agentSurface.definitionPath}`));
  assert.ok(dockerfile.includes(`COPY --chmod=0555 deploy/work-runner/builder-entrypoint.sh ${runtime.agentSurface.entrypointPath}`));
  const agentText = agent.toString("utf8");
  assert.ok(agentText.includes(`tools: ${runtime.agentSurface.declaredTools.join(", ")}`));
  for (const tool of runtime.agentSurface.blockedTools) assert.doesNotMatch(agentText.split("---", 3)[1], new RegExp(`(?:^|, )${tool}(?:,|$)`, "u"));
  const entrypointText = entrypoint.toString("utf8");
  assert.ok(entrypointText.includes(`[ "\${PI_CONFIG_DIR:-}" = "${runtime.agentSurface.configurationDirectory}" ]`));
  assert.ok(entrypointText.includes(`[ "\${PI_CODING_AGENT_DIR:-}" = "${runtime.agentSurface.stateDirectory}" ]`));
  assert.ok(entrypointText.includes("/tmp/home/.pixel-omp/agent/agents/task.md"));
});

test("Builder runtime rejects missing, reordered, substituted, and unbound packages", async () => {
  const { runtime } = await fixture();
  const reordered = structuredClone(runtime);
  reordered.systemPackages.reverse();
  assert.match(validateWorkBuilderRuntime(reordered).join("; "), /complete and canonically ordered/);
  const substituted = structuredClone(runtime);
  substituted.systemPackages[1].version = "1.6.5";
  assert.match(validateWorkBuilderRuntime(substituted).join("; "), /package version differs/);
  const widened = structuredClone(runtime);
  widened.systemPackages.push({ name: "gdb", version: "13.1-3" });
  assert.notDeepEqual(validateWorkBuilderRuntime(widened), []);
  const underqualified = structuredClone(runtime);
  underqualified.minimumMemoryMiB = 1024;
  assert.match(validateWorkBuilderRuntime(underqualified).join("; "), /minimumMemoryMiB/);
  const recursive = structuredClone(runtime);
  recursive.agentSurface.runtimeTools[0] = "task";
  assert.notDeepEqual(validateWorkBuilderRuntime(recursive), []);
});
