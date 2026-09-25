// Test-only campaign child fixture. Executed by the M5 bounded supervisor in
// tests as if it were the inner Python campaign executable. Never used in
// production. Reads a mode from process.argv[2]; modes that fork a descendant
// accept an owner-private PID file path in process.argv[3] so tests can verify
// the exact descendant PID is gone (no pgrep substring search). Liveness is
// driven by a referenced timer (never an unresolved top-level await alone,
// which Node would exit with code 13).
const mode = process.argv[2];
const pidFile = process.argv[3];
const progress = JSON.stringify({
  operation: "pixel-portal-outcome-battery-campaign-progress",
  campaignId: "outcomebattery-" + "a".repeat(24),
  profile: "builder", evaluationRegime: "matched-budget", partition: "tuning",
  runtimeCondition: "cold-first-request", status: "pass",
  requiredPairs: 1, completedPairs: 1,
  tuningBaselineFrozen: false, tuningBaselineFreezeSha256: null,
});
// Keep the event loop alive with a referenced timer AND block on an unresolved
// promise. The referenced timer prevents Node's code-13 unsettled-await exit;
// the process stays alive until the supervisor SIGKILLs it.
async function keepAlive() {
  setInterval(() => {}, 1000);
  process.stdin.resume();
  await new Promise(() => {});
}
if (mode === "progress") { process.stdout.write(progress); process.exit(0); }
if (mode === "progress-exit3") { process.stdout.write(progress); process.exit(3); }
if (mode === "overflow") { process.stdout.write("x".repeat(4 * 1024 * 1024)); process.exit(0); }
if (mode === "stderr-overflow") { process.stderr.write("y".repeat(4 * 1024 * 1024)); process.exit(0); }
if (mode === "exit2") { process.stderr.write("exit-2-diagnostic"); process.exit(2); }
if (mode === "stderr") { process.stderr.write("stderr-diagnostic-bytes"); process.exit(1); }
if (mode === "timeout") { await keepAlive(); }
// Fork a descendant that stays in the SAME process group (detached:false),
// writes its exact PID to the owner-private PID file, and keeps stdout/stderr
// open while ignoring normal termination. The parent stays alive so the
// supervisor's timeout fires and the process-group SIGKILL must reap both the
// parent and the descendant.
if (mode === "fork-descendant") {
  const { spawn } = await import("node:child_process");
  const { writeFile } = await import("node:fs/promises");
  const descendant = spawn(process.execPath, [new URL(import.meta.url).pathname, "descendant-hold"], {
    stdio: ["ignore", "inherit", "inherit"],
    detached: false,
  });
  if (pidFile) await writeFile(pidFile, String(descendant.pid), { mode: 0o600 });
  await keepAlive();
}
// Fork a descendant that calls setsid (detached:true) to leave the inner
// child's process group. The supervisor's process-group SIGKILL alone cannot
// reach it; the descendant writes its exact PID to the owner-private PID file
// so the test can clean up that exact PID in t.after.
if (mode === "detached-descendant") {
  const { spawn } = await import("node:child_process");
  const descendant = spawn(process.execPath, [new URL(import.meta.url).pathname, "detached-hold"], {
    // This fixture proves process-group escape, not inherited-pipe behavior.
    // Detach every descriptor so the supervisor closing its bounded pipes
    // cannot make the descendant exit on EPIPE and invalidate the premise.
    stdio: ["ignore", "ignore", "ignore"],
    detached: true,
    env: { ...process.env, PIXEL_TEST_DETACHED_PID_FILE: pidFile ?? "" },
  });
  descendant.unref();
  await keepAlive();
}
if (mode === "detached-hold") {
  // Write readiness from the detached process itself, after exec and setsid,
  // so the adversarial test never mistakes a merely allocated PID for a live
  // process that actually escaped the supervisor's process group.
  const { writeFile } = await import("node:fs/promises");
  const readyPath = process.env.PIXEL_TEST_DETACHED_PID_FILE;
  if (readyPath) await writeFile(readyPath, String(process.pid), { mode: 0o600 });
  process.on("SIGTERM", () => {});
  process.on("SIGINT", () => {});
  await keepAlive();
}
if (mode === "descendant-hold") {
  // Hold stdout/stderr open and write slowly (well under the bound) so the
  // supervisor's timeout is what must terminate the process group; the
  // descendant ignores normal termination signals.
  const hold = Buffer.alloc(16, 0x41);
  setInterval(() => { process.stdout.write(hold); }, 1000);
  process.on("SIGTERM", () => {});
  process.on("SIGINT", () => {});
  await keepAlive();
}
process.stderr.write("unknown-mode");
process.exit(9);
