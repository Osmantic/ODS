import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dockerfileUrl = new URL("../deploy/agent-comparison/Dockerfile.dsv4-vllm", import.meta.url);
const launcherUrl = new URL("../deploy/agent-comparison/dsv4-serve.sh", import.meta.url);
const qualifierUrl = new URL("../scripts/qualify-dsv4-image.sh", import.meta.url);

test("DSV4 qualification image pins the known-good product launcher by content", async () => {
  const [dockerfile, launcher] = await Promise.all([readFile(dockerfileUrl, "utf8"), readFile(launcherUrl)]);
  const launcherSha256 = createHash("sha256").update(launcher).digest("hex");
  assert.match(dockerfile, new RegExp(`ARG DSV4_SERVE_SHA256=${launcherSha256}`, "u"));
  assert.match(dockerfile, /ENTRYPOINT \["\/usr\/local\/bin\/pixel-dsv4-serve"\]/u);
  assert.match(dockerfile, /FROM voipmonitor\/vllm@sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f/u);
  assert.match(dockerfile, /touch -d '@0'[\s\S]*\/usr\/local\/bin\/pixel-dsv4-serve \\\n\s+\/usr\/local\/bin \\/u);
});

test("DSV4 launcher binds the measured model mount and product-quality server mechanics", async () => {
  const launcher = await readFile(launcherUrl, "utf8");
  for (const required of [
    '[ "$model_path" = "/models/model" ]',
    "/opt/venv/bin/vllm serve",
    "--trust-remote-code",
    "--kv-cache-dtype fp8",
    "--load-format instanttensor",
    "--enable-chunked-prefill",
    "--tokenizer-mode deepseek_v4",
    "--enable-force-include-usage",
    "--enable-prefix-caching",
    "--speculative-config",
    "--attention-backend B12X_MLA_SPARSE",
    "--moe-backend b12x",
    "--linear-backend b12x",
    "--disable-custom-all-reduce",
    "--override-generation-config '{\"top_p\":0.95}'",
  ]) assert.ok(launcher.includes(required), required);
  assert.doesNotMatch(launcher, /--override-generation-config\s+'[^']*"temperature"/u, "request temperature must not be overridden globally");
  assert.doesNotMatch(launcher, /(?:curl|wget|HF_TOKEN|API_KEY|https?:\/\/)/u);
});

test("DSV4 image qualification emits one byte-reproducible loaded identity without model execution", async () => {
  const [qualifier, manifest] = await Promise.all([
    readFile(qualifierUrl, "utf8"),
    readFile(new URL("../RELEASE-MANIFEST.json", import.meta.url), "utf8").then(JSON.parse),
  ]);
  assert.match(qualifier, /moby\/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec/u);
  assert.match(qualifier, /WORKSPACE_PATCH_SHA256='50b5a02e83a419e6da309efb2f78580b72e5b04c57babf6d34854ef3d3fb6dbe'/u);
  for (const value of [
    "--workspace-patch", "--no-cache", "--pull=false", "--provenance=false", "SOURCE_DATE_EPOCH=0",
    "type=docker", "rewrite-timestamp=true", "cmp --silent", "docker load --input", "1970-01-01T00:00:00Z",
  ]) assert.ok(qualifier.includes(value), value);
  assert.doesNotMatch(qualifier, /docker\s+(?:run|start)\b/u);
  assert.match(qualifier, /without starting a container, mounting the model, using a credential, or calling a provider/u);
  assert.equal(manifest.qualification.portalOutcomeDsv4ImageQualifier, "./scripts/qualify-dsv4-image.sh");
});
