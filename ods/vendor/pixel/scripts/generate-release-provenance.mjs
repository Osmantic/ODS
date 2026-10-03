import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { readBoundedRegularFile } from "./lib/secure-files.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function fail(message) {
  throw new Error(message);
}

function argumentsByName() {
  const values = {};
  for (let index = 2; index < process.argv.length; index += 2) {
    const key = process.argv[index];
    const value = process.argv[index + 1];
    if (!["--artifact", "--sbom", "--output"].includes(key) || !value) fail("Invalid provenance arguments");
    values[key.slice(2)] = resolve(value);
  }
  if (Object.keys(values).length !== 3) {
    fail("Usage: node scripts/generate-release-provenance.mjs --artifact PATH --sbom PATH --output PATH");
  }
  return values;
}

async function regularDigest(path) {
  try {
    const { bytes } = await readBoundedRegularFile(path, 512 * 1024 * 1024, "release input");
    return createHash("sha256").update(bytes).digest("hex");
  } catch {
    fail(`${path} is not a regular release input`);
  }
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

const paths = argumentsByName();
const version = (await readFile(join(root, "VERSION"), "utf8")).trim();
const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const sourceTree = execFileSync("git", ["rev-parse", "HEAD^{tree}"], { cwd: root, encoding: "utf8" }).trim();
const manifestSha256 = await regularDigest(join(root, "RELEASE-MANIFEST.json"));
const compatibilitySha256 = await regularDigest(join(root, "OPENCLAW-COMPATIBILITY.json"));
const builder = process.env.GITHUB_ACTIONS === "true"
  ? `https://github.com/Osmantic/Pixel/actions/runs/${process.env.GITHUB_RUN_ID ?? "unknown"}`
  : "https://github.com/Osmantic/Pixel/blob/main/scripts/package-release.sh";

const statement = {
  _type: "https://in-toto.io/Statement/v1",
  subject: [
    { name: basename(paths.artifact), digest: { sha256: await regularDigest(paths.artifact) } },
    { name: basename(paths.sbom), digest: { sha256: await regularDigest(paths.sbom) } },
  ].sort((left, right) => left.name.localeCompare(right.name)),
  predicateType: "https://slsa.dev/provenance/v1",
  predicate: {
    buildDefinition: {
      buildType: "https://github.com/Osmantic/Pixel/blob/main/scripts/package-release.sh",
      externalParameters: { version },
      internalParameters: {},
      resolvedDependencies: [
        { uri: `git+https://github.com/Osmantic/Pixel@${sourceCommit}`, digest: { gitCommit: sourceCommit, gitTree: sourceTree } },
        { uri: "RELEASE-MANIFEST.json", digest: { sha256: manifestSha256 } },
        { uri: "OPENCLAW-COMPATIBILITY.json", digest: { sha256: compatibilitySha256 } },
      ],
    },
    runDetails: {
      builder: { id: builder },
      metadata: {
        invocationId: process.env.GITHUB_RUN_ID
          ? `${process.env.GITHUB_RUN_ID}.${process.env.GITHUB_RUN_ATTEMPT ?? "1"}`
          : `local:${sourceCommit}`,
      },
      byproducts: [],
    },
  },
};

await atomicWrite(paths.output, `${JSON.stringify(statement)}\n`);
console.log(`Release provenance: ${paths.output}`);
