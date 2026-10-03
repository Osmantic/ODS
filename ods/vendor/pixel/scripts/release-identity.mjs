import { execFileSync, spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const boundary = "Content-free source and baseline-qualification identity only. This record does not prove installed-byte integrity, runtime health, provider capability, release promotion, publication, deployment approval, or client acceptance.";
const sha256Pattern = /^[a-f0-9]{64}$/u;
const gitPattern = /^[a-f0-9]{40}$/u;
const versionPattern = /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/u;

function fail(message) {
  throw new Error(message);
}

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

async function digest(path) {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size > 4 * 1024 * 1024) fail(`${basename(path)} is not a bounded regular file`);
    const payload = await handle.readFile();
    const after = await handle.stat();
    if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || payload.length !== after.size) {
      fail(`${basename(path)} changed while its identity was computed`);
    }
    return createHash("sha256").update(payload).digest("hex");
  } finally {
    await handle.close();
  }
}

function sameStringMap(left, right) {
  if (!left || typeof left !== "object" || Array.isArray(left) || !right || typeof right !== "object" || Array.isArray(right)) return false;
  const names = Object.keys(left).sort();
  return names.join("\0") === Object.keys(right).sort().join("\0")
    && names.every((name) => typeof left[name] === "string" && left[name] === right[name]);
}

function validate(value) {
  if (!exactKeys(value, ["schemaVersion", "kind", "pixel", "source", "manifests", "qualification", "boundary"])) fail("release identity has missing or unknown fields");
  if (value.schemaVersion !== 1 || value.kind !== "pixel-release-source-identity" || !versionPattern.test(value.pixel) || value.boundary !== boundary) fail("release identity header is invalid");
  if (!exactKeys(value.source, ["state", "commit", "tree"]) || !["git-clean", "unavailable"].includes(value.source.state)) fail("release source identity is invalid");
  if (value.source.state === "git-clean") {
    if (!gitPattern.test(value.source.commit) || !gitPattern.test(value.source.tree)) fail("release Git identity is invalid");
  } else if (value.source.commit !== null || value.source.tree !== null) fail("unavailable source identity must not claim Git values");
  if (!exactKeys(value.manifests, ["releaseSha256", "compatibilitySha256", "qualificationMatrixSha256"]) || !Object.values(value.manifests).every((item) => sha256Pattern.test(item))) fail("release manifest identities are invalid");
  if (!exactKeys(value.qualification, ["recordStatus", "sourceCommit", "qualifiedAt", "liveAudit", "relationship"])) fail("qualification identity is invalid");
  if (!["supported", "candidate", "blocked", "retired", null].includes(value.qualification.recordStatus)) fail("qualification status is invalid");
  if (value.qualification.sourceCommit !== null && !gitPattern.test(value.qualification.sourceCommit)) fail("qualification source is invalid");
  if (value.qualification.qualifiedAt !== null && !/^\d{4}-\d{2}-\d{2}$/u.test(value.qualification.qualifiedAt)) fail("qualification date is invalid");
  if (value.qualification.liveAudit !== null && (typeof value.qualification.liveAudit !== "string" || value.qualification.liveAudit.length < 1 || value.qualification.liveAudit.length > 255)) fail("qualification audit is invalid");
  if (!["same-source", "qualified-ancestor", "unverified", "unavailable"].includes(value.qualification.relationship)) fail("qualification relationship is invalid");
  if (value.source.state === "unavailable" && value.qualification.relationship !== "unavailable") fail("unavailable source has an invalid qualification relationship");
  if (value.source.state === "git-clean" && value.qualification.relationship === "unavailable") fail("available source has an invalid qualification relationship");
  if (["same-source", "qualified-ancestor"].includes(value.qualification.relationship) && value.qualification.sourceCommit === null) fail("verified qualification lineage has no source");
  if (value.qualification.relationship === "same-source" && value.qualification.sourceCommit !== value.source.commit) fail("same-source qualification does not match the release source");
  return value;
}

async function atomicJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = join(dirname(path), `.${basename(path)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.chmod(0o644);
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  }
}

if (process.argv.length !== 4 || process.argv[2] !== "--output") fail("Usage: node scripts/release-identity.mjs --output PATH");
const output = resolve(process.argv[3]);
const releasePath = join(root, "RELEASE-MANIFEST.json");
const compatibilityPath = join(root, "OPENCLAW-COMPATIBILITY.json");
const matrixPath = join(root, "QUALIFICATION-MATRIX.json");
const manifest = JSON.parse(await readFile(releasePath, "utf8"));
const compatibility = JSON.parse(await readFile(compatibilityPath, "utf8"));
const manifests = {
  releaseSha256: await digest(releasePath),
  compatibilitySha256: await digest(compatibilityPath),
  qualificationMatrixSha256: await digest(matrixPath),
};

const embedded = join(root, "RELEASE-IDENTITY.json");
let identity;
try {
  identity = validate(JSON.parse(await readFile(embedded, "utf8")));
  if (identity.pixel !== manifest.pixel || JSON.stringify(identity.manifests) !== JSON.stringify(manifests)) fail("embedded release identity does not match its manifests");
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
  const combination = Array.isArray(compatibility.combinations)
    ? compatibility.combinations.find((item) => item?.pixel === manifest.pixel && item?.openclaw === manifest.openclaw
      && sameStringMap(item?.plugins, manifest.openclawPlugins))
    : undefined;
  let source = { state: "unavailable", commit: null, tree: null };
  try {
    const gitOptions = { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] };
    const status = execFileSync("git", ["status", "--porcelain", "--untracked-files=normal"], gitOptions);
    if (status === "") {
      source = {
        state: "git-clean",
        commit: execFileSync("git", ["rev-parse", "HEAD"], gitOptions).trim(),
        tree: execFileSync("git", ["rev-parse", "HEAD^{tree}"], gitOptions).trim(),
      };
    }
  } catch {}
  const qualifiedSource = combination?.evidence?.sourceCommit;
  let relationship = source.state === "unavailable" ? "unavailable" : "unverified";
  if (source.state === "git-clean" && gitPattern.test(qualifiedSource ?? "")) {
    if (qualifiedSource === source.commit) relationship = "same-source";
    else {
      const ancestor = spawnSync("git", ["merge-base", "--is-ancestor", qualifiedSource, source.commit], { cwd: root });
      if (ancestor.status === 0) relationship = "qualified-ancestor";
    }
  }
  identity = validate({
    schemaVersion: 1,
    kind: "pixel-release-source-identity",
    pixel: manifest.pixel,
    source,
    manifests,
    qualification: {
      recordStatus: combination?.status ?? null,
      sourceCommit: gitPattern.test(qualifiedSource ?? "") ? qualifiedSource : null,
      qualifiedAt: typeof combination?.qualifiedAt === "string" ? combination.qualifiedAt : null,
      liveAudit: typeof combination?.evidence?.liveAudit === "string" ? combination.evidence.liveAudit : null,
      relationship,
    },
    boundary,
  });
}

await atomicJson(output, identity);
console.log(JSON.stringify({ output, sourceState: identity.source.state, qualificationRelationship: identity.qualification.relationship }));
