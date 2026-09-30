// One PDF text extraction in its own Node process (pdf-text.mjs). The
// document arrives on stdin and one JSON line with the outcome leaves on
// stdout. The gateway starts this process with an empty environment and a V8
// heap limit and kills it at its deadline, so running out of heap here ends
// only this process. It parses only the bytes it was given.
import {extractPdfTextSync} from './pdf-text.mjs';

let message;
try {
  const {limits, deadline} = JSON.parse(Buffer.from(process.argv[2] ?? '', 'base64').toString('utf8'));
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > limits.maxBytes) throw Object.assign(new Error('body too large'), {reason: 'too-large'});
    chunks.push(chunk);
  }
  const bytes = Buffer.concat(chunks, size);
  message = {type: 'result', ...extractPdfTextSync(new Uint8Array(bytes.buffer, bytes.byteOffset, bytes.byteLength),
    {limits, budgetMs: Math.max(50, deadline - Date.now())})};
} catch (error) {
  message = {type: 'failed', reason: typeof error?.reason === 'string' ? error.reason : 'unsupported'};
}
process.stdout.write(`${JSON.stringify(message)}\n`);
