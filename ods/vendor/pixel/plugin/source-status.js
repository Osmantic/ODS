import { readFile } from "node:fs/promises";
import { join } from "node:path";

// The broker's content-free status.json says whether a source is syncing and why not.
const SYNC_KEYS = ["state", "errorClass", "lastSuccessAt", "failingSince"];

export async function readSourceSync(projectionDir, source) {
  let value;
  try {
    value = JSON.parse(await readFile(join(projectionDir, "status.json"), "utf8"));
  } catch (error) {
    // No status yet (first refresh pending) or an unreadable one: the sync state is unknown.
    if (error?.code === "ENOENT" || error instanceof SyntaxError) return null;
    throw error;
  }
  const entry = value?.kind === "pixel-source-broker-status" ? value.sources?.[source] : null;
  if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
  return Object.fromEntries(SYNC_KEYS.map((key) => [key, typeof entry[key] === "string" ? entry[key] : null]));
}
