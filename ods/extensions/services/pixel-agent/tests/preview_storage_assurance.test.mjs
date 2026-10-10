import test from 'node:test';
import assert from 'node:assert/strict';
import {unsupportedPreviewStorageClaim} from '../plugin/preview-storage-assurance.mjs';

const TOWER3_STORAGE_CLAIM = 'The inspection tool runs in an isolated browser session, so I cannot directly verify cross-reload persistence in this environment. However, the localStorage implementation is standard and will persist the book list across page reloads in a normal browser.';

for (const answer of [
  TOWER3_STORAGE_CLAIM,
  'For actual persistence, open it in your regular browser.',
  'Your list will persist after reload.',
  '**localStorage** will persist the list across page reloads.',
  '`localStorage` ensures persistence across reloads.',
  'Persistence is unverified. However, your changes will survive a refresh.',
  'I could not test it, but the data stays saved after reload.',
  'You reported that saving failed, but the list will persist after reload.',
]) test('rejects unsupported preview guarantee: '+answer,()=>assert.equal(unsupportedPreviewStorageClaim(answer),true));

for (const answer of [
  'I verified Add and the unread count. Reload persistence remains unverified.',
  'The list will not persist after reload.',
  'The list does not persist across reloads.',
  'I cannot guarantee that your list will persist after reload.',
  'I have not verified that it persists after reload.',
  'The list may persist after reload in a different viewing mode.',
  'If browser storage is available, it will persist across reloads.',
  'You verified that the URL-based list survives reload.',
  'As you independently observed, the URL-based list survives reload.',
  'The owner personally tested that the list persists after reload.',
  'According to your test, the standalone list persists after reload.',
  'You confirmed reload success; I did not independently test persistence.',
  'Does the list persist after reload?',
  'Test whether the state persists after reload.',
  'For actual persistence, do not assume a regular browser is sufficient.',
  'Add and toggle call saveBooks(); storage errors fall back to memory.',
  'The exported JSON lets you save a copy; import it to restore the list.',
  'Read https://docs.example/persists/after/reload for implementation details.',
  'See [the storage guide](https://docs.example/persists-after-reload).',
  '> Your list will persist after reload.\nThat earlier statement was incorrect.',
  'I incorrectly said “your list will persist after reload”. It is unverified.',
  "I incorrectly said 'your list will persist after reload'. It is unverified.",
  '```text\nThe list will persist after reload.\n```\nThis example is not evidence.',
]) test('preserves scoped or non-asserted prose: '+answer,()=>assert.equal(unsupportedPreviewStorageClaim(answer),false));
