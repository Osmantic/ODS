import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertJsonSchema } from "./lib/json-schema.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const mode = process.argv[2] ?? "--check";
if (!["--check", "--write"].includes(mode) || process.argv.length > 3) {
  throw new Error("Usage: node scripts/generate-release-files.mjs [--check | --write]");
}

const read = (path) => readFile(join(root, path), "utf8");
const manifest = JSON.parse(await read("RELEASE-MANIFEST.json"));
const compatibility = JSON.parse(await read("OPENCLAW-COMPATIBILITY.json"));
const manifestSchema = JSON.parse(await read("schemas/release-manifest-v1.schema.json"));
const compatibilitySchema = JSON.parse(await read("schemas/openclaw-compatibility-v1.schema.json"));
assertJsonSchema(manifest, manifestSchema, "RELEASE-MANIFEST.json");
assertJsonSchema(compatibility, compatibilitySchema, "OPENCLAW-COMPATIBILITY.json");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function json(value) {
  return `${JSON.stringify(value, null, 2)}\n`;
}

function replaceEnv(text, key, value) {
  const pattern = new RegExp(`^${key}=.*$`, "m");
  assert(pattern.test(text), `.env.example is missing ${key}`);
  return text.replace(pattern, `${key}=${shellQuote(value)}`);
}

function replaceRequired(text, pattern, replacement, label) {
  assert(pattern.test(text), `${label} generation anchor is missing`);
  return text.replace(pattern, replacement);
}

function normalizedEntries(value) {
  return Object.entries(value ?? {}).sort(([left], [right]) => left.localeCompare(right));
}

assert(manifest.$schema === "./schemas/release-manifest-v1.schema.json", "release manifest schema path is invalid");
assert(manifest.schemaVersion === 1, "release manifest schemaVersion must be 1");
assert(/^\d+\.\d+\.\d+$/.test(manifest.pixel), "Pixel version is not semantic versioning");
assert(/^\d{4}\.\d+\.\d+(?:-\d+)?$/.test(manifest.openclaw), "OpenClaw version is invalid");
assert(/^>=?\d+$/.test(manifest.node), "Node runtime requirement is invalid");
assert(/^22\.\d+\.\d+$/.test(manifest.nodeRuntime.version), "Node qualification runtime version is invalid");
assert(manifest.nodeRuntime.url === `https://nodejs.org/dist/v${manifest.nodeRuntime.version}/node-v${manifest.nodeRuntime.version}-linux-x64.tar.xz`, "Node qualification runtime URL does not match its version");
assert(/^[0-9a-f]{64}$/.test(manifest.nodeRuntime.sha256), "Node qualification runtime SHA-256 is invalid");
assert(/@sha256:[0-9a-f]{64}$/.test(manifest.baseImage), "base image is not digest pinned");
for (const [name, image] of Object.entries(manifest.referenceImages ?? {})) {
  assert(/@sha256:[0-9a-f]{64}$/.test(image), `${name} reference image is not digest pinned`);
}
for (const [name, artifact] of Object.entries({
  openclawInstaller: manifest.openclawInstaller,
  openclawPackage: manifest.openclawPackage,
  discord: manifest.openclawPluginPackages?.discord,
  searxng: manifest.openclawPluginPackages?.searxng,
  llamaCpp: manifest.openclawPluginPackages?.llamaCpp,
})) {
  assert(artifact?.url?.startsWith("https://"), `${name} URL must use HTTPS`);
  assert(/^[0-9a-f]{64}$/.test(artifact?.sha256 ?? ""), `${name} SHA-256 is invalid`);
  if (artifact?.mirrors !== undefined) {
    assert(Array.isArray(artifact.mirrors), `${name} mirrors must be an array`);
    assert(artifact.mirrors.length <= 4, `${name} has too many mirrors`);
    assert(new Set(artifact.mirrors).size === artifact.mirrors.length, `${name} mirrors must be unique`);
    for (const mirror of artifact.mirrors) {
      assert(typeof mirror === "string" && mirror.startsWith("https://"), `${name} mirror must use HTTPS`);
    }
  }
}
for (const [name, artifact] of Object.entries({ openclaw: manifest.openclawPackage, ...manifest.openclawPluginPackages })) {
  assert(/^sha512-[A-Za-z0-9+/]+=*$/.test(artifact.integrity), `${name} npm integrity is invalid`);
}
assert(manifest.openclawPackage.url.endsWith(`/openclaw-${manifest.openclaw}.tgz`), "OpenClaw package URL does not match its version");
for (const [name, packageKey] of Object.entries({
  "@openclaw/discord": "discord",
  "@openclaw/searxng-plugin": "searxng",
  "@openclaw/llama-cpp-provider": "llamaCpp",
})) {
  const archiveName = name.slice(name.lastIndexOf("/") + 1);
  assert(manifest.openclawPluginPackages[packageKey].url.endsWith(`/${archiveName}-${manifest.openclawPlugins[name]}.tgz`), `${name} package URL does not match its version`);
}
assert(compatibility.$schema === "./schemas/openclaw-compatibility-v1.schema.json", "compatibility schema path is invalid");
assert(compatibility.schemaVersion === 1 && Array.isArray(compatibility.combinations), "compatibility data is invalid");
const combination = compatibility.combinations.find((item) =>
  item.pixel === manifest.pixel
  && item.openclaw === manifest.openclaw
  && JSON.stringify(normalizedEntries(item.plugins)) === JSON.stringify(normalizedEntries(manifest.openclawPlugins))
  && ["supported", "candidate"].includes(item.status));
assert(combination, "release manifest combination is absent from the compatibility matrix");

const constants = {
  schemaVersion: 1,
  pixel: manifest.pixel,
  node: manifest.node,
  nodeRuntime: manifest.nodeRuntime,
  openclaw: manifest.openclaw,
  openclawInstaller: { ...manifest.openclawInstaller, mirrors: manifest.openclawInstaller.mirrors ?? [] },
  openclawPackage: manifest.openclawPackage,
  openclawPlugins: manifest.openclawPlugins,
  openclawPluginPackages: manifest.openclawPluginPackages,
  sandboxImage: manifest.sandboxImage,
  baseImage: manifest.baseImage,
  referenceImages: manifest.referenceImages,
};

const installerMirrorEnv = (constants.openclawInstaller.mirrors ?? []).map((mirror) => shellQuote(mirror)).join(" ");

const env = `# Generated by node scripts/generate-release-files.mjs --write. Do not edit.\n\
PIXEL_GENERATED_RELEASE_SCHEMA_VERSION=${shellQuote(constants.schemaVersion)}\n\
PIXEL_GENERATED_RELEASE_VERSION=${shellQuote(constants.pixel)}\n\
PIXEL_GENERATED_NODE_REQUIREMENT=${shellQuote(constants.node)}\n\
PIXEL_GENERATED_NODE_RUNTIME_VERSION=${shellQuote(constants.nodeRuntime.version)}\n\
PIXEL_GENERATED_NODE_RUNTIME_URL=${shellQuote(constants.nodeRuntime.url)}\n\
PIXEL_GENERATED_NODE_RUNTIME_SHA256=${shellQuote(constants.nodeRuntime.sha256)}\n\
PIXEL_GENERATED_OPENCLAW_VERSION=${shellQuote(constants.openclaw)}\n\
PIXEL_GENERATED_OPENCLAW_INSTALLER_URL=${shellQuote(constants.openclawInstaller.url)}\n\
PIXEL_GENERATED_OPENCLAW_INSTALLER_SHA256=${shellQuote(constants.openclawInstaller.sha256)}\n\
PIXEL_GENERATED_OPENCLAW_INSTALLER_MIRRORS=(${installerMirrorEnv})\n\
PIXEL_GENERATED_OPENCLAW_PACKAGE_URL=${shellQuote(constants.openclawPackage.url)}\n\
PIXEL_GENERATED_OPENCLAW_PACKAGE_SHA256=${shellQuote(constants.openclawPackage.sha256)}\n\
PIXEL_GENERATED_OPENCLAW_PACKAGE_INTEGRITY=${shellQuote(constants.openclawPackage.integrity)}\n\
PIXEL_GENERATED_DISCORD_PLUGIN_VERSION=${shellQuote(constants.openclawPlugins["@openclaw/discord"])}\n\
PIXEL_GENERATED_DISCORD_PLUGIN_URL=${shellQuote(constants.openclawPluginPackages.discord.url)}\n\
PIXEL_GENERATED_DISCORD_PLUGIN_SHA256=${shellQuote(constants.openclawPluginPackages.discord.sha256)}\n\
PIXEL_GENERATED_DISCORD_PLUGIN_INTEGRITY=${shellQuote(constants.openclawPluginPackages.discord.integrity)}\n\
PIXEL_GENERATED_SEARXNG_PLUGIN_VERSION=${shellQuote(constants.openclawPlugins["@openclaw/searxng-plugin"])}\n\
PIXEL_GENERATED_SEARXNG_PLUGIN_URL=${shellQuote(constants.openclawPluginPackages.searxng.url)}\n\
PIXEL_GENERATED_SEARXNG_PLUGIN_SHA256=${shellQuote(constants.openclawPluginPackages.searxng.sha256)}\n\
PIXEL_GENERATED_SEARXNG_PLUGIN_INTEGRITY=${shellQuote(constants.openclawPluginPackages.searxng.integrity)}\n\
PIXEL_GENERATED_LLAMA_PLUGIN_VERSION=${shellQuote(constants.openclawPlugins["@openclaw/llama-cpp-provider"])}\n\
PIXEL_GENERATED_LLAMA_PLUGIN_URL=${shellQuote(constants.openclawPluginPackages.llamaCpp.url)}\n\
PIXEL_GENERATED_LLAMA_PLUGIN_SHA256=${shellQuote(constants.openclawPluginPackages.llamaCpp.sha256)}\n\
PIXEL_GENERATED_LLAMA_PLUGIN_INTEGRITY=${shellQuote(constants.openclawPluginPackages.llamaCpp.integrity)}\n\
PIXEL_GENERATED_SANDBOX_IMAGE=${shellQuote(constants.sandboxImage)}\n\
PIXEL_GENERATED_BASE_IMAGE=${shellQuote(constants.baseImage)}\n\
PIXEL_GENERATED_SEARXNG_IMAGE=${shellQuote(constants.referenceImages.searxng)}\n\
PIXEL_GENERATED_LLAMA_IMAGE=${shellQuote(constants.referenceImages.llamaCpp)}\n`;

const statusOrder = { supported: 0, candidate: 1, blocked: 2, retired: 3 };
const rows = [...compatibility.combinations]
  .sort((left, right) => (statusOrder[left.status] - statusOrder[right.status]) || right.qualifiedAt.localeCompare(left.qualifiedAt))
  .map((item) => {
    const plugins = Object.entries(item.plugins).map(([name, version]) => `${name}@${version}`).join("<br>");
    const attestation = item.evidence.attestationSha256 ? ` / attestation \`${item.evidence.attestationSha256.slice(0, 12)}\`` : "";
    return `| ${item.pixel} | ${item.openclaw} | ${plugins} | ${item.status} | ${item.qualifiedAt} | [audit](${item.evidence.liveAudit}) / \`${item.evidence.sourceCommit.slice(0, 12)}\`${attestation} |`;
  });
const table = `# Pixel / OpenClaw compatibility\n\nThis table is generated from \`OPENCLAW-COMPATIBILITY.json\`. Do not edit it directly. The evidence commit is the qualified functional source; a signed release envelope separately binds it to the exact later packaged commit through an evidence-only Git delta.\n\n| Pixel | OpenClaw | Official plugins | State | Qualified | Evidence |\n|---|---|---|---|---|---|\n${rows.join("\n")}\n`;

const thirdPartyNotices = `<!-- Generated by node scripts/generate-release-files.mjs --write. Do not edit. -->

# Third-Party Notices

Pixel is source-available for use within ODS under the ODS-specific terms in
[LICENSE.md](LICENSE.md). This notice covers the official OpenClaw
runtime packages used by Pixel. Other third-party software and services remain
governed by their respective upstream terms.

## OpenClaw

Pixel uses the following official OpenClaw packages at the versions pinned in
\`RELEASE-MANIFEST.json\`:

- \`openclaw\` ${manifest.openclaw}
- \`@openclaw/discord\` ${manifest.openclawPlugins["@openclaw/discord"]}
- \`@openclaw/searxng-plugin\` ${manifest.openclawPlugins["@openclaw/searxng-plugin"]}
- \`@openclaw/llama-cpp-provider\` ${manifest.openclawPlugins["@openclaw/llama-cpp-provider"]}

These packages remain governed by their upstream terms and are not relicensed
under Pixel's ODS-specific license. They are maintained in the
[OpenClaw repository](https://github.com/openclaw/openclaw/tree/v${manifest.openclaw}),
which identifies the pinned release as MIT-licensed:

- [MIT license](https://github.com/openclaw/openclaw/blob/v${manifest.openclaw}/LICENSE)
- [OpenClaw third-party notices](https://github.com/openclaw/openclaw/blob/v${manifest.openclaw}/THIRD_PARTY_NOTICES.md)

The upstream MIT notice is reproduced below.

### MIT License

Copyright (c) 2026 OpenClaw Foundation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

OpenClaw's package includes its upstream \`LICENSE\` and
\`THIRD_PARTY_NOTICES.md\` files, including notices for third-party material
incorporated into OpenClaw itself.
`;

const outputs = new Map([
  [".node-version", `${manifest.nodeRuntime.version}\n`],
  ["scripts/generated/release-constants.json", json(constants)],
  ["scripts/generated/release.env", env],
  ["OPENCLAW-COMPATIBILITY.md", table],
  ["THIRD_PARTY_NOTICES.md", thirdPartyNotices],
  ["VERSION", `${manifest.pixel}\n`],
]);

let envExample = await read(".env.example");
for (const [key, value] of Object.entries({
  PIXEL_RELEASE_VERSION: manifest.pixel,
  PIXEL_OPENCLAW_VERSION: manifest.openclaw,
  PIXEL_DISCORD_PLUGIN_VERSION: manifest.openclawPlugins["@openclaw/discord"],
  PIXEL_SEARXNG_PLUGIN_VERSION: manifest.openclawPlugins["@openclaw/searxng-plugin"],
  PIXEL_LLAMA_CPP_PLUGIN_VERSION: manifest.openclawPlugins["@openclaw/llama-cpp-provider"],
  PIXEL_SEARXNG_IMAGE: manifest.referenceImages.searxng,
  PIXEL_SANDBOX_IMAGE: manifest.sandboxImage,
  PIXEL_LLAMA_IMAGE: manifest.referenceImages.llamaCpp,
})) envExample = replaceEnv(envExample, key, value);
outputs.set(".env.example", envExample);

for (const path of [
  "plugin/package.json", "plugin/package-lock.json", "plugin/openclaw.plugin.json",
  "plugin-ops/package.json", "plugin-ops/package-lock.json", "plugin-ops/openclaw.plugin.json",
  "plugin-frontier/package.json", "plugin-frontier/package-lock.json", "plugin-frontier/openclaw.plugin.json",
]) {
  const original = await read(path);
  const value = JSON.parse(original);
  let changed = false;
  if (value.version !== manifest.pixel) { value.version = manifest.pixel; changed = true; }
  if (value.packages?.[""] && value.packages[""].version !== manifest.pixel) {
    value.packages[""].version = manifest.pixel;
    changed = true;
  }
  outputs.set(path, changed ? json(value) : original);
}

let dockerfile = await read("deploy/sandbox/Dockerfile");
dockerfile = replaceRequired(dockerfile, /^FROM .*$/m, `FROM ${manifest.baseImage}`, "sandbox Dockerfile base image");
outputs.set("deploy/sandbox/Dockerfile", dockerfile);

let workRunnerDockerfile = await read("deploy/work-runner/Dockerfile");
workRunnerDockerfile = replaceRequired(workRunnerDockerfile, /^FROM .* AS omp-native-extractor$/m, `FROM ${manifest.baseImage} AS omp-native-extractor`, "work runner extractor base image");
workRunnerDockerfile = replaceRequired(workRunnerDockerfile, /^FROM (?!.* AS omp-native-extractor$).*$/m, `FROM ${manifest.baseImage}`, "work runner final base image");
workRunnerDockerfile = replaceRequired(
  workRunnerDockerfile,
  /^ADD --checksum=sha256:[a-f0-9]{64} https:\/\/nodejs\.org\/dist\/v[^ ]+\/node-v[^ ]+-linux-x64\.tar\.xz \/tmp\/node-runtime\.tar\.xz$/m,
  `ADD --checksum=sha256:${manifest.nodeRuntime.sha256} ${manifest.nodeRuntime.url} /tmp/node-runtime.tar.xz`,
  "work runner Node runtime",
);
workRunnerDockerfile = replaceRequired(workRunnerDockerfile, /test "\$\(\/opt\/node\/bin\/node --version\)" = "v[^"]+"/, `test "$(/opt/node/bin/node --version)" = "v${manifest.nodeRuntime.version}"`, "work runner Node version");
workRunnerDockerfile = replaceRequired(workRunnerDockerfile, /org\.osmantic\.pixel\.node-runtime="[^"]+"/, `org.osmantic.pixel.node-runtime="${manifest.nodeRuntime.version}"`, "work runner Node label");
workRunnerDockerfile = replaceRequired(workRunnerDockerfile, /org\.opencontainers\.image\.version="[^"]+"/, `org.opencontainers.image.version="${manifest.pixel}"`, "work runner Pixel version");
outputs.set("deploy/work-runner/Dockerfile", workRunnerDockerfile);

let codexRunnerDockerfile = await read("deploy/work-codex-provider/Dockerfile");
codexRunnerDockerfile = replaceRequired(codexRunnerDockerfile, /^FROM .*$/m, `FROM ${manifest.baseImage}`, "Codex runner base image");
codexRunnerDockerfile = replaceRequired(
  codexRunnerDockerfile,
  /^ADD --checksum=sha256:[a-f0-9]{64} https:\/\/nodejs\.org\/dist\/v[^ ]+\/node-v[^ ]+-linux-x64\.tar\.xz \/tmp\/node-runtime\.tar\.xz$/m,
  `ADD --checksum=sha256:${manifest.nodeRuntime.sha256} ${manifest.nodeRuntime.url} /tmp/node-runtime.tar.xz`,
  "Codex runner Node runtime",
);
codexRunnerDockerfile = replaceRequired(codexRunnerDockerfile, /test "\$\(\/opt\/node\/bin\/node --version\)" = "v[^"]+"/, `test "$(/opt/node/bin/node --version)" = "v${manifest.nodeRuntime.version}"`, "Codex runner Node version");
codexRunnerDockerfile = replaceRequired(codexRunnerDockerfile, /org\.osmantic\.pixel\.node-runtime="[^"]+"/, `org.osmantic.pixel.node-runtime="${manifest.nodeRuntime.version}"`, "Codex runner Node label");
codexRunnerDockerfile = replaceRequired(codexRunnerDockerfile, /org\.opencontainers\.image\.version="[^"]+"/, `org.opencontainers.image.version="${manifest.pixel}"`, "Codex runner Pixel version");
outputs.set("deploy/work-codex-provider/Dockerfile", codexRunnerDockerfile);

let codexProxyDockerfile = await read("deploy/work-codex-provider/Dockerfile.egress-proxy");
codexProxyDockerfile = replaceRequired(codexProxyDockerfile, /^FROM .*$/m, `FROM ${manifest.baseImage}`, "Codex proxy base image");
codexProxyDockerfile = replaceRequired(
  codexProxyDockerfile,
  /^ADD --checksum=sha256:[a-f0-9]{64} https:\/\/nodejs\.org\/dist\/v[^ ]+\/node-v[^ ]+-linux-x64\.tar\.xz \/tmp\/node-runtime\.tar\.xz$/m,
  `ADD --checksum=sha256:${manifest.nodeRuntime.sha256} ${manifest.nodeRuntime.url} /tmp/node-runtime.tar.xz`,
  "Codex proxy Node runtime",
);
codexProxyDockerfile = replaceRequired(codexProxyDockerfile, /test "\$\(\/opt\/node\/bin\/node --version\)" = "v[^"]+"/, `test "$(/opt/node/bin/node --version)" = "v${manifest.nodeRuntime.version}"`, "Codex proxy Node version");
codexProxyDockerfile = replaceRequired(codexProxyDockerfile, /org\.osmantic\.pixel\.node-runtime="[^"]+"/, `org.osmantic.pixel.node-runtime="${manifest.nodeRuntime.version}"`, "Codex proxy Node label");
codexProxyDockerfile = replaceRequired(codexProxyDockerfile, /org\.opencontainers\.image\.version="[^"]+"/, `org.opencontainers.image.version="${manifest.pixel}"`, "Codex proxy Pixel version");
outputs.set("deploy/work-codex-provider/Dockerfile.egress-proxy", codexProxyDockerfile);

let clientOverlaySchema = await read("schemas/client-overlay-v1.schema.json");
clientOverlaySchema = replaceRequired(clientOverlaySchema, /("pixel": \{"const": ")[^"]+("\})/, `$1${manifest.pixel}$2`, "client overlay schema Pixel version");
outputs.set("schemas/client-overlay-v1.schema.json", clientOverlaySchema);

const clientOverlay = JSON.parse(await read("client-overlay.example.json"));
clientOverlay.core.pixel = manifest.pixel;
outputs.set("client-overlay.example.json", json(clientOverlay));

const assistantTemplate = JSON.parse(await read("deploy/agent-comparison/assistant-template.example.json"));
assistantTemplate.sandboxImage = manifest.sandboxImage;
outputs.set("deploy/agent-comparison/assistant-template.example.json", json(assistantTemplate));

let clientKit = await read("scripts/client-kit.mjs");
clientKit = replaceRequired(clientKit, /(core: \{ pixel: ")[^"]+(", modificationAllowed: false)/, `$1${manifest.pixel}$2`, "client-kit Pixel version");
outputs.set("scripts/client-kit.mjs", clientKit);

let mcpClient = await read("deploy/work-controller/mcp-stdio-client.mjs");
mcpClient = replaceRequired(mcpClient, /(clientInfo": \{ name: "pixel-deep-work", version: ")[^"]+(")/, `$1${manifest.pixel}$2`, "MCP client Pixel version");
outputs.set("deploy/work-controller/mcp-stdio-client.mjs", mcpClient);

let workPolicy = await read("deploy/work-broker/policy.example.json");
workPolicy = replaceRequired(workPolicy, /("localModel": \{[\s\S]*?"imageRef": ")[^"]+("\,)/, `$1${manifest.referenceImages.llamaCpp}$2`, "Deep Work local-model image reference");
workPolicy = replaceRequired(workPolicy, /("localModel": \{[\s\S]*?"imageDigest": ")sha256:[a-f0-9]{64}("\,)/, `$1${manifest.referenceImages.llamaCpp.slice(manifest.referenceImages.llamaCpp.indexOf("@") + 1)}$2`, "Deep Work local-model image digest");
outputs.set("deploy/work-broker/policy.example.json", workPolicy);

let compose = await read("deploy/compose.yaml");
compose = replaceRequired(compose, /^    image: \$\{PIXEL_SEARXNG_IMAGE:-.*\}$/m, `    image: \${PIXEL_SEARXNG_IMAGE:-${manifest.referenceImages.searxng}}`, "SearXNG image");
compose = replaceRequired(compose, /^    image: \$\{PIXEL_LLAMA_IMAGE:-.*\}$/m, `    image: \${PIXEL_LLAMA_IMAGE:-${manifest.referenceImages.llamaCpp}}`, "llama.cpp image");
outputs.set("deploy/compose.yaml", compose);

let configExample = await read("configs/openclaw.patch.example.json5");
configExample = replaceRequired(
  configExample,
  /docker: \{ image: "[^"]+", network: "none", readOnlyRoot: true \}/,
  `docker: { image: "${manifest.sandboxImage}", network: "none", readOnlyRoot: true }`,
  "OpenClaw patch example sandbox image",
);
outputs.set("configs/openclaw.patch.example.json5", configExample);

const drift = [];
for (const [path, expected] of outputs) {
  let current = null;
  try { current = await read(path); } catch (error) { if (error?.code !== "ENOENT") throw error; }
  if (current === expected) continue;
  if (mode === "--check") drift.push(path);
  else {
    await mkdir(dirname(join(root, path)), { recursive: true });
    await writeFile(join(root, path), expected, "utf8");
  }
}
if (drift.length) throw new Error(`generated release files are stale: ${drift.join(", ")}`);
console.log(mode === "--write" ? "Generated release files updated." : "Generated release files match the release manifest.");
