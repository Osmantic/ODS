import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// The offline builder fixture preserves schema shape without claiming full
// validation. Also run with the release's real locked TypeBox when staged.
const STUBS = {
  typebox: new URL("./typebox.mjs", import.meta.url).href,
  "openclaw/plugin-sdk/plugin-entry": new URL("./plugin-entry.mjs", import.meta.url).href,
};

if (process.env.PIXEL_TEST_TYPEBOX_DIR) {
  const directory = process.env.PIXEL_TEST_TYPEBOX_DIR;
  const actual = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
  const expected = JSON.parse(readFileSync(new URL("../../../plugin/package.json", import.meta.url), "utf8"));
  if (actual.version !== expected.dependencies.typebox) throw new Error("Stage the exact locked TypeBox dependency");
  STUBS.typebox = pathToFileURL(join(directory, "build/index.mjs")).href;
}

export async function resolve(specifier, context, nextResolve) {
  if (Object.hasOwn(STUBS, specifier)) return { url: STUBS[specifier], shortCircuit: true };
  return nextResolve(specifier, context);
}
