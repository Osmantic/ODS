import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const [source, output, ...pluginPaths] = process.argv.slice(2);
if (!source || !output || pluginPaths.length === 0) throw new Error("Usage: render-validation-config.mjs SOURCE OUTPUT EXISTING_PLUGIN_PATH...");
const config = JSON.parse(await readFile(resolve(source), "utf8"));
config.plugins ??= {};
config.plugins.load ??= {};
config.plugins.load.paths = pluginPaths.map((path) => resolve(path));
await writeFile(resolve(output), `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
