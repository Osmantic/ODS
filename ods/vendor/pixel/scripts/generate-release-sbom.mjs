import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function fail(message) {
  throw new Error(message);
}

function argument(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) fail(`Missing ${name}`);
  return resolve(process.argv[index + 1]);
}

async function atomicWrite(path, contents) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = join(dirname(path), `.${basename(path)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
    await handle.chmod(0o644);
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

function sha512FromIntegrity(integrity) {
  const match = /^sha512-([A-Za-z0-9+/]+=*)$/.exec(integrity ?? "");
  return match ? Buffer.from(match[1], "base64").toString("hex") : null;
}

function npmPurl(name, version) {
  const encoded = name.startsWith("@")
    ? `%40${name.slice(1).split("/").map(encodeURIComponent).join("/")}`
    : encodeURIComponent(name);
  return `pkg:npm/${encoded}@${encodeURIComponent(version)}`;
}

function componentRef(component) {
  return component.purl ?? `pixel:${component.type}:${component.name}@${component.version ?? "unknown"}`;
}

function addComponent(components, component) {
  const ref = componentRef(component);
  const value = { ...component, "bom-ref": ref };
  if (!components.has(ref)) components.set(ref, value);
  return ref;
}

function artifactComponent(name, version, artifact, purl) {
  return {
    type: "library",
    name,
    version,
    purl,
    hashes: [{ alg: "SHA-256", content: artifact.sha256 }],
    externalReferences: [{ type: "distribution", url: artifact.url }],
    properties: artifact.integrity ? [{ name: "pixel:npm-integrity", value: artifact.integrity }] : [],
  };
}

function containerComponent(name, value) {
  const digest = /@sha256:([0-9a-f]{64})$/.exec(value);
  if (!digest) fail(`${name} container image is not digest pinned`);
  return {
    type: "container",
    name,
    version: value,
    hashes: [{ alg: "SHA-256", content: digest[1] }],
    properties: [{ name: "pixel:image-reference", value }],
  };
}

function parsePythonRequirements(contents) {
  const values = [];
  for (const line of contents.split(/\r?\n/)) {
    const match = /^([A-Za-z0-9_.-]+)==([^\s\\]+)(?:\s+\\)?$/.exec(line.trim());
    if (!match) continue;
    const normalized = match[1].toLowerCase().replaceAll("_", "-");
    values.push({
      type: "library",
      name: normalized,
      version: match[2],
      purl: `pkg:pypi/${encodeURIComponent(normalized)}@${encodeURIComponent(match[2])}`,
    });
  }
  return values;
}

if (process.argv.length !== 4 || process.argv[2] !== "--output") {
  fail("Usage: node scripts/generate-release-sbom.mjs --output ABSOLUTE_OR_RELATIVE_PATH");
}

const output = argument("--output");
const readJson = async (path) => JSON.parse(await readFile(join(root, path), "utf8"));
const manifest = await readJson("RELEASE-MANIFEST.json");
const dataRuntime = await readJson("deploy/work-runner/data-runtime.json");
const builderRuntime = await readJson("deploy/work-runner/builder-runtime.json");
const components = new Map();
const dependencies = new Map();
const rootRef = `pkg:generic/pixel@${encodeURIComponent(manifest.pixel)}`;
const rootDependencies = new Set();

for (const directory of ["plugin", "plugin-ops", "plugin-frontier"]) {
  const lock = await readJson(`${directory}/package-lock.json`);
  if (lock.lockfileVersion !== 3 || lock.version !== manifest.pixel) fail(`${directory} lock does not match the release`);
  const packageRef = addComponent(components, {
    type: "library",
    name: lock.name,
    version: lock.version,
    purl: npmPurl(lock.name, lock.version),
    properties: [{ name: "pixel:source-path", value: directory }],
  });
  rootDependencies.add(packageRef);
  const packageDependencies = new Set();
  for (const [path, value] of Object.entries(lock.packages ?? {})) {
    if (!path.startsWith("node_modules/") || !value.version) continue;
    const name = path.slice("node_modules/".length);
    const integrity = sha512FromIntegrity(value.integrity);
    const dependencyRef = addComponent(components, {
      type: "library",
      name,
      version: value.version,
      purl: npmPurl(name, value.version),
      ...(integrity ? { hashes: [{ alg: "SHA-512", content: integrity }] } : {}),
      ...(value.license ? { licenses: [{ license: { id: value.license } }] } : {}),
      ...(value.resolved ? { externalReferences: [{ type: "distribution", url: value.resolved }] } : {}),
    });
    packageDependencies.add(dependencyRef);
  }
  dependencies.set(packageRef, packageDependencies);
}

for (const component of parsePythonRequirements(await readFile(join(root, "deploy/web-courier/requirements.lock"), "utf8"))) {
  rootDependencies.add(addComponent(components, component));
}
for (const artifact of dataRuntime.artifacts) {
  rootDependencies.add(addComponent(components, artifactComponent(
    artifact.name,
    artifact.version,
    artifact,
    `pkg:pypi/${encodeURIComponent(artifact.name)}@${encodeURIComponent(artifact.version)}`,
  )));
}
for (const entry of dataRuntime.systemPackages) {
  rootDependencies.add(addComponent(components, {
    type: "library",
    name: entry.name,
    version: entry.version,
    purl: `pkg:deb/debian/${encodeURIComponent(entry.name)}@${encodeURIComponent(entry.version)}`,
    properties: [{ name: "pixel:source-path", value: "deploy/work-runner/data-runtime.json" }],
  }));
}
for (const entry of builderRuntime.systemPackages) {
  rootDependencies.add(addComponent(components, {
    type: "library",
    name: entry.name,
    version: entry.version,
    purl: `pkg:deb/debian/${encodeURIComponent(entry.name)}@${encodeURIComponent(entry.version)}`,
    properties: [{ name: "pixel:source-path", value: "deploy/work-runner/builder-runtime.json" }],
  }));
}
for (const artifact of builderRuntime.artifacts) {
  rootDependencies.add(addComponent(components, artifactComponent(
    artifact.name,
    artifact.version,
    artifact,
    npmPurl(artifact.name, artifact.version),
  )));
}

rootDependencies.add(addComponent(components, artifactComponent(
  "node",
  manifest.nodeRuntime.version,
  manifest.nodeRuntime,
  `pkg:generic/node@${encodeURIComponent(manifest.nodeRuntime.version)}`,
)));
rootDependencies.add(addComponent(components, artifactComponent(
  "openclaw",
  manifest.openclaw,
  manifest.openclawPackage,
  npmPurl("openclaw", manifest.openclaw),
)));
for (const [name, key] of Object.entries({
  "@openclaw/discord": "discord",
  "@openclaw/searxng-plugin": "searxng",
  "@openclaw/llama-cpp-provider": "llamaCpp",
})) {
  rootDependencies.add(addComponent(components, artifactComponent(
    name,
    manifest.openclawPlugins[name],
    manifest.openclawPluginPackages[key],
    npmPurl(name, manifest.openclawPlugins[name]),
  )));
}
rootDependencies.add(addComponent(components, {
  type: "file",
  name: "openclaw-installer",
  version: manifest.openclaw,
  hashes: [{ alg: "SHA-256", content: manifest.openclawInstaller.sha256 }],
  externalReferences: [
    { type: "distribution", url: manifest.openclawInstaller.url },
    ...(manifest.openclawInstaller.mirrors ?? []).map((url) => ({ type: "distribution", url })),
  ],
}));
rootDependencies.add(addComponent(components, containerComponent("pixel-sandbox-base", manifest.baseImage)));
for (const [name, value] of Object.entries(manifest.referenceImages)) {
  rootDependencies.add(addComponent(components, containerComponent(name, value)));
}

const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const sourceTree = execFileSync("git", ["rev-parse", "HEAD^{tree}"], { cwd: root, encoding: "utf8" }).trim();
const manifestDigest = createHash("sha256").update(await readFile(join(root, "RELEASE-MANIFEST.json"))).digest("hex");
dependencies.set(rootRef, rootDependencies);

const sbom = {
  bomFormat: "CycloneDX",
  specVersion: "1.6",
  version: 1,
  metadata: {
    component: {
      type: "application",
      name: "Pixel",
      version: manifest.pixel,
      "bom-ref": rootRef,
      purl: rootRef,
      supplier: { name: "Osmantic" },
      externalReferences: [{ type: "vcs", url: `https://github.com/Osmantic/Pixel/tree/${sourceCommit}` }],
      properties: [
        { name: "pixel:source-commit", value: sourceCommit },
        { name: "pixel:source-tree", value: sourceTree },
        { name: "pixel:release-manifest-sha256", value: manifestDigest },
      ],
    },
  },
  components: [...components.values()].sort((left, right) => left["bom-ref"].localeCompare(right["bom-ref"])),
  dependencies: [...dependencies.entries()]
    .map(([ref, values]) => ({ ref, dependsOn: [...values].sort() }))
    .sort((left, right) => left.ref.localeCompare(right.ref)),
};

await atomicWrite(output, `${JSON.stringify(sbom, null, 2)}\n`);
console.log(`Release SBOM: ${output}`);
