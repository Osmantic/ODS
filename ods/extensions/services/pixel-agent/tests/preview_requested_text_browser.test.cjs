/* Real-Chromium regression for the preview inspector's requested-text check.
 * Test-only fixture pages, never a Pixel artifact.
 * Run: node --test preview_requested_text_browser.test.cjs
 * Requires Playwright + Chromium; optional PIXEL_TEST_CHROMIUM_EXECUTABLE.
 * The in-page functions, timings, CSP, sandbox and wrapper page are read from
 * the production capsule source (host/preview_inspection_capsule.py), and
 * checkRequestedText below follows its check_requested_text step for step.
 * Servers bind loopback ephemeral ports, never ODS's live service ports.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const read = file => fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
const host = path.resolve(__dirname, '../host');
const capsule = read(path.join(host, 'preview_inspection_capsule.py'));
const protocol = read(path.join(host, 'preview_inspection_protocol.py'));
function inPage(name) {
  const source = capsule.match(new RegExp(`^${name} = r"""([\\s\\S]*?)"""$`, 'm'))?.[1];
  assert.ok(source, `missing capsule function ${name}`);
  return source;
}
function constant(source, name) {
  const value = source.match(new RegExp(`^${name} = (.+)$`, 'm'))?.[1];
  assert.ok(value, `missing capsule constant ${name}`);
  return JSON.parse(value);
}
const REQUESTED_TEXT = inPage('REQUESTED_TEXT'), SCROLL_TO = inPage('SCROLL_TO');
const REVEAL_TEXT = inPage('REVEAL_TEXT'), TEXT_ANIMATIONS = inPage('TEXT_ANIMATIONS');
const T = Object.fromEntries(['TEXT_SETTLE_MS', 'TEXT_TIMEOUT_MS', 'TEXT_SCROLL_STEPS', 'TEXT_SCROLL_WAIT_MS',
  'TEXT_FINAL_WAIT_MS', 'TEXT_MIN_OBSERVE_MS', 'TEXT_ANIMATION_WAIT_MS', 'TEXT_SCROLL_BUDGET_S'].map(name => [name, constant(capsule, name)]));
const VIEWPORT = constant(capsule, 'TEXT_VIEWPORT');
const STATUSES = JSON.parse(capsule.match(/^REQUESTED_TEXT_STATUSES = \((.+)\)$/m)[1].replace(/^/, '[').replace(/$/, ']'));
const REASONS = JSON.parse('[' + capsule.match(/^REQUESTED_TEXT_REASONS = \(\n([\s\S]+?),?\n\)/m)[1] + ']');
const CSP = protocol.match(/^CSP = \(\n([\s\S]+?)\n\)/m)[1].split('\n').map(line => JSON.parse(line.trim())).join('');
const SANDBOX = constant(protocol, 'SANDBOX');
const PREFIX = '/site-aaaaaaaaaaaaaaaaaaaaaaaa/';
const wrapperParts = capsule.match(/def wrapper_document\(prefix\):[\s\S]*?return \(\n\s+f"(.+)"\n\s+f'(.+)'\n/);
assert.ok(wrapperParts, 'use the actual capsule wrapper page');
const WRAPPER = (wrapperParts[1] + wrapperParts[2]).replaceAll('{{', '{').replaceAll('}}', '}')
  .replace('{SANDBOX}', SANDBOX).replace('{prefix}', PREFIX);
const ROUND_087 = read(path.resolve(__dirname, '../../../../tests/fixtures/preview-requested-text/tower1-r087/index.html'));

let current = '';
async function serve() {
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://fixture').pathname;
    const body = pathname === '/__ods_inspection__.html' ? WRAPPER
      : pathname === PREFIX || pathname === PREFIX + 'index.html' ? current : undefined;
    if (body === undefined) { response.writeHead(404); response.end(); return; }
    response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP,
      'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Resource-Policy': 'cross-origin', 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store'});
    response.end(body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
}

// check_requested_text, minus the request guard (fixtures load nothing else).
async function checkRequestedText(browser, origin, html, texts) {
  current = html;
  const context = await browser.newContext({viewport: VIEWPORT, serviceWorkers: 'block', acceptDownloads: false});
  const started = performance.now();
  try {
    const page = await context.newPage();
    page.setDefaultTimeout(T.TEXT_TIMEOUT_MS);
    await page.goto(origin + '/__ods_inspection__.html', {waitUntil: 'load', timeout: T.TEXT_TIMEOUT_MS});
    const loaded = performance.now();
    assert.equal(page.frame({name: 'inspection'})?.url(), origin + PREFIX);
    const cdp = await context.newCDPSession(page);
    const tree = (await cdp.send('Page.getFrameTree')).frameTree;
    const frameId = tree.childFrames.find(child => child.frame.name === 'inspection').frame.id;
    const world = (await cdp.send('Page.createIsolatedWorld',
      {frameId, worldName: 'ods-requested-text', grantUniveralAccess: false})).executionContextId;
    const call = async (functionDeclaration, value) => {
      const result = await cdp.send('Runtime.callFunctionOn',
        {executionContextId: world, functionDeclaration, arguments: [{value}], returnByValue: true});
      assert.equal(result.exceptionDetails, undefined, JSON.stringify(result.exceptionDetails));
      return result.result.value;
    };
    const entry = (text, observed) => {
      assert.ok(STATUSES.includes(observed?.status), JSON.stringify(observed));
      if (observed.status !== 'hidden') return {text, status: observed.status};
      assert.ok(REASONS.includes(observed.reason), JSON.stringify(observed));
      return {text, ...observed};
    };
    await page.waitForTimeout(T.TEXT_SETTLE_MS);
    const first = await call(REQUESTED_TEXT, texts);
    const outcome = texts.map((text, i) => entry(text, first.results[i]));
    let pending = outcome.flatMap((value, i) => value.status === 'hidden' ? [i] : []);
    const sample = async () => {
      const observed = (await call(REQUESTED_TEXT, pending.map(i => texts[i]))).results;
      pending.forEach((i, k) => {
        const value = entry(texts[i], observed[k]);
        if (['visible', 'hidden'].includes(value.status)) outcome[i] = value;
      });
      pending = pending.filter(i => outcome[i].status === 'hidden');
    };
    let animationBudget = T.TEXT_ANIMATION_WAIT_MS;
    const finishAnimations = async waiting => {
      const remaining = await call(TEXT_ANIMATIONS, waiting.map(i => texts[i]));
      assert.ok(Number.isFinite(remaining) && remaining >= 0 && remaining <= 60000, String(remaining));
      const wait = Math.trunc(Math.min(remaining, animationBudget));
      if (wait > 0) {
        animationBudget -= wait;
        await page.waitForTimeout(wait);
        await sample();
      }
    };
    const scrolled = pending.length > 0;
    if (pending.length) {
      const deadline = performance.now() + T.TEXT_SCROLL_BUDGET_S * 1000;
      let moved = false;
      for (const i of [...pending]) {
        if (!pending.includes(i)) continue;
        if (performance.now() > deadline) break;
        const revealed = await call(REVEAL_TEXT, texts[i]);
        assert.equal(typeof revealed, 'boolean');
        if (revealed) {
          moved = true;
          await page.waitForTimeout(T.TEXT_SCROLL_WAIT_MS);
        }
        await sample();
        if (pending.includes(i)) await finishAnimations([i]);
      }
      const maximum = Math.trunc(first.maxScroll), height = Math.trunc(first.viewport);
      const step = Math.max(height * 0.8, maximum / T.TEXT_SCROLL_STEPS, 1);
      const tops = [];
      for (let top = 0; top < maximum && tops.length < T.TEXT_SCROLL_STEPS;) {
        top = Math.min(maximum, top + step);
        tops.push(Math.round(top));
      }
      for (const top of tops) {
        if (!pending.length || performance.now() > deadline) break;
        await call(SCROLL_TO, top);
        moved = true;
        await page.waitForTimeout(T.TEXT_SCROLL_WAIT_MS);
        await sample();
      }
      if (pending.length) {
        const elapsed = performance.now() - loaded;
        await page.waitForTimeout(Math.max(T.TEXT_FINAL_WAIT_MS, Math.round(T.TEXT_MIN_OBSERVE_MS - elapsed)));
        await sample();
      }
      if (pending.length) await finishAnimations(pending);
      if (pending.length && moved) {
        await call(SCROLL_TO, 0);
        await page.waitForTimeout(T.TEXT_SCROLL_WAIT_MS);
        await sample();
      }
    }
    return {scrolled, texts: outcome, ms: performance.now() - started};
  } finally {
    await context.close();
  }
}

const page = body => `<!doctype html><html><head><meta charset="utf-8"></head><body>${body}</body></html>`;
const observer = (options = '{threshold:.2}') => '<script>const io=new IntersectionObserver(es=>es.forEach(e=>{' +
  "if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target)}}),"+ options +
  ");document.querySelectorAll('.reveal').forEach(el=>io.observe(el));</script>";
const REVEAL = '<style>.reveal{opacity:0;transform:translateY(40px);transition:opacity .6s,transform .6s}' +
  '.reveal.in{opacity:1;transform:none}</style>';
const entrance = delay => page('<style>@keyframes up{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:none}}' +
  `.a{opacity:0;animation:up .8s ease ${delay}s forwards}</style><section style="min-height:80vh;display:grid;` +
  'place-items:center"><div><h1 class="a">Night Garden FLEET-1</h1></div></section>');
const preloader = extra => page('<style>body{opacity:0;transition:opacity .5s}body.ready{opacity:1}</style>' +
  `<h1>Night Garden</h1>${extra}<script>addEventListener('load',()=>setTimeout(()=>document.body.classList.add('ready'),600))</script>`);

// [label, html, {text: expected}] with expected 'visible' or a hidden reason.
const VISIBLE_CASES = [
  ['fade-in on scroll', page(`${REVEAL}<div style="height:2400px">Intro</div><section class="reveal"><h2>Our story</h2></section>${observer()}`),
    {'Our story': 'visible'}],
  ['normal text below the fold', page('<div style="height:2400px">Intro</div><footer>Bottom line</footer>'), {'Bottom line': 'visible'}],
  ['fade-in on load', page('<style>@keyframes f{from{opacity:0}to{opacity:1}}h1{animation:f .8s ease both}</style><h1>Painted</h1>'),
    {Painted: 'visible'}],
  // Review B3: the page scrolls inside a container, never the window.
  ['reveal inside a full-height scrolling main', page('<style>html,body{height:100%;margin:0;overflow:hidden}' +
    `main{height:100%;overflow-y:auto}</style>${REVEAL}<main><div style="height:2400px">Intro</div>` +
    `<section class="reveal"><h2>Our story</h2></section></main>${observer()}`), {'Our story': 'visible'}],
  ['reveal inside a scroll-snap deck', page('<style>html,body{height:100%;margin:0}.snap{height:100vh;overflow-y:scroll;' +
    `scroll-snap-type:y mandatory}.snap>section{height:100vh;scroll-snap-align:start}</style>${REVEAL}<div class="snap">` +
    `<section><h1>Hero</h1></section><section><h2 class="reveal">Our story</h2></section><section>Three</section></div>${observer()}`),
    {'Our story': 'visible'}],
  ['reveal inside a scrolling body', page(`<style>html{overflow:hidden;height:100%}body{height:100%;overflow:auto;margin:0}</style>` +
    `${REVEAL}<div style="height:2400px"></div><section class="reveal"><p>Our story</p></section>${observer()}`), {'Our story': 'visible'}],
  // Review non-blocking: page steps longer than the view skip a small band.
  ['small reveal between the steps of a very long page', page(`${REVEAL}<div style="height:850px">a</div>` +
    `<p class="reveal" style="margin:0;height:30px">Our story</p><div style="height:13000px">b</div>${observer('{threshold:1}')}`),
    {'Our story': 'visible'}],
  ['reveal with a transition delay', page('<style>.reveal{opacity:0;transition:opacity .8s ease .6s}.reveal.in{opacity:1}</style>' +
    `<div style="height:2400px">Intro</div><footer class="reveal"><p>Our story</p></footer>${observer()}`), {'Our story': 'visible'}],
  ['reveal from a scroll handler', page('<style>.r{opacity:0;transition:opacity .4s}.r.in{opacity:1}</style>' +
    "<div style=\"height:3000px\"></div><p class=\"r\">Our story</p><script>addEventListener('scroll',()=>{" +
    "document.querySelectorAll('.r').forEach(e=>{if(e.getBoundingClientRect().top<innerHeight)e.classList.add('in')})})</script>"),
    {'Our story': 'visible'}],
  // Review B2: delayed entrances on a page that fits one screen.
  ...[0.5, 0.8, 1.2].map(delay => [`one-screen entrance delayed ${delay}s`, entrance(delay), {'Night Garden FLEET-1': 'visible'}]),
  ['staggered one-screen hero', page('<style>@keyframes up{from{opacity:0}to{opacity:1}}.s{opacity:0;animation:up .6s ease forwards}' +
    '.s1{animation-delay:.1s}.s2{animation-delay:.3s}.s3{animation-delay:.6s}.s4{animation-delay:.9s}</style><header>' +
    '<h1 class="s s1">Night Garden</h1><p class="s s2">Tagline</p><a class="s s3" href="#">Book a table</a>' +
    '<small class="s s4">Open daily</small></header>'), {'Night Garden': 'visible', 'Book a table': 'visible', 'Open daily': 'visible'}],
  ['preloader on a one-screen page', preloader(''), {'Night Garden': 'visible'}],
  ['preloader on a long page', preloader('<div style="height:4000px"></div>'), {'Night Garden': 'visible'}],
  ['white text on a dark inset shadow', page('<div style="box-shadow:inset 0 0 0 1000px #222;padding:40px;color:#fff">' +
    '<h1>Painted</h1></div>'), {Painted: 'visible'}],
];

const HIDDEN_CASES = [
  ['permanent opacity 0', page('<style>.ghost{opacity:0}</style><footer class="ghost"><p>Always faded</p></footer>'),
    {'Always faded': 'transparent'}],
  ['display:none ancestor', page('<div id="later" style="display:none"><p>Inside hidden</p></div>'), {'Inside hidden': 'display-none'}],
  ['white on white', page('<style>body{background:#fff}footer{color:#fff}</style><main>Hi</main><footer>Ghost footer</footer>'),
    {'Ghost footer': 'same-color'}],
  ['a reveal that never fires', page(`${REVEAL}<div style="height:2400px"></div><section class="reveal"><p>Never shown</p></section>`),
    {'Never shown': 'transparent'}],
  ['an infinite animation is not waited for', page('<style>@keyframes spin{to{transform:rotate(1turn)}}' +
    '.x{opacity:0;animation:spin 1s linear infinite}</style><p class="x">Spinning ghost</p>'), {'Spinning ghost': 'transparent'}],
  ['an entrance delayed past the bound', entrance(6), {'Night Garden FLEET-1': 'transparent'}],
];

function assertOutcome(result, expected, label) {
  assert.deepEqual(Object.fromEntries(result.texts.map(t => [t.text, t.status === 'visible' ? 'visible' : t.reason ?? t.status])),
    expected, `${label}: ${JSON.stringify(result.texts)}`);
}

test('requested text in real Chromium: reveals, delayed entrances and scroll containers count as visible', {timeout: 240000}, async t => {
  const browser = await chromium.launch({headless: true, chromiumSandbox: true,
    ...(process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE} : {})});
  const server = await serve();
  t.after(async () => { await browser.close(); server.close(); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  for (const [label, html, expected] of VISIBLE_CASES) {
    await t.test(label, async () => {
      const result = await checkRequestedText(browser, origin, html, Object.keys(expected));
      assertOutcome(result, expected, label);
    });
  }
  await t.test('text visible right after load takes one measurement', async () => {
    const result = await checkRequestedText(browser, origin, page('<h1>Night Garden</h1><div style="height:4000px"></div>'),
      ['Night Garden']);
    assert.equal(result.scrolled, false);
    assert.ok(result.ms < T.TEXT_MIN_OBSERVE_MS, `${result.ms} ms`);
  });
});

test('requested text in real Chromium: hidden text stays reported within the bound', {timeout: 240000}, async t => {
  const browser = await chromium.launch({headless: true, chromiumSandbox: true,
    ...(process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE ? {executablePath: process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE} : {})});
  const server = await serve();
  t.after(async () => { await browser.close(); server.close(); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  // Settle, the scroll pass with one step started at its end, the final
  // wait, animation waits and the step back to the top; plus the page load.
  const bound = T.TEXT_TIMEOUT_MS + T.TEXT_SETTLE_MS + T.TEXT_SCROLL_BUDGET_S * 1000 + T.TEXT_SCROLL_WAIT_MS +
    T.TEXT_FINAL_WAIT_MS + T.TEXT_ANIMATION_WAIT_MS + T.TEXT_SCROLL_WAIT_MS + 3000;
  for (const [label, html, expected] of HIDDEN_CASES) {
    await t.test(label, async () => {
      const result = await checkRequestedText(browser, origin, html, Object.keys(expected));
      assertOutcome(result, expected, label);
      assert.ok(result.ms < bound, `${label}: ${result.ms} ms`);
    });
  }
  await t.test('round 087: the footer inside the hidden sold-out section', async () => {
    const result = await checkRequestedText(browser, origin, ROUND_087,
      ['FLEET-eaa39f42e9 edited successfully', 'Night Garden FLEET-eaa39f42e9 Revised']);
    assert.deepEqual(result.texts, [
      {text: 'FLEET-eaa39f42e9 edited successfully', status: 'hidden', element: 'p', reason: 'display-none',
        culprit: 'div#soldOutSection'},
      {text: 'Night Garden FLEET-eaa39f42e9 Revised', status: 'visible'}]);
  });
});
