#!/usr/bin/env node
// Remove retired shipped template text from an existing Pixel workspace.
// Only byte-exact shipped copies are removed; owner edits are never changed.
// Each changed file keeps a private backup of its original bytes in
// BACKUP_DIRECTORY, outside the workspace. Exit status 1: a file needs review.
import { isAbsolute, resolve } from "node:path";
import { removeRetiredWorkspaceFiles } from "./lib/retired-workspace-text.mjs";

const [workspaceArgument, backupArgument] = process.argv.slice(2);
if (process.argv.length !== 4 || !isAbsolute(workspaceArgument) || !isAbsolute(backupArgument)) {
  throw new Error("Usage: migrate-retired-workspace-text.mjs ABSOLUTE_WORKSPACE ABSOLUTE_BACKUP_DIRECTORY");
}
const results = await removeRetiredWorkspaceFiles(resolve(workspaceArgument), resolve(backupArgument));
const REVIEW = "; review";
const describe = ({ name, status, backup, code, modified }) => {
  if (status === "current" || status === "missing") return `${name} ${status}`;
  if (status === "removed") return `${name} removed (backup ${backup})${modified ? `; modified retired section present${REVIEW}` : ""}`;
  if (status === "modified-retired-section") return `${name} modified retired section present${REVIEW}`;
  if (status === "skipped-backup-conflict") return `${name} ${status} (backup ${backup})${REVIEW}`;
  if (status === "failed") return `${name} failed (${code})${REVIEW}`;
  return `${name} ${status}${REVIEW}`;
};
const lines = results.map(describe);
for (const line of lines) console.log(`Retired workspace text: ${line}`);
if (lines.some(line => line.endsWith(REVIEW))) process.exitCode = 1;
