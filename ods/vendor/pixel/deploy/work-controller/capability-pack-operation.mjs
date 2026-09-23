import { randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, realpath, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { assertJsonSchema } from "../../scripts/lib/json-schema.mjs";

const ID_RE = /^[a-z][a-z0-9-]{1,62}$/u;
const VERSION_RE = /^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u;
const kinds = new Set(["image-admission-inspection", "health-probe", "tool-execution", "image-admission-revocation", "pack-removal"]);
const authority = Object.freeze({ grantsExecution: false, grantsImagePull: false, grantsToolUse: false, grantsDataAccess: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Private single-writer custody for one exact installed capability-pack operation. It serializes image inspection, explicit health-probe custody, separately granted tool execution, admission revocation, and pack removal but grants no independent execution, image pull, tool use, data access, network, external effect, or completion authority.";
const operationSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-pack-operation-v1.schema.json", import.meta.url), "utf8"));

export class CapabilityPackOperationError extends Error {}
function fail(message) { throw new CapabilityPackOperationError(message); }

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) === dirname(resolve(path))) fail(`${label} must be an absolute non-root directory`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path) !== resolve(path)) fail(`${label} must be a real unlinked directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
  return resolve(path);
}

async function privateRegular(path, maximumBytes, label) {
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch { fail(`${label} must be a descriptor-bound regular file`); }
  try {
    const info = await handle.stat(), current = await lstat(path);
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > maximumBytes || current.isSymbolicLink() || current.dev !== info.dev || current.ino !== info.ino) fail(`${label} must be a bounded regular single-link file`);
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
    return await handle.readFile();
  } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function checkedPack(value) {
  const keys = ["id", "packSha256", "signerIdentity", "treeSha256", "version"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || !ID_RE.test(value.id ?? "") || !VERSION_RE.test(value.version ?? "") || !SHA_RE.test(value.packSha256 ?? "") || !SHA_RE.test(value.treeSha256 ?? "") || !IDENTITY_RE.test(value.signerIdentity ?? "")) fail("capability-pack operation binding is invalid");
  return value;
}

function checkedOperation(value) {
  const keys = ["$schema", "authority", "bindingSha256", "boundary", "kind", "operation", "pack", "schemaVersion", "token"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || value.$schema !== "https://osmantic.com/pixel/schemas/work-capability-pack-operation-v1.schema.json" || value.schemaVersion !== 1 || value.operation !== "pixel-work-capability-pack-operation" || !kinds.has(value.kind) || !SHA_RE.test(value.bindingSha256 ?? "") || !SHA_RE.test(value.token ?? "") || canonical(value.authority) !== canonical(authority) || value.boundary !== boundary) fail("capability-pack operation record is invalid");
  checkedPack(value.pack);
  try { assertJsonSchema(value, operationSchema, "capability-pack operation"); } catch { fail("capability-pack operation record is invalid"); }
  return value;
}

async function location(stateRoot, id, version, packSha256, create) {
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "") || !SHA_RE.test(packSha256 ?? "")) fail("capability-pack operation identity is invalid");
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const operations = await privateDirectory(join(root, "capability-pack-operations"), "capability-pack operation root", create);
  const identity = await privateDirectory(join(operations, id), "capability-pack operation identity root", create);
  return { root, operations, identity, path: join(identity, `${version}-${packSha256}.json`) };
}

export async function loadCapabilityPackOperation({ stateRoot, id, version, packSha256, optional = false }) {
  let value;
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "") || !SHA_RE.test(packSha256 ?? "")) fail("capability-pack operation identity is invalid");
  const checkedRoot = await privateDirectory(resolve(stateRoot), "capability state root");
  const expectedPath = join(checkedRoot, "capability-pack-operations", id, `${version}-${packSha256}.json`);
  if (optional && !(await lstat(expectedPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error)))) return null;
  const place = await location(stateRoot, id, version, packSha256, false);
  try { value = checkedOperation(JSON.parse((await privateRegular(place.path, 64 * 1024, "capability-pack operation record")).toString("utf8"))); }
  catch (error) { if (error instanceof CapabilityPackOperationError) throw error; fail("capability-pack operation record is invalid"); }
  if (value.pack.id !== id || value.pack.version !== version || value.pack.packSha256 !== packSha256) fail("capability-pack operation differs from its filename");
  return Object.freeze({ ...value, pack: Object.freeze({ ...value.pack }), authority: Object.freeze({ ...value.authority }) });
}

export async function acquireCapabilityPackOperation({ stateRoot, pack, kind, bindingSha256 }) {
  checkedPack(pack);
  if (!kinds.has(kind) || !SHA_RE.test(bindingSha256 ?? "")) fail("capability-pack operation request is invalid");
  const place = await location(stateRoot, pack.id, pack.version, pack.packSha256, true), token = randomBytes(32).toString("hex");
  const record = { $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-operation-v1.schema.json", schemaVersion: 1, operation: "pixel-work-capability-pack-operation", kind, pack: { ...pack }, bindingSha256, token, authority: { ...authority }, boundary };
  let handle;
  try { handle = await open(place.path, "wx", 0o600); }
  catch (error) { if (error?.code === "EEXIST") fail("capability pack already has an in-progress mutation; recover it before continuing"); throw error; }
  try { await handle.writeFile(`${JSON.stringify(record, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); }
  await syncDirectory(place.identity);
  return Object.freeze({ ...record, pack: Object.freeze({ ...record.pack }), authority: Object.freeze({ ...record.authority }) });
}

export async function releaseCapabilityPackOperation({ stateRoot, operation }) {
  const expected = checkedOperation(operation), current = await loadCapabilityPackOperation({ stateRoot, id: expected.pack.id, version: expected.pack.version, packSha256: expected.pack.packSha256 });
  if (canonical(current) !== canonical(expected)) fail("capability-pack operation release differs from current custody");
  const place = await location(stateRoot, expected.pack.id, expected.pack.version, expected.pack.packSha256, false);
  await unlink(place.path); await syncDirectory(place.identity);
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-pack-operation-release", status: "released", kind: expected.kind, pack: { ...expected.pack }, token: expected.token, authority: { ...authority }, boundary });
}

export async function auditCapabilityPackOperationRoot(stateRoot) {
  const root = await privateDirectory(resolve(stateRoot), "capability state root"), operationsPath = join(root, "capability-pack-operations");
  const info = await lstat(operationsPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!info) return Object.freeze({ active: 0 });
  const operations = await privateDirectory(operationsPath, "capability-pack operation root");
  let active = 0;
  for (const id of (await readdir(operations)).sort()) {
    if (!ID_RE.test(id)) fail("capability-pack operation root contains an unsafe identity");
    const identity = await privateDirectory(join(operations, id), "capability-pack operation identity root");
    for (const name of (await readdir(identity)).sort()) {
      if (!name.endsWith(".json") || !VERSION_RE.test(name.slice(0, name.length - 70)) || name.at(-70) !== "-" || !SHA_RE.test(name.slice(-69, -5))) fail("capability-pack operation identity root contains an unsafe entry");
      await loadCapabilityPackOperation({ stateRoot: root, id, version: name.slice(0, name.length - 70), packSha256: name.slice(-69, -5) }); active += 1;
    }
  }
  return Object.freeze({ active });
}

export const capabilityPackOperationBoundary = boundary;
