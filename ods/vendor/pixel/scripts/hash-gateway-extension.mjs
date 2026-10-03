#!/usr/bin/env node
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";

const root = process.argv[2];
if (!root || !isAbsolute(root) || resolve(root) === "/") {
  console.error("Usage: ./pixel extension-hash /absolute/reviewed/plugin-directory");
  process.exit(2);
}

const files = [];
async function walk(directory, relative = "") {
  for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
    const child = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Extension contains a symbolic link: ${childRelative}`);
    if (entry.isDirectory()) await walk(child, childRelative);
    else if (entry.isFile()) files.push([childRelative, child]);
    else throw new Error(`Extension contains a non-regular file: ${childRelative}`);
  }
}

await walk(root);
const hash = createHash("sha256");
for (const [relative, path] of files) {
  const content = await readFile(path);
  hash.update(relative); hash.update("\0"); hash.update(String(content.length)); hash.update("\0"); hash.update(content); hash.update("\0");
}
console.log(hash.digest("hex"));
