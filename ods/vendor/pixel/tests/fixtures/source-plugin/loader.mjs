import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// Source handlers validate arguments, so this lane needs the real locked
// validator, not a builder stub that could falsely accept malformed calls.
const STUBS = {
  "openclaw/plugin-sdk/plugin-entry": new URL("./plugin-entry.mjs", import.meta.url).href,
};

const directory = process.env.PIXEL_TEST_TYPEBOX_DIR
  ?? fileURLToPath(new URL("../../../plugin/node_modules/typebox", import.meta.url));
const actual = JSON.parse(readFileSync(join(directory, "package.json"), "utf8"));
const expected = JSON.parse(readFileSync(new URL("../../../plugin/package.json", import.meta.url), "utf8"));
if (actual.version !== expected.dependencies.typebox) throw new Error("Stage the exact locked TypeBox dependency");
STUBS.typebox = pathToFileURL(join(directory, "build/index.mjs")).href;
STUBS["typebox/value"] = pathToFileURL(join(directory, "build/value/index.mjs")).href;

export async function resolve(specifier, context, nextResolve) {
  if (Object.hasOwn(STUBS, specifier)) return { url: STUBS[specifier], shortCircuit: true };
  return nextResolve(specifier, context);
}
