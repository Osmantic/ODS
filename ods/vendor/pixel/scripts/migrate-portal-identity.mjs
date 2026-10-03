#!/usr/bin/env node
// Replace only the byte-exact former default profile. Owner edits are never overwritten.
import { createHash, randomUUID } from "node:crypto";
import { lstat, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

const [workspaceArg, generatedArg] = process.argv.slice(2);
if (!workspaceArg || !generatedArg || process.argv.length !== 4) {
  throw new Error("Usage: migrate-portal-identity.mjs WORKSPACE GENERATED_WORKSPACE");
}
const workspace = resolve(workspaceArg);
const generated = resolve(generatedArg);

async function regular(path) {
  const entry = await lstat(path);
  if (!entry.isFile() || entry.isSymbolicLink() || entry.nlink !== 1) {
    throw new Error("Assistant profile is not a single regular file");
  }
}

async function directory(path) {
  const entry = await lstat(path);
  if (!entry.isDirectory() || entry.isSymbolicLink()) {
    throw new Error("Assistant profile directory is unsafe");
  }
}

async function migrate(name, isFormerDefault) {
  const target = join(workspace, name);
  const source = join(generated, name);
  await regular(target);
  await regular(source);
  const current = await readFile(target);
  const replacement = await readFile(source);
  if (current.equals(replacement)) return "current";
  if (!isFormerDefault(current, replacement)) return "owner-customized";
  const temporary = join(workspace, `.${name}.${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, replacement, { flag: "wx", mode: 0o600 });
    await rename(temporary, target);
  } finally {
    await unlink(temporary).catch(error => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
  return "migrated";
}

await directory(workspace);
await directory(generated);
const soul = await migrate("SOUL.md", current =>
  createHash("sha256").update(current).digest("hex") ===
  "57a4fc37dfc88f86da499099fe8a9211dcc86313783e00cc30b812415f3e4bc6");
const identity = await migrate("IDENTITY.md", (current, replacement) => {
  const rendered = replacement.toString("utf8");
  if (!rendered.includes("- Name: Portal\n")) throw new Error("Generated public identity is invalid");
  return current.equals(Buffer.from(rendered.replace("- Name: Portal\n", "- Name: Pixel\n")));
});
console.log(`Portal profile: SOUL.md ${soul}; IDENTITY.md ${identity}`);
