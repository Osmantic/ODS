// HTML-to-text extraction for public pages, off the gateway's event loop.
//
// OpenClaw's extractBasicHtmlContent (web-fetch-utils) runs lazy `[\s\S]*?`
// regexes over the whole response. On markup without closing tags the cost is
// quadratic: measured on tower2 with the pinned 2026.6.33 extractor, 1 MB of
// unclosed <li> took 10.2 s, unclosed <script> 12.5 s, and 256 KB of either
// 0.7-0.8 s (the laptop is about 2.5x slower). `</li>` is optional in valid
// HTML, so ordinary pages can trigger this, and the regex is synchronous: no
// deadline or AbortSignal can interrupt it on the gateway thread, and every
// Pixel session stalls meanwhile. pixel_ods_search_read opens pages chosen by
// search results, not by the model, which makes the exposure larger.
//
// Each extraction therefore runs in its own short-lived worker thread that
// loads the very function the plugin imported (verified by its source text),
// and is terminated at a deadline or when the caller aborts. Measured on
// tower2: about 25 ms per page including worker start, 7 ms to terminate a
// worker mid-regex, and the gateway thread keeps its 10 ms timer cadence.
// Without a usable worker the extraction runs in-process on a bounded prefix
// of the HTML instead, which bounds the stall (about 0.8 s at 256 KB).

import {Worker} from 'node:worker_threads';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

export const HTML_EXTRACTION_LIMITS = Object.freeze({
  timeoutMs: 5000,            // one page's extraction in its worker
  inProcessMaxChars: 262_144, // fallback input bound without a worker
  workerHeapMb: 192,
});

export const HTML_EXTRACTION_TIMEOUT = 'ODS_HTML_EXTRACTION_TIMEOUT';
export const HTML_EXTRACTION_ABORTED = 'ODS_HTML_EXTRACTION_ABORTED';
const failure = (code) => Object.assign(new Error(code), {code});

// The first `limit` characters, never ending inside a surrogate pair.
export function boundedHtml(html, limit = HTML_EXTRACTION_LIMITS.inProcessMaxChars) {
  if (typeof html !== 'string' || html.length <= limit) return typeof html === 'string' ? html : '';
  let end = limit;
  const code = html.charCodeAt(end - 1);
  if (code >= 0xD800 && code <= 0xDBFF) end -= 1;
  return html.slice(0, end);
}

// The module that defines the SDK's extractBasicHtmlContent, read from the
// SDK entry's own import statement: `import { t as extractBasicHtmlContent }
// from "../web-fetch-utils-<hash>.js"`. Undefined when the entry has another
// shape; the caller then extracts in-process.
export function locateSdkExtractor(sdkModuleUrl, readText = url => readFileSync(fileURLToPath(url), 'utf8')) {
  if (typeof sdkModuleUrl !== 'string' || !sdkModuleUrl.startsWith('file:')) return undefined;
  const text = readText(sdkModuleUrl);
  if (typeof text !== 'string' || text.length > 4_000_000) return undefined;
  for (const match of text.matchAll(/import\s*\{([^}]{1,4000})\}\s*from\s*["'](\.{1,2}\/[^"'\s]{1,200}\.m?js)["']/g)) {
    for (const specifier of match[1].split(',')) {
      const parts = specifier.trim().split(/\s+as\s+/);
      const local = parts.at(-1)?.trim();
      const exported = parts[0]?.trim();
      if (local === 'extractBasicHtmlContent' && /^[A-Za-z_$][\w$]*$/.test(exported)) {
        return {moduleUrl: new URL(match[2], sdkModuleUrl).href, exportName: exported};
      }
    }
  }
  return undefined;
}

// Returns extractHtml({html, url, extractMode, signal}) -> {text, title}|null.
// It rejects with HTML_EXTRACTION_TIMEOUT or HTML_EXTRACTION_ABORTED; any
// other failure of the isolated path falls back to bounded in-process work.
export function createIsolatedHtmlExtractor({
  extract,
  locate,
  limits = {},
  WorkerImpl = Worker,
  workerUrl = new URL('./html-extraction-worker.mjs', import.meta.url),
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  warn = () => {},
} = {}) {
  if (typeof extract !== 'function') throw new TypeError('Pixel HTML extraction needs the SDK extractor');
  const bounds = {...HTML_EXTRACTION_LIMITS, ...limits};
  let target; // undefined: not located yet; null: unavailable
  let expectedSource, consecutiveFailures = 0;
  const inProcess = ({html, url, extractMode}) =>
    extract({html: boundedHtml(html, bounds.inProcessMaxChars), url, extractMode});
  const disable = (reason) => {
    if (target) warn(`Pixel HTML extraction runs in-process on a bounded prefix: ${reason}`);
    target = null;
  };

  function isolated({html, url, extractMode, signal}) {
    return new Promise((resolve, reject) => {
      let worker, timer, settled = false;
      const finish = (outcome) => {
        if (settled) return;
        settled = true;
        clearTimer(timer);
        signal?.removeEventListener?.('abort', onAbort);
        // Terminating an idle or finished worker is cheap; a busy one is
        // interrupted mid-regex.
        worker?.terminate?.().catch?.(() => {});
        outcome();
      };
      const onAbort = () => finish(() => reject(failure(HTML_EXTRACTION_ABORTED)));
      if (signal?.aborted) { onAbort(); return; }
      try {
        worker = new WorkerImpl(workerUrl, {
          workerData: {moduleUrl: target.moduleUrl, exportName: target.exportName, expectedSource,
            html, url, extractMode},
          resourceLimits: {maxOldGenerationSizeMb: bounds.workerHeapMb},
        });
      } catch (error) {
        disable(`worker unavailable (${String(error?.code ?? error?.name ?? 'error')})`);
        finish(() => resolve(inProcess({html, url, extractMode})));
        return;
      }
      worker.unref?.();
      signal?.addEventListener?.('abort', onAbort, {once: true});
      timer = setTimer(() => finish(() => reject(failure(HTML_EXTRACTION_TIMEOUT))), bounds.timeoutMs);
      // A worker that fails without a result (out of memory, a crash) falls
      // back to the bounded in-process path for this page. Repeated failures
      // disable the worker path; a timeout is never retried in-process.
      const fallBack = () => finish(() => {
        if (++consecutiveFailures >= 3) disable('worker failed repeatedly');
        resolve(inProcess({html, url, extractMode}));
      });
      worker.on('message', message => {
        if (message?.type === 'result') {
          consecutiveFailures = 0;
          finish(() => resolve(typeof message.text === 'string' && message.text
            ? {text: message.text, ...(typeof message.title === 'string' ? {title: message.title} : {})}
            : null));
        } else if (message?.type === 'unavailable') {
          // The located module is not the function this plugin imported.
          disable('extractor module mismatch');
          finish(() => resolve(inProcess({html, url, extractMode})));
        } else {
          fallBack();
        }
      });
      worker.on('error', fallBack);
      worker.on('exit', fallBack);
    });
  }

  return async function extractHtml({html, url, extractMode, signal} = {}) {
    if (target === undefined) {
      try {
        const located = locate?.();
        expectedSource = Function.prototype.toString.call(extract);
        target = located && typeof located.moduleUrl === 'string' && typeof located.exportName === 'string'
          ? located : null;
      } catch {
        target = null;
      }
    }
    if (!target) return inProcess({html, url, extractMode});
    return isolated({html, url, extractMode, signal});
  };
}
