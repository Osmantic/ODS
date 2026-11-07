import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {isolateSdkImports} from './isolated-sdk-imports.mjs';

test('fixed fixture bindings preserve exact SDK arguments and reject unexpected calls', () => {
  const calls = [], context = {bridge(name, args) {
    assert.equal(name, 'resolveUserPath', 'unexpected SDK call');
    calls.push(args);
    return args[0];
  }};
  const source = 'import {resolveUserPath, getSessionEntry} from "openclaw/plugin-sdk/agent-runtime";\n'
    + 'globalThis.result = resolveUserPath("fixture-path", {check: true});\n';
  vm.runInNewContext(isolateSdkImports(source, 'bridge'), context);
  assert.equal(context.result, 'fixture-path');
  assert.equal(calls.length, 1);
  assert.equal(JSON.stringify(calls[0]), '["fixture-path",{"check":true}]');
  assert.throws(() => vm.runInNewContext('getSessionEntry()', context), /unexpected SDK call/);
});

test('source import captures cannot construct declarations or invoke another expression', () => {
  for (const names of ['newSdkExport', 'resolveUserPath as other', 'resolveUserPath; globalThis.injected = true']) {
    assert.throws(() => isolateSdkImports(`import {${names}} from "openclaw/plugin-sdk/agent-runtime";`, 'bridge'),
      /Unknown SDK fixture binding/);
  }
  assert.throws(() => isolateSdkImports('import sdk from "openclaw/plugin-sdk/agent-runtime";', 'bridge'),
    /all SDK imports are isolated/);
});

test('the bridge selector remains string data, including quotes and statement punctuation', () => {
  const hook = 'fixture"]; globalThis.injected = true; ["', context = {[hook]: (_name, args) => args[0]};
  vm.runInNewContext(isolateSdkImports('globalThis.result = resolveUserPath("fixture-path");', hook), context);
  assert.equal(context.result, 'fixture-path');
  assert.equal(context.injected, undefined);
});
