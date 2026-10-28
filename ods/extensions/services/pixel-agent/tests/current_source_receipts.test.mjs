import test from 'node:test';
import assert from 'node:assert/strict';
import { createCompletionAssurance } from '../plugin/completion-assurance.mjs';

// Helpers to build realistic tool events matching the shapes the plugin
// actually observes. These mirror the fields openedSourceUrls()/observe()
// read, so the tests exercise the real receipt path rather than constants.

function webFetchEvent({ url, finalUrl, status = 200, text = 'page body', title } = {}) {
  return {
    params: { url },
    result: {
      details: {
        status,
        url,
        finalUrl: finalUrl ?? url,
        text,
        ...(title !== undefined ? { title } : {}),
      },
      content: [{ type: 'text', text }],
    },
  };
}

function webExtractEvent({ sourceUrl, matched = true, mode, text = 'extracted body' } = {}) {
  return {
    params: { url: sourceUrl },
    result: {
      details: {
        boundary: 'public-web-read-only',
        matched,
        ...(mode !== undefined ? { mode } : {}),
        source_url: sourceUrl,
      },
      content: [{ type: 'text', text }],
    },
  };
}

function failedFetchEvent({ url, status = 500 } = {}) {
  return {
    params: { url },
    result: {
      details: { status, url, finalUrl: url, text: '' },
      content: [],
    },
  };
}

function searchEvent({ urls = [] } = {}) {
  return {
    params: { query: 'q' },
    result: {
      details: { results: urls.map(url => ({ url })) },
      content: [{ type: 'text', text: JSON.stringify({ results: urls.map(url => ({ url })) }) }],
    },
  };
}

function browserSnapshotEvent({ url, ok = true, status = 200, text = 'snapshot body' } = {}) {
  return {
    params: { action: 'snapshot' },
    result: {
      details: { ok, status, url },
      content: [{ type: 'text', text }],
    },
  };
}

function makeClock(start = 1_000_000) {
  let t = start;
  const now = () => t;
  now.advance = ms => { t += ms; };
  now.set = v => { t = v; };
  return now;
}

function freshAssurance(opts = {}) {
  const clock = opts.clock ?? makeClock();
  const assurance = createCompletionAssurance({ now: clock, ...opts });
  assurance.begin('please read the sources and summarize', {});
  return { assurance, clock };
}

test('successful web_fetch records a current-run source receipt', () => {
  const { assurance, clock } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/a' }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].url, 'https://example.com/a');
  assert.equal(receipts[0].timestamp, clock());
});

test('successful pixel_ods_web_extract records a receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('pixel_ods_web_extract', webExtractEvent({ sourceUrl: 'https://example.com/extract' }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].url, 'https://example.com/extract');
});

test('web_extract overview mode also records a receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('pixel_ods_web_extract', webExtractEvent({ sourceUrl: 'https://example.com/overview', matched: false, mode: 'overview' }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].url, 'https://example.com/overview');
});

test('failed fetch (non-2xx) does not record a receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', failedFetchEvent({ url: 'https://example.com/fail', status: 500 }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('failed fetch (empty text) does not record a receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/empty', text: '   ' }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('search results alone do not record receipts', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_search', searchEvent({ urls: ['https://example.com/lead1', 'https://example.com/lead2'] }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('browser snapshot does not record a source receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('browser', browserSnapshotEvent({ url: 'https://example.com/browser' }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('host verification does not record a source receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observeHostVerification('https://example.com/host-verified');
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('web_extract without matched/overview does not record a receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('pixel_ods_web_extract', webExtractEvent({ sourceUrl: 'https://example.com/nomatch', matched: false }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('returned receipts are cloned, not live references', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/clone' }));
  const first = assurance.currentRunSourceReceipts();
  first[0].url = 'https://evil.example/mutated';
  first[0].timestamp = -1;
  first.push({ url: 'https://evil.example/extra', timestamp: 0 });
  const second = assurance.currentRunSourceReceipts();
  assert.equal(second.length, 1);
  assert.equal(second[0].url, 'https://example.com/clone');
  assert.notEqual(second[0].timestamp, -1);
});

test('receipts are capped at 32 with most-recent-unique eviction', () => {
  const { assurance } = freshAssurance();
  for (let i = 0; i < 40; i++) {
    assurance.observe('web_fetch', webFetchEvent({ url: `https://example.com/page-${i}` }));
  }
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 32);
  // The oldest 8 (page-0..page-7) were evicted; page-8..page-39 remain.
  assert.equal(receipts[0].url, 'https://example.com/page-8');
  assert.equal(receipts.at(-1).url, 'https://example.com/page-39');
});

test('re-observing a URL refreshes its timestamp and moves it to the end', () => {
  const { assurance, clock } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/first' }));
  const t1 = clock();
  clock.advance(1000);
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/second' }));
  clock.advance(1000);
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/first' }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 2);
  assert.equal(receipts[0].url, 'https://example.com/second');
  assert.equal(receipts[1].url, 'https://example.com/first');
  assert.ok(receipts[1].timestamp > t1);
  assert.equal(receipts[1].timestamp, clock());
});

test('re-observing a URL does not grow the receipt set', () => {
  const { assurance } = freshAssurance();
  for (let i = 0; i < 5; i++) {
    assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/same' }));
  }
  assert.equal(assurance.currentRunSourceReceipts().length, 1);
});

test('receipts older than 30 minutes are excluded', () => {
  const { assurance, clock } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/old' }));
  clock.advance(30 * 60 * 1000 + 1);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('receipts exactly at the 30-minute boundary are still included', () => {
  const { assurance, clock } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/boundary' }));
  clock.advance(30 * 60 * 1000);
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].url, 'https://example.com/boundary');
});

test('receipts with a future timestamp are excluded', () => {
  const clock = makeClock(1_000_000);
  const assurance = createCompletionAssurance({ now: clock });
  assurance.begin('please read the sources', {});
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/future' }));
  // Rewind the clock so the recorded timestamp is now in the future.
  clock.set(1_000_000 - 1);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a cancelled run returns no receipts', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/cancelled' }));
  assert.equal(assurance.currentRunSourceReceipts().length, 1);
  assurance.cancel();
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a non-finite clock yields no receipts', () => {
  const clock = makeClock(1_000_000);
  const assurance = createCompletionAssurance({ now: clock });
  assurance.begin('please read the sources', {});
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/nan' }));
  clock.set(NaN);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('receipts are not shared across assurance instances', () => {
  const a = freshAssurance();
  a.assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/instance-a' }));
  const b = freshAssurance();
  assert.deepEqual(b.assurance.currentRunSourceReceipts(), []);
  assert.equal(a.assurance.currentRunSourceReceipts().length, 1);
});

test('web_fetch with a private/localhost URL records no receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'http://localhost/secret' }));
  assurance.observe('web_fetch', webFetchEvent({ url: 'http://service.internal/secret' }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('web_fetch with a non-http(s) URL records no receipt', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'ftp://example.com/file' }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a failed event (event.error) records no receipt', () => {
  const { assurance } = freshAssurance();
  const event = webFetchEvent({ url: 'https://example.com/errored' });
  event.error = new Error('boom');
  assurance.observe('web_fetch', event);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a result flagged isError records no receipt', () => {
  const { assurance } = freshAssurance();
  const event = webFetchEvent({ url: 'https://example.com/iserror' });
  event.result.isError = true;
  assurance.observe('web_fetch', event);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a details.status of failed/error/blocked records no receipt', () => {
  const { assurance } = freshAssurance();
  for (const status of ['failed', 'error', 'blocked', 'unavailable', 'invalid_request']) {
    const event = webFetchEvent({ url: `https://example.com/${status}` });
    event.result.details.status = status;
    assurance.observe('web_fetch', event);
  }
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('tool_call wrapper envelopes do not record receipts', () => {
  const { assurance } = freshAssurance();
  const inner = webFetchEvent({ url: 'https://example.com/wrapped' });
  assurance.observe('tool_call', inner);
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('discovery tools do not record receipts', () => {
  const { assurance } = freshAssurance();
  assurance.observe('tool_search', webFetchEvent({ url: 'https://example.com/discovery' }));
  assurance.observe('tool_describe', webFetchEvent({ url: 'https://example.com/discovery2' }));
  assert.deepEqual(assurance.currentRunSourceReceipts(), []);
});

test('a mix of successes and failures records only the successes', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/ok-1' }));
  assurance.observe('web_fetch', failedFetchEvent({ url: 'https://example.com/bad-1' }));
  assurance.observe('web_search', searchEvent({ urls: ['https://example.com/lead'] }));
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/ok-2' }));
  assurance.observe('browser', browserSnapshotEvent({ url: 'https://example.com/snap' }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.deepEqual(receipts.map(r => r.url), ['https://example.com/ok-1', 'https://example.com/ok-2']);
});

test('web_fetch finalUrl is recorded alongside the requested url', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({
    url: 'https://example.com/redirect',
    finalUrl: 'https://example.com/final',
  }));
  const urls = assurance.currentRunSourceReceipts().map(r => r.url).sort();
  assert.deepEqual(urls, ['https://example.com/final', 'https://example.com/redirect']);
});

test('receipts survive a later failed observation of the same URL', () => {
  const { assurance } = freshAssurance();
  assurance.observe('web_fetch', webFetchEvent({ url: 'https://example.com/flaky' }));
  assurance.observe('web_fetch', failedFetchEvent({ url: 'https://example.com/flaky', status: 503 }));
  const receipts = assurance.currentRunSourceReceipts();
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0].url, 'https://example.com/flaky');
});
