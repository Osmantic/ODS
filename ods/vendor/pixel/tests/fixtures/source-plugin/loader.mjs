// Resolve the two modules the OpenClaw gateway provides, so plugin/index.js loads in a test.
const STUBS = {
  typebox: new URL("./typebox.mjs", import.meta.url).href,
  "openclaw/plugin-sdk/plugin-entry": new URL("./plugin-entry.mjs", import.meta.url).href,
};

export async function resolve(specifier, context, nextResolve) {
  if (Object.hasOwn(STUBS, specifier)) return { url: STUBS[specifier], shortCircuit: true };
  return nextResolve(specifier, context);
}
