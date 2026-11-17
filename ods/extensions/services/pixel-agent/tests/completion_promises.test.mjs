import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompletionAssurance, promisesExecution} from '../plugin/completion-assurance.mjs';
import {partialFinalizationAnswer, progressFinalizationAnswer} from '../plugin/progress-finalization.mjs';

const promises = [
  'Let me create the new folder, copy the file, then repair the form.',
  "Now the repair — I'll replace the form section and its JS:",
  "Now I'll create the new folder.",
  'Next, I will copy the file.',
  "I'll now replace the form section.",
  'I’ll create the new folder now.',
  'I will write the file.',
  'I will publish the preview.',
  "I'll create the new folder now.",
  'Then I will repair the form.',
  'I will check if the file exists.',
  "I'll write Bob's configuration file.",
  '> I will publish an example.\nLet me create the actual file.',
  '```I will publish an example.```\nLet me create the actual file.',
  'The example says "I will create a folder."\nI will publish the actual preview.',
];
for (const text of promises) test(`unfinished action is recognized: ${text}`, () => {
  assert.equal(promisesExecution(text), true);
  const guard = createCompletionAssurance();
  guard.begin('Copy demo.html into a new folder and repair the form validation');
  guard.observe('read', {result:{content:[{type:'text',text:'<form></form>'}]}});
  assert.equal(guard.finalize(text)?.action, 'revise');
  assert.equal(guard.finalize(text)?.action, 'revise');
  assert.equal(guard.finalize(text)?.action, 'finalize');
  assert.equal(guard.terminalStatus, 'failed');
  assert.match(guard.terminal, /The task is incomplete/);
});

const nonActions = [
  'Let me know if you want a new folder.',
  'I created the new folder and copied the file.',
  'I will be happy to help.',
  'May I create the folder?',
  'I will create the folder, okay?',
  'If you approve, I will publish the preview.',
  'I will publish the preview if you want.',
  'I will write the file if approved.',
  'You said: "Example one. I will publish the preview."',
  '“Example one. I’ll create the folder.”',
  'The manual says: "An example follows.\nI will publish the preview."',
  'The manual says: “An example follows.\nI’ll create the folder.”',
  "'Example one. I'll create the folder.'",
  '> Example one. I will publish the preview.',
  'Example: `First step. I will publish the preview.`',
  'Example: ``First ` step. I will publish the preview.``',
  '```text\nI will write the file.\n```',
  '~~~text\nLet me create the file.\n~~~',
  '~~~text~info\nLet me create the file.\n~~~',
  '~~~text\nLet me create the file.',
  '    First step. I will publish the preview.',
];
for (const text of nonActions) test(`example, result or consent request is not an action: ${text}`, () => {
  assert.equal(promisesExecution(text), false);
});

const substantive = 'The copied form contains three input controls with matching labels and the original validation handlers. ' +
  'The email field still accepts an empty value because its required attribute is absent, and the submit handler does not check that condition.';

test('tool-limit answers retain substantive evidence alongside a newly recognized promise', () => {
  for (const promise of promises.slice(0,9)) {
    const answer = `${promise}\n\n${substantive}`;
    assert.ok(answer.length < 900);
    assert.equal(progressFinalizationAnswer(answer), answer);
    assert.equal(partialFinalizationAnswer(answer), answer);
  }
});

test('long narration cannot satisfy the existing substantive partial-answer threshold', () => {
  const answer = 'Now the repair — I’ll replace the entire form section, its validation handlers and associated scripts after checking every current input field.\n' +
    'Let me create a separate working folder, copy the original page into that folder and write a replacement with all the requested validation rules.';
  assert.ok(answer.match(/[\p{L}\p{N}]/gu).length >= 160);
  assert.equal(progressFinalizationAnswer(answer), undefined);
  assert.equal(partialFinalizationAnswer(answer), undefined);
});

test('short statements of progress do not become substantive partial answers', () => {
  assert.equal(progressFinalizationAnswer('Let me create the file. The copy exists.'), undefined);
  assert.equal(partialFinalizationAnswer('Let me create the file. The copy exists.'), undefined);
});
