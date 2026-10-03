import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");

const manifest = JSON.parse(await read("RELEASE-MANIFEST.json"));
const runner = await read("deploy/work-codex-provider/Dockerfile");
const proxy = await read("deploy/work-codex-provider/Dockerfile.egress-proxy");
const comparison = await read("deploy/agent-comparison/Dockerfile.codex-runner");
const entrypoint = await read("deploy/work-codex-provider/container-entrypoint.mjs");
const proxySource = await read("deploy/work-codex-provider/egress-proxy.mjs");
const qualifier = await read("scripts/qualify-work-codex-images.sh");

function assertSharedPinnedRuntime(source) {
  assert.ok(source.startsWith(`ARG SOURCE_DATE_EPOCH=0\nFROM ${manifest.baseImage}\n`));
  assert.match(source, new RegExp(`ADD --checksum=sha256:${manifest.nodeRuntime.sha256} ${manifest.nodeRuntime.url.replaceAll(".", "\\.")} `, "u"));
  assert.match(source, /apt-get install -y --no-install-recommends [^\n]+=[^\s\\]+/u);
  assert.doesNotMatch(source, /(?:^|\s)(?:npm|npx|pnpm|yarn)\s+(?:install|add|exec)\b/mu);
  assert.doesNotMatch(source, /(?:FROM|ADD|COPY)[^\n]*(?::latest|@latest)\b/u);
  assert.doesNotMatch(source, /(?:curl|wget)\s/u);
}

test("isolated runner image pins the complete Codex supply chain and current entrypoint", () => {
  assertSharedPinnedRuntime(runner);
  assert.match(runner, /ADD --checksum=sha256:d28b4fd4bd9f07ea71083d0cc40c579595cebbd4c10bc8ca98a6d385432e7255 https:\/\/registry\.npmjs\.org\/@openai\/codex\/-\/codex-0\.147\.0\.tgz/u);
  assert.match(runner, /ADD --checksum=sha256:c969740cf8297e4c31905cd551efeb2c99af5080c12c236bdf825598b250139a https:\/\/registry\.npmjs\.org\/@openai\/codex\/-\/codex-0\.147\.0-linux-x64\.tgz/u);
  assert.match(runner, /org\.osmantic\.pixel\.codex-package-integrity="sha512-EQLEXecAG2ptxI7UpBMo2TR\/ga5596\/c\/OsYF\/0LoUDh5JANZ7IoGqlzBEWbuEVQ76JePIbtTW\/ihCkp1a7Z3w=="/u);
  assert.match(runner, /org\.osmantic\.pixel\.codex-linux-x64-integrity="sha512-0W9MBxPpWW0cSkNqrTDN2jR7rzzT7oNMhQY5446lT2Lw5cz5yhDTck4Va9rjkQEm\+HlFzP\/dmEMSZbXfJsINmw=="/u);
  const entrypointHash = sha256(entrypoint);
  assert.match(runner, new RegExp(`echo '${entrypointHash}  /opt/pixel/deploy/work-codex-provider/container-entrypoint\\.mjs'`, "u"));
  assert.match(runner, new RegExp(`org\\.osmantic\\.pixel\\.entrypoint-sha256="${entrypointHash}"`, "u"));
  assert.match(runner, /test "\$\(HOME=\/tmp\/codex-version-home CODEX_HOME=\/tmp\/codex-version-home \/usr\/local\/bin\/codex --version\)" = "codex-cli 0\.147\.0"/u);
  assert.match(runner, /CODEX_HOME=\/tmp\/codex-version-home/u);
  assert.match(runner, /rm -rf \/tmp\/codex-version-home \/root\/\.codex/u);
  assert.match(runner, /ENTRYPOINT \["\/opt\/node\/bin\/node", "\/opt\/pixel\/deploy\/work-codex-provider\/container-entrypoint\.mjs"\]/u);
});

test("egress image pins the current proxy and has an immutable non-root runtime", () => {
  assertSharedPinnedRuntime(proxy);
  const sourceHash = sha256(proxySource);
  assert.match(proxy, new RegExp(`echo '${sourceHash}  /opt/pixel/deploy/work-codex-provider/egress-proxy\\.mjs'`, "u"));
  assert.match(proxy, new RegExp(`org\\.osmantic\\.pixel\\.egress-proxy-sha256="${sourceHash}"`, "u"));
  assert.match(proxy, /USER 65532:65532/u);
  assert.match(proxy, /useradd [^\n]+--no-log-init/u);
  assert.match(proxy, /ENTRYPOINT \["\/opt\/node\/bin\/node", "\/opt\/pixel\/deploy\/work-codex-provider\/egress-proxy\.mjs"\]/u);
  assert.doesNotMatch(proxy, /(?:ca-certificates|openssl|libssl)/u);
});

test("comparison runner gives Codex the exact backend-neutral Pixel coding and data toolchain", () => {
  assertSharedPinnedRuntime(comparison);
  for (const value of [
    "codex-0.147.0.tgz", "codex-0.147.0-linux-x64.tgz", "gdb=13.1-3", "python3=3.11.2-1+b1",
    "python3-debugpy=1.6.6+ds-1", "sqlite3=3.40.1-2+deb12u2", "pyright-1.1.411.tgz",
    "duckdb-1.5.5", "polars-1.43.2", "pixel-codex-comparison-runtime-v1",
  ]) assert.ok(comparison.includes(value), value);
  assert.match(comparison, /ENV PATH=\/opt\/node\/bin:[^\n]+\\\n    PYTHONPATH=\/opt\/pixel\/data-runtime/u);
  assert.match(comparison, /USER 1000:1000/u);
  assert.match(comparison, /COPY --chmod=0555 scripts\/codex_comparison_workspace\.py \/opt\/pixel\/scripts\/codex_comparison_workspace\.py/u);
  assert.match(comparison, /ENTRYPOINT \["\/usr\/local\/bin\/codex"\]/u);
  assert.doesNotMatch(comparison, /(?:auth\.json|OPENAI_API_KEY|CODEX_API_KEY|credential|secret)/iu);
});

test("both images copy only repository-controlled runtime inputs and carry no credential", () => {
  for (const source of [runner, proxy]) {
    assert.match(source, /COPY --chmod=0444 schemas\/\*\.json \/opt\/pixel\/schemas\//u);
    for (const line of source.split("\n").filter((value) => value.startsWith("COPY "))) {
      assert.match(line, /^COPY (?:--chmod=0[45]{3} )?(?:deploy\/work-codex-provider\/|scripts\/lib\/|schemas\/)/u);
    }
    assert.doesNotMatch(source, /(?:auth\.json|OPENAI_API_KEY|CODEX_API_KEY|credential|secret)/iu);
  }
});

test("image qualification pins its builder and emits loaded reproducible image identities", () => {
  assert.match(qualifier, /moby\/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec/u);
  for (const value of ["--no-cache", "--pull=false", "--provenance=false", "SOURCE_DATE_EPOCH=0", "type=docker", "rewrite-timestamp=true", "cmp --silent", "docker load", "reproducible image ID", "Dockerfile.egress-proxy", "Dockerfile.codex-runner"]) assert.ok(qualifier.includes(value), value);
  assert.match(qualifier, /first_id=.*Loaded image ID: sha256:\[a-f0-9\]\{64\}/u);
  assert.match(qualifier, /first_id.*!=.*second_id/u);
  assert.match(qualifier, /pixel-codex-repro\.\*/u);
});
