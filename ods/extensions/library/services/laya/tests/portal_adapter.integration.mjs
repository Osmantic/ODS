// Real service qualification: no mocked engine, HTTP transport or adapter.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {performance} from 'node:perf_hooks';
import {createLayaClient} from '../../../../services/pixel-agent/plugin/laya-client.mjs';

assert.ok(process.env.LAYA_TEST_KEY_FILE, 'Set the private test key file');
const token = readFileSync(process.env.LAYA_TEST_KEY_FILE, 'utf8').trim();
const port = Number(process.env.LAYA_TEST_PORT ?? 8017);
const client = createLayaClient({token, port});
const health = await client.status();
assert.equal(health.status, 'loaded');
assert.equal(health.inferenceVerified, false, 'upstream health alone never proves inference');
const readiness = await fetch(`http://127.0.0.1:${port}/ready`, {signal: AbortSignal.timeout(5000)});
assert.deepEqual(await readiness.json(), {status: 'ok', startupInferenceVerified: true});

const questions = [
  {id: 'department', type: 'choice', instructions: 'Which department handles this request?',
    choices: [{id: 'billing', description: 'payments and refunds'},
      {id: 'technical', description: 'software bugs'}, {id: 'other', description: 'other topics'}]},
  {id: 'urgency', type: 'score', instructions: 'How urgent is this request?', levels: ['low', 'medium', 'high']},
  {id: 'refund', type: 'noul', instructions: 'Is the customer asking for a refund?'},
];
const cases = [
  {checkpoint: 'english', language: 'en', text: 'I was charged twice this month. Please refund the duplicate charge.'},
  {checkpoint: 'multilingual', language: 'pt', text: 'Fui cobrado duas vezes neste mes. Quero o reembolso da cobranca duplicada.'},
  {checkpoint: 'typed-decisions', language: 'en', text: 'A customer requests a refund for a duplicate charge. Route this support ticket.'},
];
const measurements = [];
for (const {checkpoint, language, text} of cases) {
  const started = performance.now();
  const result = await client.decide({items: [{id: 'ticket', text}], questions, checkpoint, language});
  assert.equal(result.completeContext, true);
  assert.equal(result.advisory, true);
  assert.equal(result.items[0].id, 'ticket');
  assert.equal(result.items[0].answers.department.choice, 'billing');
  assert.doesNotMatch(JSON.stringify(result), /act_probability|Bearer/);
  measurements.push({checkpoint, language, milliseconds: Math.round(performance.now() - started)});
}
await assert.rejects(createLayaClient({token: 'invalid'.repeat(8), port})
  .decide({items: [{id: 'a', text: 'Hello'}], questions}), error => error.code === 'authentication_failed');
await assert.rejects(client.decide({items: [{id: 'long', text: 'Background text. '.repeat(1100)}],
  questions, checkpoint: 'english'}), error => error.code === 'truncated_context');
const longer = {items: [{id: 'context', text:
  'This background sentence describes a resolved software issue. '.repeat(70) + ' Please refund the duplicate charge.'}],
  questions: [{id: 'refund', type: 'noul', instructions: 'Does the customer ask for a refund?'}], checkpoint: 'english'};
await assert.rejects(client.decide(longer), error => error.code === 'truncated_context');
const complete = await client.decide({...longer, contextTokens: 2048});
assert.equal(complete.completeContext, true, 'larger relevant evidence is accepted only when consumed in full');
console.log(JSON.stringify({qualified: true, device: health.device, measurements}));
