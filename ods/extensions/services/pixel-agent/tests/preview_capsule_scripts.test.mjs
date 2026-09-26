import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

// The inspection capsule runs these scripts in a Chromium isolated world, but
// they live as JavaScript inside Python strings, and only the opt-in real
// browser suite executes them. Compile each one here, so a textual merge that
// declares a matcher helper twice or drops a bracket fails CI. Otherwise every
// role/name step would silently become "unavailable" on the fleet.
function capsuleScripts() {
  const script = 'import json,sys\nsys.path.insert(0,sys.argv[1])\nimport preview_inspection_capsule as c\n' +
    "print(json.dumps({k:v for k,v in vars(c).items() if k.isupper() and isinstance(v,str) and v.lstrip().startswith('function')}))\n";
  const result = spawnSync(process.platform === 'win32' ? 'python' : 'python3',
    ['-c', script, fileURLToPath(new URL('../host', import.meta.url))], {encoding: 'utf8', windowsHide: true});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

test('every capsule isolated-world script compiles', () => {
  const scripts = capsuleScripts();
  for (const name of ['OBSERVE_ELEMENT', 'DIAGNOSTIC', 'SELECTOR_COUNT', 'ROLE_NAME_INCLUDING_HIDDEN', 'ROLE_NAME_RENDERED',
    'CONTROL_NAMES'])
    assert.ok(name in scripts, name);
  for (const [name, source] of Object.entries(scripts))
    assert.doesNotThrow(() => new vm.Script(`(${source})`, {filename: name}), name);
});

test('the scripts sharing the name rules run; both role/name matchers keep the passed Chromium matches, de-duplicated by identity', () => {
  const scripts = capsuleScripts();
  const first = {}, second = {};
  for (const name of ['ROLE_NAME_INCLUDING_HIDDEN', 'ROLE_NAME_RENDERED']) {
    // An empty document: the rules' top-level declarations run, the walk finds nothing.
    const matcher = new vm.Script(`(${scripts[name]})`).runInContext(vm.createContext({document: {querySelectorAll: () => []}}));
    const matches = matcher('button', 'Show sold out', first, second, first);
    assert.equal(matches.length, 2, name);
    assert.ok(matches[0] === first && matches[1] === second, name);
  }
  const controls = new vm.Script(`(${scripts.CONTROL_NAMES})`).runInContext(vm.createContext({document: {querySelectorAll: () => []}}));
  assert.deepEqual(JSON.parse(JSON.stringify(controls(48))), {count: 0, items: []});
});
