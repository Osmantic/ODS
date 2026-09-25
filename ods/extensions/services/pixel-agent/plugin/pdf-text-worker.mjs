// One PDF text extraction, isolated from the gateway thread (pdf-text.mjs).
// It parses only the bytes it was given: no network, files or environment.
import {parentPort, workerData} from 'node:worker_threads';
import {extractPdfTextSync} from './pdf-text.mjs';

let message;
try {
  const {bytes, limits, budgetMs} = workerData ?? {};
  message = {type: 'result', ...extractPdfTextSync(new Uint8Array(bytes), {limits, budgetMs})};
} catch (error) {
  message = {type: 'failed', reason: typeof error?.reason === 'string' ? error.reason : 'unsupported'};
}
parentPort.postMessage(message);
