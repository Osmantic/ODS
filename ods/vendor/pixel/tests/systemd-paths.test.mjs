import assert from "node:assert/strict";
import test from "node:test";
import { protectedHomeRuntimeMountRoot } from "../scripts/lib/systemd-paths.mjs";

test("private runtime beside deployment HOME remains visible through ProtectHome", () => {
  const home = "/home/gabs/.local/share/foss-cmo-host/home";
  assert.equal(
    protectedHomeRuntimeMountRoot(home, "/home/gabs/.local/share/foss-cmo-host/node-private/bin/node"),
    "/home/gabs/.local/share/foss-cmo-host/node-private",
  );
  assert.equal(
    protectedHomeRuntimeMountRoot(home, "/home/gabs/.local/share/foss-cmo-host/node-private/bin/openclaw"),
    "/home/gabs/.local/share/foss-cmo-host/node-private",
  );
});

test("launcher target and default npm prefix receive bounded mounts", () => {
  const home = "/home/operator/private-home";
  assert.equal(
    protectedHomeRuntimeMountRoot(home, "/home/operator/private-home/.npm-global/bin/openclaw"),
    "/home/operator/private-home/.npm-global",
  );
  assert.equal(
    protectedHomeRuntimeMountRoot(home, "/home/operator/runtime/lib/node_modules/openclaw/openclaw.mjs"),
    "/home/operator/runtime/lib/node_modules/openclaw",
  );
  assert.equal(protectedHomeRuntimeMountRoot(home, "/usr/bin/node"), null);
});
