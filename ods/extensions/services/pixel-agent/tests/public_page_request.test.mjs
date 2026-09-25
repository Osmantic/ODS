// The public page request shared by pixel_ods_web_extract and host citation
// verification: browser-compatible navigation headers, raw-text removal before
// extraction, bot-challenge refusal, and the unchanged SSRF/size/time guards.
import test from 'node:test';
import assert from 'node:assert/strict';
import {botChallenge, chromeMajorVersion, createPublicPageReader, createPublicWebExtractTool,
  PUBLIC_PAGE_PRODUCT_TOKEN, publicPageRequestHeaders, readableHtml} from '../plugin/web-extract.mjs';
import {createHostCitationVerifier} from '../plugin/citation-verification.mjs';
import {createCompletionAssurance} from '../plugin/completion-assurance.mjs';

const DAY = 24 * 60 * 60 * 1000;
const ANCHOR = Date.UTC(2026, 8, 1);
const JOURNEY = 'https://www.xfinitymobilearena.com/events/detail/journey-10-28-26';
// Shaped like the live page's markup: navigation, a heading, the date block.
const JOURNEY_PAGE = '<!doctype html><html><head><title>Journey | Xfinity Mobile Arena</title>' +
  '<script>window.dataLayer=[];if(a<b){document.write("<div hidden>")}</script></head><body>' +
  '<nav>Events &amp; Tickets All Events Philadelphia Flyers</nav><h1>Journey</h1>' +
  '<div class="date">Date Oct 28, 2026</div><div>Event Starts 7:30 PM</div><div>On Sale Now</div></body></html>';

const headerOf = (init, name) => Object.entries(init?.headers ?? {})
  .find(([key]) => key.toLowerCase() === name.toLowerCase())?.[1];

// The refusal rules measured on 2026-09-25 (tower2), replayed offline. Arena:
// a browser User-Agent with CORS-mode fetch metadata (undici's default) is Not
// Acceptable, a navigation is served; in its intermittent strict windows any
// browser User-Agent is Not Acceptable and only the plain agent is served.
// Sports: a Chrome older than 130 gets an AWS WAF challenge. Retailer: an agent
// without a browser token gets HTTP 503.
const RULES = {
  arena: ({agent, mode}) => /Chrome\//.test(agent) && mode !== 'navigate' ? {status: 406} : undefined,
  arenaStrict: ({agent}) => /Chrome\//.test(agent) ? {status: 406} : undefined,
  sports: ({agent}) => Number(agent.match(/Chrome\/(\d+)/)?.[1] ?? 999) < 130
    ? {status: 202, headers: {'x-amzn-waf-action': 'challenge'}} : undefined,
  retailer: ({agent}) => /Mozilla\/5\.0 \(/.test(agent) ? undefined : {status: 503},
};
function measuredWaf(rule, {calls = [], page = JOURNEY_PAGE} = {}) {
  return async options => {
    calls.push(options);
    const refused = RULES[rule]({agent: headerOf(options.init, 'User-Agent') ?? 'undici',
      mode: headerOf(options.init, 'Sec-Fetch-Mode') ?? 'cors'});
    return {
      response: new Response(refused ? '' : page, {status: refused?.status ?? 200,
        headers: {'Content-Type': 'text/html; charset=UTF-8', ...refused?.headers}}),
      finalUrl: options.url,
      release: () => {},
    };
  };
}
const WEB_FETCH_HEADERS = {Accept: 'text/markdown, text/html;q=0.9, */*;q=0.1', 'Accept-Language': 'en-US,en;q=0.9',
  'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'};
const OLD_EXTRACT_HEADERS = {Accept: 'text/markdown, text/html;q=0.9, text/plain;q=0.8, application/json;q=0.7',
  'Accept-Language': 'en-US,en;q=0.9'};
const plainText = async ({html}) => ({text: html.replace(/<[^>]+>/g, '\n').replace(/&amp;/g, '&')});
const wholeBody = async response => ({text: await response.text(), truncated: false});

function harness({status = 200, body = '', headers = {'Content-Type': 'text/html; charset=utf-8'}, finalUrl,
  fetchError, truncated = false, extract} = {}) {
  const calls = [], reads = [], extracted = [];
  let releases = 0;
  const deps = {
    guardedFetch: async options => {
      calls.push(options);
      if (fetchError) throw fetchError;
      return {response: new Response(body, {status, headers}), finalUrl: finalUrl ?? options.url,
        release: () => { releases += 1; }};
    },
    readResponseText: async (response, options) => {
      reads.push(options.maxBytes);
      return {text: await response.text(), truncated};
    },
    extractBasicHtmlContent: async ({html}) => {
      extracted.push(html);
      return extract ? extract(html) : {text: html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()};
    },
  };
  return {calls, reads, extracted, releases: () => releases, deps,
    readPage: createPublicPageReader(deps), tool: createPublicWebExtractTool(deps)};
}

test('requests are one browser-compatible document navigation that stays identifiable', async () => {
  const h = harness({body: '<title>Docs</title><p>Path.exists returns a boolean.</p>'});
  const page = await h.readPage('https://docs.example.org/reference');
  assert.equal(page.ok, true);
  assert.equal(h.calls.length, 1);
  const [{init, useEnvProxy, maxRedirects}] = h.calls;
  assert.equal(useEnvProxy, false);
  assert.equal(maxRedirects, 3);
  assert.equal(headerOf(init, 'Sec-Fetch-Mode'), 'navigate');
  assert.equal(headerOf(init, 'Sec-Fetch-Dest'), 'document');
  assert.equal(headerOf(init, 'Sec-Fetch-Site'), 'none');
  assert.equal(headerOf(init, 'Upgrade-Insecure-Requests'), '1');
  assert.match(headerOf(init, 'Accept'), /^text\/markdown, text\/html;q=0\.9/);
  assert.match(headerOf(init, 'Accept-Language'), /^en-US/);
  const agent = headerOf(init, 'User-Agent');
  assert.match(agent, /^Mozilla\/5\.0 \(X11; Linux x86_64\) AppleWebKit\/537\.36 \(KHTML, like Gecko\) Chrome\/\d{3}\.0\.0\.0 Safari\/537\.36 /);
  assert.ok(agent.endsWith(` ${PUBLIC_PAGE_PRODUCT_TOKEN}`));
  assert.doesNotMatch(agent, /undici|Headless/i);
  // No cookies, credentials or conditional state are ever sent.
  for (const name of ['Cookie', 'Authorization', 'If-None-Match', 'Referer']) assert.equal(headerOf(init, name), undefined);
});

test('the Chrome version follows the stable cadence and is bounded against a wrong clock', () => {
  assert.equal(chromeMajorVersion(ANCHOR), 151);
  assert.equal(chromeMajorVersion(ANCHOR - 400 * DAY), 151);
  assert.equal(chromeMajorVersion(ANCHOR + 27 * DAY), 151);
  assert.equal(chromeMajorVersion(ANCHOR + 28 * DAY), 152);
  assert.equal(chromeMajorVersion(ANCHOR + 365 * DAY), 164);
  assert.equal(chromeMajorVersion(ANCHOR + 50 * 365 * DAY), 177);
  assert.equal(chromeMajorVersion(Number.NaN), 151);
  assert.match(publicPageRequestHeaders(ANCHOR + 56 * DAY)['User-Agent'], /Chrome\/153\.0\.0\.0/);
  const h = harness({body: 'text'});
  const reader = createPublicPageReader({...h.deps, now: () => ANCHOR + 28 * DAY});
  return reader('https://docs.example.org/').then(() =>
    assert.match(headerOf(h.calls[0].init, 'User-Agent'), /Chrome\/152\.0\.0\.0/));
});

test('replay: each measured refusal of the old requests is read once by the new one', async () => {
  const status = async (rule, headers) => (await measuredWaf(rule)({url: JOURNEY, init: {headers}})).response.status;
  const rules = ['arena', 'arenaStrict', 'sports', 'retailer'];
  const statuses = async headers => Promise.all(rules.map(rule => status(rule, headers)));
  // Before: web_fetch fails both arena rules and the sports rule; the old
  // extractor fails the retailer rule.
  assert.deepEqual(await statuses(WEB_FETCH_HEADERS), [406, 406, 202, 200]);
  assert.deepEqual(await statuses(OLD_EXTRACT_HEADERS), [200, 200, 200, 503]);
  // After: every rule is read; only the strict arena window uses the one
  // plain fallback request.
  for (const rule of rules) {
    const calls = [];
    const tool = createPublicWebExtractTool({guardedFetch: measuredWaf(rule, {calls}),
      readResponseText: wholeBody, extractBasicHtmlContent: plainText});
    const result = await tool.execute('replay-' + rule, {url: JOURNEY});
    assert.equal(calls.length, rule === 'arenaStrict' ? 2 : 1, rule);
    assert.equal(result.isError, undefined, rule);
    assert.equal(result.details.source_url, JOURNEY, rule);
    if (calls.length === 2) {
      assert.deepEqual(calls[1].init.headers, OLD_EXTRACT_HEADERS);
      assert.equal(calls[1].url, JOURNEY);
    }
  }
});

test('the plain fallback follows only a plain 403 or 406, once, and never a challenge', async () => {
  const run = async (responses, {signal, finalUrls = []} = {}) => {
    const calls = [], released = [];
    const reader = createPublicPageReader({
      guardedFetch: async options => {
        const index = calls.push(options) - 1;
        const [status, headers = {}, body = ''] = responses[Math.min(index, responses.length - 1)];
        return {response: new Response(status === 200 ? '<title>Journey</title><p>Oct 28, 2026</p>' : body,
          {status, headers: {'Content-Type': 'text/html', ...headers}}),
        finalUrl: finalUrls[index] ?? options.url, release: () => released.push(index)};
      },
      readResponseText: wholeBody, extractBasicHtmlContent: plainText});
    const page = await reader(JOURNEY, {signal});
    return {page, calls, released};
  };
  for (const status of [406, 403]) {
    const {page, calls, released} = await run([[status], [200]]);
    assert.equal(page.ok, true);
    assert.equal(page.requests, 2);
    assert.equal(calls.length, 2);
    assert.equal(headerOf(calls[0].init, 'Sec-Fetch-Mode'), 'navigate');
    assert.deepEqual(calls[1].init.headers, OLD_EXTRACT_HEADERS);
    assert.deepEqual(released, [0, 1], 'the refused response is released before the fallback');
  }
  // Refused again: reported as the fallback's status, with no third request.
  const twice = await run([[406], [403]]);
  assert.deepEqual([twice.page.ok, twice.page.status, twice.page.requests, twice.calls.length], [false, 403, 2, 2]);
  // Challenges, rate limits, server errors and other statuses are not retried.
  for (const response of [[403, {'cf-mitigated': 'challenge'}], [403, {}, '<title>Attention Required! | Cloudflare</title>'],
    [406, {}, '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'],
    [429, {'Retry-After': '60'}], [503], [401], [404], [451], [202, {'x-amzn-waf-action': 'captcha'}]]) {
    const {page, calls, released} = await run([response, [200]]);
    assert.equal(page.ok, false, String(response[0]));
    assert.equal(calls.length, 1, `HTTP ${response[0]} is not retried`);
    assert.equal(page.requests ?? 1, 1);
    assert.deepEqual(released, [0]);
  }
  // A caller that has already given up gets no second request.
  const controller = new AbortController();
  controller.abort();
  const aborted = await run([[406], [200]], {signal: controller.signal});
  assert.equal(aborted.calls.length, 1);
  assert.equal(aborted.page.ok, false);
  // The fallback is guarded like the first request: a private final address is not read.
  const redirected = await run([[406], [200]], {finalUrls: [JOURNEY, 'http://169.254.169.254/latest/meta-data/']});
  assert.deepEqual(redirected.page, {ok: false, reason: 'blocked'});
  assert.deepEqual(redirected.released, [0, 1]);
});

for (const rule of ['arena', 'arenaStrict']) test(`replay: the arena page that web_fetch got as HTTP 406 yields a read receipt and host verification (${rule})`, async () => {
  const calls = [];
  const tool = createPublicWebExtractTool({guardedFetch: measuredWaf(rule, {calls}),
    readResponseText: wholeBody, extractBasicHtmlContent: plainText});
  const params = {url: JOURNEY};
  const result = await tool.execute('replay-406', params);
  assert.equal(calls.length, rule === 'arenaStrict' ? 2 : 1);
  assert.equal(result.isError, undefined);
  assert.equal(result.details.boundary, 'public-web-read-only');
  assert.equal(result.details.mode, 'overview');
  assert.equal(result.details.source_url, JOURNEY);
  assert.match(result.content[0].text, /Journey[\s\S]*Oct 28, 2026/);
  assert.doesNotMatch(result.content[0].text, /dataLayer/);

  // The completion check counts it as a current-run read of the cited page.
  const assurance = createCompletionAssurance();
  assurance.begin('Search the live web for Philadelphia events. Actually search and open sources and give a direct official source URL.');
  assurance.observe('pixel_ods_web_extract', {params, result});
  assert.deepEqual(assurance.readPages, [{url: JOURNEY}]);
  const answer = `## Journey\n- **Date:** Wednesday, October 28, 2026 at 7:30 PM\n- **Venue:** Xfinity Mobile Arena\n` +
    `- **Official Source:** ${JOURNEY}`;
  assert.deepEqual(assurance.unverifiedCitations(answer), []);

  // The host citation check reads through the same request and verifies it.
  const verifier = createHostCitationVerifier({readPage: createPublicPageReader({guardedFetch: measuredWaf(rule),
    readResponseText: wholeBody, extractBasicHtmlContent: plainText})});
  const verified = await verifier.verify({answer, urls: [JOURNEY]});
  assert.deepEqual(verified.verified, [{url: JOURNEY, finalUrl: JOURNEY}]);
});

test('raw-text elements and comments are removed before extraction', async () => {
  const html = '<head><title>Events</title><script>if (a < b) { x = "<div hidden><p>"; }</script>' +
    '<STYLE>p{color:red}</STYLE><!-- <p>commented out</p> --><noscript>Enable JavaScript</noscript>' +
    '<template><p>template only</p></template><script type="application/ld+json">{"name":"x"}</script></head>' +
    '<body><scripted-card>Oct 3</scripted-card><p>October 3: Philly Music Fest</p></body>';
  const cleaned = readableHtml(html);
  assert.match(cleaned, /<title>Events<\/title>/);
  assert.match(cleaned, /<scripted-card>Oct 3<\/scripted-card>/);
  assert.match(cleaned, /October 3: Philly Music Fest/);
  for (const hidden of [/a < b/, /color:red/, /commented out/, /Enable JavaScript/, /template only/, /"name"/]) {
    assert.doesNotMatch(cleaned, hidden);
  }
  // Unterminated raw text runs to the end of the document, as in a browser.
  assert.equal(readableHtml('<p>kept</p><script>never closed <p>lost</p>').trim(), '<p>kept</p>');
  assert.equal(readableHtml('<p>kept</p><!-- open comment <p>lost</p>').trim(), '<p>kept</p>');
  assert.equal(readableHtml(''), '');
  assert.equal(readableHtml(undefined), '');

  const h = harness({body: html});
  const page = await h.readPage('https://www.visitphilly.com/articles/philadelphia/upcoming-concerts');
  assert.equal(page.ok, true);
  assert.equal(h.extracted.length, 1);
  assert.doesNotMatch(h.extracted[0], /a < b|commented out/);
  assert.match(page.text, /October 3: Philly Music Fest/);
});

test('raw-text removal stays linear on hostile markup', () => {
  for (const hostile of ['<script>'.repeat(120_000), '<!--'.repeat(200_000), '</script'.repeat(100_000),
    '<script '.repeat(100_000), `<style>${'</styl'.repeat(100_000)}`]) {
    const started = performance.now();
    readableHtml(hostile);
    assert.ok(performance.now() - started < 1_000, `took ${performance.now() - started} ms`);
  }
});

for (const [label, response] of [
  ['Cloudflare challenge header on HTTP 403', {status: 403, headers: {'Content-Type': 'text/html', 'cf-mitigated': 'challenge'},
    body: '<title>Just a moment...</title>'}],
  ['Cloudflare block page on HTTP 403', {status: 403, headers: {'Content-Type': 'text/html'},
    body: '<title>Attention Required! | Cloudflare</title><p>Sorry, you have been blocked</p>'}],
  ['Cloudflare interstitial markup on HTTP 503', {status: 503, headers: {'Content-Type': 'text/html'},
    body: '<title>Checking</title><script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>'}],
  ['AWS WAF challenge on HTTP 202', {status: 202, headers: {'Content-Type': 'text/html', 'x-amzn-waf-action': 'challenge'}, body: ''}],
  ['identity check on HTTP 401', {status: 401, headers: {'Content-Type': 'application/json'}, body: '{"response":"identify"}'}],
  ['interstitial served with HTTP 200', {status: 200, headers: {'Content-Type': 'text/html'},
    body: '<title>Just a moment...</title><p>Verifying you are human. This may take a few seconds.</p>'}],
  ['Imperva resource page served with HTTP 200', {status: 200, headers: {'Content-Type': 'text/html'},
    body: '<html><head><meta name="robots" content="noindex"><script src="/_Incapsula_Resource?SWJIYLWA=5074a744"></script></head><body></body></html>'}],
]) {
  test(`a bot challenge is a failure, never evidence: ${label}`, async () => {
    const h = harness(response);
    const result = await h.tool.execute('challenge', {url: 'https://www.techspot.com/specs/gpu/305060-nvidia-geforce-rtx-5070.html'});
    assert.equal(result.isError, true);
    assert.equal(result.details.challenge, true);
    assert.equal(result.details.matched, false);
    assert.equal(result.details.status, response.status);
    assert.match(result.content[0].text, /bot-protection challenge or block[\s\S]*does not solve or bypass[\s\S]*Do not retry/);
    assert.doesNotMatch(result.content[0].text, /EXTERNAL_UNTRUSTED_CONTENT|Verifying you are human|Sorry, you have been blocked/);
    assert.equal(h.calls.length, 1, 'no retry');
    assert.equal(h.releases(), 1);
    // A refusal body is read only within a small bound.
    assert.ok(h.reads.every(maxBytes => maxBytes <= (response.status >= 300 || response.status < 200 ? 65_536 : 1_000_000)));

    const verifier = createHostCitationVerifier({readPage: harness(response).readPage});
    const url = 'https://www.techspot.com/specs/gpu/305060-nvidia-geforce-rtx-5070.html';
    const outcome = await verifier.verify({answer: `- **RTX 5070 Specs** — released March 5, 2025 (${url})`, urls: [url]});
    assert.deepEqual(outcome.verified, []);
    assert.equal(outcome.results[0].verified, false);
  });
}

test('ordinary pages behind bot-protection services are still read', async () => {
  // Cloudflare's JavaScript detections are injected into normal pages; a page
  // may also discuss CAPTCHAs or be titled like a refusal while carrying content.
  const article = '<title>How CAPTCHA and bot challenges work | Example Tech</title>' +
    '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>' +
    `<p>${'A long article about verifying you are human and captcha design. '.repeat(60)}</p>`;
  for (const body of [article, `<title>Access Denied</title><p>${'Access Denied is a 2026 thriller released October 3. '.repeat(40)}</p>`]) {
    const h = harness({body, headers: {'Content-Type': 'text/html', 'x-datadome': 'protected', 'cf-ray': 'abc-EWR'}});
    const page = await h.readPage('https://www.example-tech.com/captcha');
    assert.equal(page.ok, true);
    assert.ok(page.text.length > 1_500);
  }
  // An ordinary error page is reported as the HTTP status, not as a challenge.
  const missing = harness({status: 404, body: '<title>Page not found</title>'});
  const result = await missing.tool.execute('missing', {url: 'https://www.example-tech.com/nothing'});
  assert.equal(result.details.status, 404);
  assert.equal(result.details.challenge, undefined);
  assert.match(result.content[0].text, /HTTP 404/);
  assert.equal(botChallenge({status: 200, html: '<title>Just a moment...</title>', text: 'x'.repeat(5_000)}), false);
  assert.equal(botChallenge({headers: {get: () => { throw new Error('bad headers'); }}, status: 200}), false);
});

test('a page with no server-rendered text says JavaScript may be required instead of guessing', async () => {
  const h = harness({body: '<html><body><div id="root"></div><script>render()</script></body></html>'});
  const result = await h.tool.execute('empty', {url: 'https://www.visitphilly.com/events/'});
  assert.equal(result.isError, true);
  assert.match(result.content[0].text, /no readable text[\s\S]*JavaScript, which this reader does not run/);
});

test('guards: private, loopback, link-local and metadata targets are blocked before any request', async () => {
  for (const url of ['http://169.254.169.254/latest/meta-data/', 'http://[fe80::1]/', 'http://[fd00::1]/',
    'http://10.0.0.5/', 'http://192.168.1.10/', 'http://0.0.0.0/', 'http://[::ffff:127.0.0.1]/',
    'http://router.lan.internal/', 'http://nas.local/', 'http://localhost:8080/']) {
    const h = harness({body: 'secret'});
    assert.deepEqual(await h.readPage(url), {ok: false, reason: 'invalid-url'}, url);
    const result = await h.tool.execute('private', {url});
    assert.equal(result.isError, true, url);
    assert.equal(h.calls.length, 0, url);
  }
});

test('guards: a redirect that ends on a private or local address is not read and not reflected', async () => {
  for (const finalUrl of ['http://127.0.0.1/admin', 'http://169.254.169.254/latest/meta-data/iam',
    'http://[fd00::1]/', 'http://10.1.2.3/', 'http://printer.local/']) {
    const h = harness({body: '<p>internal secret</p>', finalUrl});
    assert.deepEqual(await h.readPage('https://www.example.org/moved'), {ok: false, reason: 'blocked'});
    const result = await h.tool.execute('redirect', {url: 'https://www.example.org/moved'});
    assert.equal(result.isError, true);
    assert.doesNotMatch(JSON.stringify(result), /internal secret|127\.0\.0\.1|169\.254|fd00|10\.1\.2\.3|printer/);
    assert.equal(h.extracted.length, 0);
    assert.equal(h.releases(), 2);
  }
});

test('guards: guard denials, timeouts and aborts are reported as not read without details', async () => {
  for (const error of [Object.assign(new Error('Blocked: resolves to private/internal/special-use IP address 10.0.0.8'),
    {name: 'SsrFBlockedError'}), new Error('request timed out after 20000ms'), new DOMException('aborted', 'AbortError')]) {
    const h = harness({fetchError: error});
    assert.deepEqual(await h.readPage('https://www.example.org/'), {ok: false, reason: 'blocked'});
    const result = await h.tool.execute('denied', {url: 'https://www.example.org/'});
    assert.match(result.content[0].text, /blocked or unavailable/);
    assert.doesNotMatch(result.content[0].text, /10\.0\.0\.8|20000|aborted/);
  }
  // The caller's time bound and signal reach the guarded request unchanged.
  const h = harness({body: 'ok'});
  const controller = new AbortController();
  await h.readPage('https://www.example.org/', {signal: controller.signal, timeoutSeconds: 4});
  assert.equal(h.calls[0].signal, controller.signal);
  assert.equal(h.calls[0].timeoutSeconds, 4);
});

test('guards: oversized pages are bounded and marked truncated', async () => {
  const h = harness({body: `<p>${'x'.repeat(2_000)}</p>`, truncated: true});
  const page = await h.readPage('https://www.example.org/huge');
  assert.equal(page.ok, true);
  assert.equal(page.truncated, true);
  assert.deepEqual(h.reads, [1_000_000]);
  const result = await h.tool.execute('huge', {url: 'https://www.example.org/huge'});
  assert.equal(result.details.response_truncated, true);
  assert.ok(result.content[0].text.length < 6_500);
});
