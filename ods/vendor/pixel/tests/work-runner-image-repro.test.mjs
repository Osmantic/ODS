import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("Work runner build removes known nondeterminism and qualifies two exact loadable images", async () => {
  const [dockerfile, qualifier, manifest] = await Promise.all([
    read("deploy/work-runner/Dockerfile"), read("scripts/qualify-work-runner-image.sh"),
    read("RELEASE-MANIFEST.json").then(JSON.parse),
  ]);
  assert.match(dockerfile, /rm -f \/var\/cache\/ldconfig\/aux-cache \/var\/log\/alternatives\.log \/var\/log\/apt\/history\.log \/var\/log\/apt\/term\.log \/var\/log\/dpkg\.log/u);
  assert.match(dockerfile, /PYTHONDONTWRITEBYTECODE=1[\s\S]*python3 -B -c/u);
  assert.match(dockerfile, /find \/opt\/pixel\/data-runtime -type d -name __pycache__ -prune -exec rm -rf \{\} \+/u);
  for (const source of ["model-qualification.mjs", "model-qualification-runner.mjs", "model-qualification-bridge.mjs", "model-qualification-container.mjs"]) {
    assert.match(dockerfile, new RegExp(`COPY --chmod=0444 deploy/work-controller/${source.replaceAll(".", "\\.")} /opt/pixel/deploy/work-controller/${source.replaceAll(".", "\\.")}`, "u"));
  }
  assert.match(dockerfile, /COPY --chmod=0444 deploy\/agent-comparison\/research-mcp-server\.mjs \/opt\/pixel\/deploy\/agent-comparison\/research-mcp-server\.mjs/u);
  assert.match(dockerfile, /COPY --chmod=0444 deploy\/work-runner\/research-tool\.mjs \/opt\/pixel\/deploy\/work-runner\/research-tool\.mjs/u);
  assert.match(dockerfile, /COPY --chmod=0444 schemas\/\*\.json \/opt\/pixel\/schemas\//u);
  assert.match(qualifier, /moby\/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec/u);
  for (const value of ["--no-cache", "--pull=false", "--provenance=false", "SOURCE_DATE_EPOCH=0", "type=docker", "rewrite-timestamp=true", "cmp --silent", "docker load --input"]) assert.ok(qualifier.includes(value), value);
  assert.match(qualifier, /pixel-work-runner-repro\.\*/u);
  assert.match(qualifier, /without starting a container, using a credential, or calling a provider/u);
  assert.equal(manifest.qualification.workRunnerImageQualifier, "./scripts/qualify-work-runner-image.sh");
});
