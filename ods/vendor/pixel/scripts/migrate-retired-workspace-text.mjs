#!/usr/bin/env node
// Remove retired shipped template text from an existing Pixel workspace.
// Only byte-exact shipped copies are removed; owner edits are never changed.
// Each changed file keeps a backup of its original bytes next to it.
import { isAbsolute, resolve } from "node:path";
import { removeRetiredWorkspaceFiles } from "./lib/retired-workspace-text.mjs";

const [workspaceArgument] = process.argv.slice(2);
if (process.argv.length !== 3 || !workspaceArgument || !isAbsolute(workspaceArgument)) {
  throw new Error("Usage: migrate-retired-workspace-text.mjs ABSOLUTE_WORKSPACE");
}
const results = await removeRetiredWorkspaceFiles(resolve(workspaceArgument));
const describe = result => {
  if (result.status === "removed") return `${result.name} removed (backup ${result.backup})`;
  if (result.status === "failed") return `${result.name} failed (${result.code})`;
  return `${result.name} ${result.status}`;
};
console.log(`Retired workspace text: ${results.map(describe).join("; ")}`);
if (results.some(result => result.status === "failed")) process.exitCode = 1;
