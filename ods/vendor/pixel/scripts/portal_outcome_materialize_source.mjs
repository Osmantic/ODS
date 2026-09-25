#!/usr/bin/env node
/** Materialize an exact untrusted outcome source through Pixel's production safe-tar projection. */

import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { materializeUstar } from "../deploy/work-runner/safe-tar.mjs";

function fail(message) { throw new Error(message); }
function integer(value, minimum, maximum, label) {
  if (!/^(?:0|[1-9][0-9]*)$/u.test(value ?? "")) fail(`${label} is invalid`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) fail(`${label} is invalid`);
  return parsed;
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 7) fail("usage");
  const [archiveValue, destinationValue, expectedSha256, archiveBytesValue, extractedBytesValue, entriesValue, fileBytesValue] = argv;
  const archivePath = resolve(archiveValue);
  const destination = resolve(destinationValue);
  if (archivePath !== archiveValue || destination !== destinationValue || !/^[a-f0-9]{64}$/u.test(expectedSha256)) fail("path or digest is invalid");
  const result = await materializeUstar(archivePath, destination, {
    expectedSha256,
    maxArchiveBytes: integer(archiveBytesValue, 1536, 1073741824, "archive bytes"),
    maxExtractedBytes: integer(extractedBytesValue, 1, 4294967296, "extracted bytes"),
    maxEntries: integer(entriesValue, 1, 100000, "entries"),
    maxFileBytes: integer(fileBytesValue, 1, 4294967296, "file bytes"),
  });
  process.stdout.write(`${JSON.stringify({
    schemaVersion: 1, operation: "pixel-portal-outcome-source-materialization",
    archiveSha256: result.archiveSha256, archiveBytes: result.archiveBytes,
    treeSha256: result.treeSha256, extractedBytes: result.extractedBytes,
    entries: result.entries.length, inertEntries: result.entries.filter((item) => item.inert).length,
  })}\n`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((error) => {
    process.stderr.write(`portal-outcome-source-materialization: ${error instanceof Error ? error.message : "failed"}\n`);
    process.exitCode = 1;
  });
}
