#!/usr/bin/env node
import { readFile, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertJsonSchema } from "./lib/json-schema.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const [inputValue, outputValue, ...options] = process.argv.slice(2);
let confirmed = false;
let suppliedIntegrity;
while (options.length > 0) {
  const option = options.shift();
  if (option === "--confirm") { confirmed = true; continue; }
  if (option === "--openclaw-integrity") {
    suppliedIntegrity = options.shift();
    if (!suppliedIntegrity) throw new Error("--openclaw-integrity requires an npm sha512 value");
    continue;
  }
  throw new Error(`Unknown release manifest migration option: ${option}`);
}
if (!inputValue || !outputValue || !confirmed) {
  throw new Error("Usage: ./pixel release-manifest-migrate INPUT.json OUTPUT.json [--openclaw-integrity sha512-...] --confirm");
}
if (!isAbsolute(inputValue) || !isAbsolute(outputValue)) {
  throw new Error("Release manifest migration paths must be absolute");
}
const input = resolve(inputValue);
const output = resolve(outputValue);
if (input === output) throw new Error("Release manifest migration paths must be distinct");

const legacy = JSON.parse(await readFile(input, "utf8"));
if (legacy.$schema !== undefined || legacy.schemaVersion !== undefined) {
  throw new Error("Input must be an unversioned legacy release manifest");
}
for (const field of ["pixel", "node", "openclaw", "openclawInstaller", "openclawPackage", "openclawPlugins", "openclawPluginPackages", "baseImage", "referenceImages"]) {
  if (legacy[field] === undefined) throw new Error(`Legacy release manifest is missing ${field}`);
}
if (legacy.openclawPackage.integrity !== undefined && suppliedIntegrity !== undefined && legacy.openclawPackage.integrity !== suppliedIntegrity) {
  throw new Error("Supplied OpenClaw npm integrity differs from the legacy manifest");
}
if (legacy.openclawPackage.integrity === undefined) legacy.openclawPackage.integrity = suppliedIntegrity;
if (!/^sha512-[A-Za-z0-9+/]+=*$/.test(legacy.openclawPackage.integrity ?? "")) {
  throw new Error("Migration requires the authoritative OpenClaw npm integrity");
}

const migrated = {
  $schema: "./schemas/release-manifest-v1.schema.json",
  schemaVersion: 1,
  ...legacy,
};
const schema = JSON.parse(await readFile(join(root, "schemas/release-manifest-v1.schema.json"), "utf8"));
assertJsonSchema(migrated, schema, "migrated release manifest");
await writeFile(output, `${JSON.stringify(migrated, null, 2)}\n`, { flag: "wx", mode: 0o600 });
console.log(JSON.stringify({ migrated: true, input, output, schemaVersion: 1 }));
