import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// The installed replacement runs inside OpenClaw's observeModelCallStream. The
// stand-in preamble provides only the two values its proxy block uses: an
// observed iterator over the transport and an observed result function, both
// counting what the diagnostic observer sees. The wrappers below have the
// shapes of OpenClaw's own stream wrappers. The real composed runtime is
// covered by runtime_diagnostic_stream_writes.integration.mjs.
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-diagnostic-stream-writes.json', import.meta.url)));
assert.equal(manifest.replacements.length, 1);
const [[pinnedBlock, repairedBlock]] = manifest.replacements;
assert.ok(pinnedBlock.startsWith('\treturn new Proxy(stream, '), 'test the installed transformation, not a copied implementation');

function observerFrom(block) {
  return vm.runInThisContext(`(function observeModelCallStream(stream, seen) {
    const asyncIterator = stream[Symbol.asyncIterator];
    const createIterator = () => asyncIterator.call(stream);
    const observedIterator = () => (async function* () {
      const iterator = createIterator();
      for (;;) {
        const next = await iterator.next();
        if (next.done) break;
        seen.events.push(next.value.type);
        yield next.value;
      }
    })()[Symbol.asyncIterator]();
    const resultFn = typeof stream.result === "function" ? stream.result : undefined;
    const observedResult = resultFn && ((...args) => { seen.results += 1; return resultFn.apply(stream, args); });
${block}})`);
}
const pinned = observerFrom(pinnedBlock);
const repaired = observerFrom(repairedBlock);

// A Python write whose source has a real line break right after "as f:". The
// pinned transport's streaming JSON repair reads "f:" as a Windows drive prefix
// and stores each later \n escape as a literal backslash-n (recorded on macOS).
const RAW = '{"path":"a.py","content":"with open(p, \\"w\\") as f:\\n    json.dump(x, f)\\n"}';
const MODEL_ARGS = {path: 'a.py', content: 'with open(p, "w") as f:\n    json.dump(x, f)\n'};
const TRANSPORT_ARGS = {path: 'a.py', content: 'with open(p, "w") as f:\\n    json.dump(x, f)\\n'};
assert.deepEqual(JSON.parse(RAW), MODEL_ARGS);

// Shape of the transport's event stream: iterator and result() on the prototype.
class TransportStream {
  constructor(deltas, parsed) {
    this.queue = [];
    const message = {role: 'assistant', content: [{type: 'toolCall', id: 'call-1', name: 'write', arguments: parsed}]};
    this.events = [{type: 'start'}, {type: 'toolcall_start', contentIndex: 0},
      ...deltas.map((delta) => ({type: 'toolcall_delta', contentIndex: 0, delta})),
      {type: 'toolcall_end', contentIndex: 0, toolCall: message.content[0]}, {type: 'done', message}];
    this.message = message;
  }
  push(event) { this.queue.push(event); }
  async *[Symbol.asyncIterator]() { yield* this.events; }
  async result() { return this.message; }
}
const transport = () => new TransportStream([RAW.slice(0, 20), RAW.slice(20, 55), RAW.slice(55)], structuredClone(TRANSPORT_ARGS));

// Shape of OpenClaw's wrapStreamObjectEvents.
function wrapStreamObjectEvents(stream, onEvent) {
  const originalAsyncIterator = stream[Symbol.asyncIterator].bind(stream);
  stream[Symbol.asyncIterator] = function() {
    const iterator = originalAsyncIterator();
    return {
      async next() {
        const result = await iterator.next();
        if (!result.done && result.value && typeof result.value === 'object') await onEvent(result.value);
        return result;
      },
      async return(value) { return iterator.return?.(value) ?? {done: true, value: undefined}; },
    };
  };
  return stream;
}
// Shape of wrapStreamRepairMalformedToolCallArguments on its strict-parse path:
// the raw argument text is re-parsed and replaces the transport's arguments.
function wrapStreamRepairArguments(stream, log) {
  let partial = '', parsed;
  const originalResult = stream.result.bind(stream);
  stream.result = async () => {
    const message = await originalResult();
    if (parsed) message.content[0].arguments = parsed;
    return message;
  };
  return wrapStreamObjectEvents(stream, (event) => {
    log.push(event.type);
    if (event.type === 'toolcall_delta') {
      partial += event.delta;
      if (/[}\]]/.test(event.delta)) parsed = JSON.parse(partial);
    }
    if (event.type === 'toolcall_end' && parsed) event.toolCall.arguments = parsed;
  });
}
async function drain(stream) {
  const ends = [];
  for await (const event of stream) if (event.type === 'toolcall_end') ends.push(structuredClone(event.toolCall.arguments));
  return {ends, result: structuredClone((await stream.result()).content[0].arguments)};
}
const EVENTS = ['start', 'toolcall_start', 'toolcall_delta', 'toolcall_delta', 'toolcall_delta', 'toolcall_end', 'done'];

test('macOS order: the outer argument repair re-parses the model\'s exact JSON through the observer', async () => {
  const seen = {events: [], results: 0}, repairLog = [];
  const stream = wrapStreamRepairArguments(repaired(transport(), seen), repairLog);
  const {ends, result} = await drain(stream);
  assert.deepEqual(ends, [MODEL_ARGS]);
  assert.deepEqual(result, MODEL_ARGS, 'the executed tool call carries real line breaks');
  assert.deepEqual(repairLog, EVENTS, 'the outer wrapper sees every event');
  assert.deepEqual(seen, {events: EVENTS, results: 1}, 'the diagnostic observer still sees the raw transport stream');
});

test('the pinned observer ignores the outer wrapper and keeps the transport\'s corrupted arguments', async () => {
  const seen = {events: [], results: 0}, repairLog = [];
  const {ends, result} = await drain(wrapStreamRepairArguments(pinned(transport(), seen), repairLog));
  assert.deepEqual(ends, [TRANSPORT_ARGS]);
  assert.deepEqual(result, TRANSPORT_ARGS);
  assert.deepEqual(repairLog, [], 'the replacement iterator was written to the target and never read');
  assert.deepEqual(seen, {events: EVENTS, results: 1});
});

test('stacked outer wrappers compose in application order and each sees every event once', async () => {
  const seen = {events: [], results: 0}, logs = {inner: [], middle: [], outer: []};
  let stream = repaired(transport(), seen);
  for (const name of ['inner', 'middle', 'outer']) {
    const originalResult = stream.result.bind(stream);
    stream.result = async () => { const message = await originalResult(); message.order = [...(message.order ?? []), name]; return message; };
    stream = wrapStreamObjectEvents(stream, (event) => { logs[name].push(event.type); });
  }
  for await (const event of stream) assert.ok(event);
  assert.deepEqual((await stream.result()).order, ['inner', 'middle', 'outer']);
  assert.deepEqual(logs, {inner: EVENTS, middle: EVENTS, outer: EVENTS});
  assert.deepEqual(seen, {events: EVENTS, results: 1});
});

test('upstream order, with the observer outermost, is unchanged by the repair', async () => {
  const outcomes = [];
  for (const observe of [pinned, repaired]) {
    const seen = {events: [], results: 0}, repairLog = [];
    outcomes.push({...await drain(observe(wrapStreamRepairArguments(transport(), repairLog), seen)), repairLog, seen});
  }
  assert.deepEqual(outcomes[1], outcomes[0]);
  assert.deepEqual(outcomes[1].result, MODEL_ARGS);
});

test('other writes still land on the stream and its methods stay bound to it', async () => {
  const target = transport();
  const stream = repaired(target, {events: [], results: 0});
  stream.label = 'kept';
  assert.equal(target.label, 'kept');
  assert.equal(stream.label, 'kept');
  stream.push({type: 'late'});
  assert.deepEqual(target.queue, [{type: 'late'}]);
  const bare = {async *[Symbol.asyncIterator]() { yield {type: 'done'}; }};
  const withoutResult = repaired(bare, {events: [], results: 0});
  const replacement = async () => 'outer';
  withoutResult.result = replacement;
  assert.equal(Object.hasOwn(bare, 'result'), true, 'a stream without result() keeps the upstream write-through');
  assert.equal(await withoutResult.result(), 'outer');
});

test('repair is selected by Linux/WSL installation, foreign restore and native macOS composition', () => {
  const linux = readFileSync(new URL('../../../../installers/lib/pixel-host-install.sh', import.meta.url), 'utf8');
  const mac = readFileSync(new URL('../../../../installers/macos/lib/pixel-runtime-bundle.py', import.meta.url), 'utf8');
  const helper = readFileSync(new URL('../host/openclaw_tool_recovery.py', import.meta.url), 'utf8');
  assert.match(linux, /--openclaw-bin "\$openclaw_bin" --diagnostic-stream-writes/);
  assert.match(linux, /ods-runtime-patches\/diagnostic-stream-writes"/);
  assert.match(linux, /-f "\$plugin_root\/host\/openclaw-diagnostic-stream-writes\.json"/);
  assert.match(linux.split('--restore-foreign')[1].split('>>')[0], /\bdiagnostic-stream-writes\b/);
  assert.match(helper, /DIAGNOSTIC_STREAM_MODULE = "attempt\.model-diagnostic-events-DqqiPQPY\.js"/);
  assert.match(helper, /"--diagnostic-stream-writes"/);
  // The buffered stream-progress patch composes on the last (selection) repair.
  assert.match(mac, /\('openclaw-diagnostic-stream-writes\.json', 'attempt\.model-diagnostic-events-DqqiPQPY\.js'\),\r?\n\s+\('openclaw-compaction-budget\.json', 'selection-BEwSQKM-\.js'\),\r?\n\)/);
  assert.equal(manifest.version, '2026.6.33');
  assert.equal(manifest.sourceSha256, 'fe7f3e7b20d2ef191b28c88fc759781c90f33d93ff9bb834cba2055a70decdd2');
  assert.match(manifest.patchedSha256, /^[a-f0-9]{64}$/);
  assert.deepEqual(manifest.previousReplacements, {});
});
