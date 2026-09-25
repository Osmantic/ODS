import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
const manifest = JSON.parse(await read("RELEASE-MANIFEST.json"));
const proxy = await read("deploy/work-provider/Dockerfile.egress-proxy");
const worker = await read("deploy/work-provider/Dockerfile.moonshot-worker");
const proxySource = await read("deploy/work-provider/egress-proxy.mjs");
const entrypoint = await read("deploy/work-provider/moonshot-container-entrypoint.mjs");
const launcher = await read("deploy/work-provider/run-moonshot-smoke-container.sh");
const workerPinnedSources = [
  "deploy/work-provider/adapter-contract.mjs",
  "deploy/work-provider/adapters/openai-chat.mjs",
  "deploy/work-provider/adapters/openai-responses.mjs",
  "deploy/work-provider/credential-custody.mjs",
  "deploy/work-provider/moonshot-transport.mjs",
  "deploy/work-provider/moonshot-smoke-cli.mjs",
  "deploy/work-provider/moonshot-container-entrypoint.mjs",
];

function pinned(source) {
  assert.ok(source.startsWith(`ARG SOURCE_DATE_EPOCH=0\nFROM ${manifest.baseImage}\n`));
  assert.match(source, new RegExp(`ADD --checksum=sha256:${manifest.nodeRuntime.sha256} ${manifest.nodeRuntime.url.replaceAll(".", "\\.")} `, "u"));
  assert.match(source, /apt-get install -y --no-install-recommends [^\n]+=[^\s\\]+/u);
  assert.doesNotMatch(source, /(?:^|\s)(?:npm|npx|pnpm|yarn|curl|wget)\s+/mu);
  assert.match(source, /USER 1000:1000/u);
  assert.doesNotMatch(source, /(?:MOONSHOT_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY)/u);
}

test("provider images pin source, runtime, non-root identity, and credential-free build inputs", async () => {
  pinned(proxy); pinned(worker);
  const proxyHash = sha256(proxySource), entrypointHash = sha256(entrypoint);
  for (const source of [proxy, worker]) {
    assert.match(source, /COPY --chmod=0444 deploy\/work-provider\/\*\.mjs/u);
    assert.match(source, /COPY --chmod=0444 schemas\/\*\.json/u);
  }
  assert.match(proxy, new RegExp(`${proxyHash}  /opt/pixel/deploy/work-provider/egress-proxy\\.mjs`, "u"));
  assert.match(proxy, new RegExp(`org\\.osmantic\\.pixel\\.egress-proxy-sha256="${proxyHash}"`, "u"));
  assert.match(worker, new RegExp(`${entrypointHash}  /opt/pixel/deploy/work-provider/moonshot-container-entrypoint\\.mjs`, "u"));
  assert.match(worker, new RegExp(`org\\.osmantic\\.pixel\\.entrypoint-sha256="${entrypointHash}"`, "u"));
  for (const path of workerPinnedSources) {
    const hash = sha256(await read(path));
    assert.match(worker, new RegExp(`${hash}  /opt/pixel/${path.replaceAll(".", "\\.")}`, "u"), `stale Moonshot worker pin for ${path}`);
  }
  const moonshotTransportHash = sha256(await read("deploy/work-provider/moonshot-transport.mjs"));
  assert.match(worker, new RegExp(`org\\.osmantic\\.pixel\\.moonshot-transport-sha256="${moonshotTransportHash}"`, "u"));
  assert.match(proxy, /ENTRYPOINT \["\/opt\/node\/bin\/node", "\/opt\/pixel\/deploy\/work-provider\/egress-proxy\.mjs"\]/u);
  assert.match(worker, /ENTRYPOINT \["\/opt\/node\/bin\/node", "\/opt\/pixel\/deploy\/work-provider\/moonshot-container-entrypoint\.mjs"\]/u);
});

test("container launcher gives only the proxy an external network and never projects a key through argv or environment", () => {
  for (const value of [
    "network create --internal", "network connect", "pixel-provider-egress", "--read-only", "--cap-drop ALL",
    "--security-opt no-new-privileges", "dst=/run/pixel-credential,readonly", "dst=/state/ledger",
  ]) assert.ok(launcher.includes(value), value);
  assert.match(launcher, /docker run [^\n]*--network "\$internal_network"/u);
  assert.doesNotMatch(launcher, /(?:MOONSHOT_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|provider-key\)"|cat .*provider-key)/u);
  assert.match(launcher, /\^sha256:\[a-f0-9\]\{64\}\$/u);
  assert.match(launcher, /stat -c '%u:%a:%h:%F'/u);
  assert.ok(launcher.includes("moonshot-kimi-key"), "launcher must bind the Moonshot ingress filename");
  assert.ok(launcher.includes("provider-key"), "launcher must preserve the deliberate legacy filename");
  assert.ok(launcher.includes("[[ -z \"$credential_file\" ]] || fail"), "launcher must reject two simultaneous credential files");
  assert.ok(launcher.includes("PIXEL_WORK_PROVIDER_CREDENTIAL_FILE=$credential_file"), "launcher must pass only the selected non-secret filename");
  assert.ok(entrypoint.includes("new Set([\"moonshot-kimi-key\", \"provider-key\"])"), "entrypoint must keep a closed current-and-legacy filename set");
  assert.ok(entrypoint.includes("`/run/pixel-credential/${credentialFile}`"), "entrypoint must derive only the closed mounted path");
});
