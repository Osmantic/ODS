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
  for (const name of ['OBSERVE_ELEMENT', 'FOCUSED_ELEMENT', 'DIAGNOSTIC', 'SELECTOR_COUNT', 'ROLE_NAME_INCLUDING_HIDDEN', 'ROLE_NAME_RENDERED',
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

// The native fill denylist decides whether the capsule may type into a field at
// all, so it is exercised against a minimal DOM instead of only compiled.
function fillEligibility(attributes, label = '') {
  const context = vm.createContext({});
  class Input { constructor() { this.type = 'text'; this.value = ''; this.readOnly = false; } }
  class TextArea {}
  context.HTMLInputElement = Input; context.HTMLTextAreaElement = TextArea; context.HTMLSelectElement = class {};
  context.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1'});
  context.document = {getElementById: () => null};
  const element = Object.assign(new Input(), {
    labels: label ? [{textContent: label}] : [],
    getClientRects: () => [{width: 10, height: 10}],
    checkVisibility: () => true,
    hasAttribute: () => false,
    getAttribute: name => attributes[name] ?? null,
    matches: () => false,
  });
  const observe = new vm.Script(`(${capsuleScripts().OBSERVE_ELEMENT})`).runInContext(context);
  return observe.call(element, false, null, 'value', false).input.eligible;
}

test('native text fill refuses fields whose name, label or autocomplete marks them sensitive', () => {
  assert.equal(fillEligibility({name: 'nickname'}), true);
  assert.equal(fillEligibility({name: 'shipping-note'}), true, 'a substring of an ordinary word is not a secret');
  for (const [attributes, label] of [
    [{name: 'password'}, ''], [{id: 'api-key'}, ''], [{'aria-label': 'One-time code'}, ''],
    [{name: 'cvv'}, ''], [{name: 'cvc'}, ''], [{name: 'pin'}, ''], [{id: 'user_pin'}, ''], [{name: 'ssn'}, ''],
    [{name: 'iban'}, ''], [{name: 'passphrase'}, ''], [{name: 'passcode'}, ''], [{name: 'mfa'}, ''],
    [{name: 'private_key'}, ''], [{name: 'seed-phrase'}, ''], [{name: 'security_code'}, ''],
    [{name: 'x'}, 'Social security number'], [{name: 'x'}, 'Account number'], [{name: 'x'}, 'Routing number'],
    [{name: 'x'}, 'CPF'],
  ]) assert.equal(fillEligibility(attributes, label), false, JSON.stringify([attributes, label]));
});

// Press refuses text entry, so this classification decides whether a trusted
// key may reach an element at all; it runs against a minimal DOM as well.
let observeSource;
function focusState(make, {active, designMode = 'off', hasFocus = true, mode = 'observe'} = {}) {
  observeSource ??= capsuleScripts().OBSERVE_ELEMENT;
  const context = vm.createContext({});
  for (const name of ['HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLIFrameElement',
    'HTMLFrameElement', 'HTMLObjectElement', 'HTMLEmbedElement']) context[name] = class {};
  context.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1'});
  const element = Object.assign(make(context), {getClientRects: () => [{width: 10, height: 10}],
    checkVisibility: () => true, hasAttribute: () => false, getAttribute: () => null});
  context.document = {designMode, body: {}, documentElement: {}, hasFocus: () => hasFocus};
  context.document.activeElement = active === undefined ? element : active(context);
  const observe = new vm.Script(`(${observeSource})`).runInContext(context);
  // Copied out of the vm realm, whose Object prototype differs.
  return JSON.parse(JSON.stringify(observe.call(element, false, null, null, false, mode).focus));
}

test('press refuses text entry, nested frames and editable focus, but not key-only controls', () => {
  const input = type => c => Object.assign(new c.HTMLInputElement(), {type});
  for (const type of ['button', 'checkbox', 'image', 'radio', 'range', 'reset', 'submit'])
    assert.deepEqual(focusState(input(type)), {target: false, active: false, element: true, document: true}, type);
  for (const make of [...['text', 'password', 'email', 'number', 'date', 'search', 'color', 'file', 'hidden'].map(input),
    c => new c.HTMLTextAreaElement(), c => new c.HTMLSelectElement(), c => ({isContentEditable: true}),
    c => new c.HTMLIFrameElement(), c => new c.HTMLFrameElement(), c => new c.HTMLObjectElement(), c => new c.HTMLEmbedElement()])
    assert.equal(focusState(make).target, true, make.toString());
  const canvas = () => ({});
  assert.deepEqual(focusState(canvas, {designMode: 'on'}), {target: true, active: true, element: true, document: true});
  assert.deepEqual(focusState(canvas, {active: () => ({}), hasFocus: false}), {target: false, active: false, element: false, document: false});
  assert.deepEqual(focusState(canvas, {active: input('password')}), {target: false, active: true, element: false, document: true});
  assert.equal(focusState(canvas, {active: () => null}).active, false, 'no focused element is not an editable one');
});

test('press follows open shadow roots to the focused element; a closed root stops at its host', () => {
  // document.activeElement reports only the shadow host of a web-component
  // field that took focus through delegatesFocus.
  const host = inner => () => ({shadowRoot: {activeElement: inner}});
  const password = c => Object.assign(new c.HTMLInputElement(), {type: 'password'});
  assert.deepEqual(focusState(c => ({}), {active: c => host(password(c))()}), {target: false, active: true, element: false, document: true});
  // Nested open roots, and a target that is itself inside an open root.
  assert.equal(focusState(c => ({}), {active: c => host(host(password(c))())()}).active, true);
  let inner;
  assert.equal(focusState(() => (inner = {}), {active: () => host(inner)()}).element, true);
  // A focused host whose root holds no focus is itself the focused element.
  assert.deepEqual(focusState(c => ({}), {active: host(null)}), {target: false, active: false, element: false, document: true});
  // A closed root is invisible here (shadowRoot is null); the capsule asks CDP.
  assert.equal(focusState(c => ({}), {active: () => ({shadowRoot: null})}).active, false);
  // FOCUSED_ELEMENT returns the same element as a remote object.
  const scripts = capsuleScripts(), field = {};
  const context = vm.createContext({document: {activeElement: {shadowRoot: {activeElement: {shadowRoot: {activeElement: field}}}}}});
  assert.equal(new vm.Script(`(${scripts.FOCUSED_ELEMENT})`).runInContext(context)(), field);
});

test('press releases focus another element holds, never the target, the body or before a refusal', () => {
  let blurs = 0;
  const held = c => ({blur() { blurs++; c.document.activeElement = c.document.body; }});
  const canvas = () => ({});
  assert.deepEqual(focusState(canvas, {active: held}), {target: false, active: false, element: false, document: true});
  assert.equal(blurs, 0, 'observing never moves focus');
  assert.deepEqual(focusState(canvas, {active: held, mode: 'release'}), {target: false, active: false, element: false, document: true});
  assert.equal(blurs, 1);
  // The target itself and the body carry no blur here: calling it would throw.
  assert.equal(focusState(canvas, {mode: 'release'}).element, true);
  assert.equal(focusState(canvas, {active: c => c.document.body, mode: 'release'}).element, false);
  assert.equal(focusState(c => Object.assign(new c.HTMLInputElement(), {type: 'password'}), {active: held, mode: 'release'}).target, true);
  assert.equal(blurs, 1, 'a refused text field leaves focus where it was');
});

// assert-painted reads geometry only: the content box, the largest corner
// radii and the viewport, through the isolated world's own layout APIs.
function paintBox(style, rect = {left: 10, top: 20, right: 210, bottom: 170, width: 200, height: 150}) {
  observeSource ??= capsuleScripts().OBSERVE_ELEMENT;
  const context = vm.createContext({innerWidth: 800, innerHeight: 600});
  context.getComputedStyle = () => ({display: 'block', visibility: 'visible', opacity: '1', ...style});
  const element = {getClientRects: () => [{width: 10, height: 10}], getBoundingClientRect: () => rect,
    checkVisibility: () => true, hasAttribute: () => false, getAttribute: () => null};
  const observe = new vm.Script(`(${observeSource})`).runInContext(context);
  return JSON.parse(JSON.stringify(observe.call(element, false, null, null, false, null, true).box));
}

test('assert-painted measures the content box, the largest corner radii and the viewport', () => {
  assert.deepEqual(paintBox({}), {left: 10, top: 20, right: 210, bottom: 170, radiusX: 0, radiusY: 0, viewportWidth: 800, viewportHeight: 600});
  assert.deepEqual(paintBox({borderLeftWidth: '2px', paddingLeft: '3px', borderTopWidth: '1px', paddingTop: '4px',
    borderRightWidth: '2px', paddingRight: '5px', borderBottomWidth: '1px', paddingBottom: '0px'}),
  {left: 15, top: 25, right: 203, bottom: 169, radiusX: 0, radiusY: 0, viewportWidth: 800, viewportHeight: 600});
  // Elliptical and percentage radii resolve against the border box, each at most half of it.
  const radii = style => { const {radiusX, radiusY} = paintBox(style); return [radiusX, radiusY]; };
  assert.deepEqual(radii({borderTopLeftRadius: '12px'}), [12, 12]);
  assert.deepEqual(radii({borderTopLeftRadius: '4px', borderBottomRightRadius: '20px 6px'}), [20, 6]);
  assert.deepEqual(radii({borderTopRightRadius: '10%'}), [20, 15]);
  assert.deepEqual(radii({borderTopLeftRadius: '9999px'}), [100, 75]);
  assert.deepEqual(radii({borderBottomLeftRadius: '50%'}), [100, 75]);
});

test('native numeric fill rejects nonfinite syntax before invoking any setter or event', () => {
  let writes=0,events=0;
  class Input {
    constructor() { this.type='number';this.previous='834739';this.readOnly=false;
      this.validity={valueMissing:false,rangeUnderflow:false,rangeOverflow:false,stepMismatch:false,badInput:false}; }
    get value() { return this.previous; }
    set value(value) { writes++;this.previous=value; }
  }
  const context=vm.createContext({HTMLInputElement:Input,HTMLTextAreaElement:class {},HTMLSelectElement:class {},
    InputEvent:class {},Event:class {},getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'}),
    document:{getElementById:()=>null}});
  const element=Object.assign(new Input(),{labels:[],getClientRects:()=>[{width:10,height:10}],
    checkVisibility:()=>true,hasAttribute:()=>false,getAttribute:()=>null,matches:()=>false,
    dispatchEvent:()=>{events++;}});
  const observe=new vm.Script(`(${capsuleScripts().OBSERVE_ELEMENT})`).runInContext(context);
  for(const value of ['NaN','Infinity','1e999','1,5',' 3 ','+1','0x10','1.']) {
    const result=observe.call(element,false,null,value,true);
    assert.equal(result.input.numeric.syntaxValid,false);
    assert.equal(result.input.matches,false);
    assert.equal(writes,0);assert.equal(events,0);
    assert.equal(JSON.stringify(result).includes('834739'),false);
  }
  for(const value of ['100','0','.5','-1.25e2','']) {
    const result=observe.call(element,false,null,value,true);
    assert.equal(result.input.numeric.syntaxValid,true);
    assert.equal(result.input.matches,true);
  }
  assert.equal(writes,5);assert.equal(events,10);
});
