// One HTML extraction, isolated from the gateway thread (html-extraction.mjs).
// It loads the SDK module that defines extractBasicHtmlContent and runs it
// only if its source text is identical to the function the plugin imported;
// otherwise it reports "unavailable" and the caller falls back.
import {parentPort, workerData} from 'node:worker_threads';

const {moduleUrl, exportName, expectedSource, html, url, extractMode} = workerData ?? {};
let extract;
try {
  const module = await import(moduleUrl);
  extract = module?.[exportName];
} catch {
  extract = undefined;
}
if (typeof extract !== 'function' || typeof expectedSource !== 'string' ||
    Function.prototype.toString.call(extract) !== expectedSource) {
  parentPort.postMessage({type: 'unavailable'});
} else {
  try {
    const result = await extract({html, url, extractMode});
    parentPort.postMessage({type: 'result',
      text: typeof result?.text === 'string' ? result.text : '',
      ...(typeof result?.title === 'string' ? {title: result.title} : {})});
  } catch {
    parentPort.postMessage({type: 'failed'});
  }
}
