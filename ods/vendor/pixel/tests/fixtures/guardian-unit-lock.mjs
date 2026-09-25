// Cross-process mutex so parallel test files that each create/remove the single
// throwaway user systemd unit (pixel-maintenance-recovery.service) never race on
// the shared user manager. mkdir is atomic across processes (EEXIST).
import { mkdir, rmdir } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";

const LOCK_DIR = "/tmp/pixel-guardian-unit-test.lock";
const MAX_WAIT_MS = 120000;

export async function acquireGuardianUnitLock() {
  const deadline = Date.now() + MAX_WAIT_MS;
  while (Date.now() < deadline) {
    try {
      await mkdir(LOCK_DIR, { mode: 0o700 });
      return;
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
      await delay(50);
    }
  }
  throw new Error("timed out waiting for the shared guardian unit lock");
}

export async function releaseGuardianUnitLock() {
  try { await rmdir(LOCK_DIR); } catch (error) { if (error?.code !== "ENOENT") throw error; }
}
