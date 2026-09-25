import assert from "node:assert/strict";
import test from "node:test";

import { renderTmpHygieneUnits, TmpHygieneUnitError } from "../deploy/supported-host/tmp-hygiene-units.mjs";

test("temp hygiene units sweep only the aged leak files with least privilege", () => {
  const { service, timer, execStart } = renderTmpHygieneUnits({
    serviceUser: "pixel-work", serviceGroup: "pixel-work",
    ageMinutes: 360, intervalMinutes: 60,
  });

  // Least privilege: never root, hardened, writable only in the temp dir.
  assert.match(service, /\nType=oneshot\n/);
  assert.match(service, /\nUser=pixel-work\n/);
  assert.doesNotMatch(service, /\nUser=root\n/);
  assert.match(service, /\nProtectSystem=strict\n/);
  assert.match(service, /\nNoNewPrivileges=true\n/);
  assert.match(service, /\nReadWritePaths=\/tmp\n/);
  assert.match(service, /\nCapabilityBoundingSet=\n/);

  // The sweep is bounded on BOTH the fixed leak name and a positive age, and is a delete of
  // regular files only at depth 1 — never an unbounded or recursive removal.
  assert.equal(
    execStart,
    "/usr/bin/find /tmp -xdev -mindepth 1 -maxdepth 1 -type f -name .dd*-*.so -mmin +360 -delete",
  );
  assert.match(execStart, /-name \.dd\*-\*\.so /);
  assert.match(execStart, /-mmin \+360 /);
  assert.match(execStart, /-maxdepth 1 /);
  assert.doesNotMatch(execStart, /rm |-rf|-exec|-mindepth 0|-delete\s+\S/);

  // Timer is periodic and catches up after downtime.
  assert.match(timer, /\nOnUnitActiveSec=60min\n/);
  assert.match(timer, /\nPersistent=true\n/);
  assert.match(timer, /\nWantedBy=timers\.target\n/);
});

test("temp hygiene units reject privileged, unsafe, or unbounded configuration", () => {
  assert.throws(() => renderTmpHygieneUnits({ serviceUser: "root" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ serviceUser: "Pixel Work" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ findPath: "find" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ findPath: "/usr/bin/find; rm -rf /" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ tmpDir: "/home/pixel" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ tmpDir: "/" }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ ageMinutes: 5 }), TmpHygieneUnitError); // below the 60-minute floor
  assert.throws(() => renderTmpHygieneUnits({ ageMinutes: 100000 }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ intervalMinutes: 0 }), TmpHygieneUnitError);
  assert.throws(() => renderTmpHygieneUnits({ intervalMinutes: 5000 }), TmpHygieneUnitError);
});

test("temp hygiene age floor keeps recent files that a running agent may hold mapped", () => {
  // A one-hour minimum age means the sweep never removes freshly extracted objects that the
  // live agent process may still have dlopen-mapped.
  const floor = renderTmpHygieneUnits({ ageMinutes: 60 });
  assert.match(floor.execStart, /-mmin \+60 /);
  assert.equal(floor.ageMinutes, 60);
  const value = renderTmpHygieneUnits({ ageMinutes: 720 });
  assert.match(value.execStart, /-mmin \+720 /);
});

test("temp hygiene render is deterministic for identical inputs", () => {
  const a = renderTmpHygieneUnits({ serviceUser: "pixel-work", ageMinutes: 360, intervalMinutes: 60 });
  const b = renderTmpHygieneUnits({ serviceUser: "pixel-work", ageMinutes: 360, intervalMinutes: 60 });
  assert.deepEqual(a, b);
});
