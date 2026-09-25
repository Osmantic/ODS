import { readFileSync } from 'node:fs';
import { createPublicWebExtractTool } from '../../plugin/web-extract.mjs';

const { text, query } = JSON.parse(readFileSync(0, 'utf8'));
let released = false;
const tool = createPublicWebExtractTool({
  guardedFetch: async () => ({
    response: new Response(text, { headers: { 'content-type': 'text/plain' } }),
    finalUrl: 'https://docs.example.org/reference', release() { released = true; },
  }),
  readResponseText: async response => ({ text: await response.text(), truncated: false }),
  extractBasicHtmlContent: async () => { throw new Error('plain text does not need HTML extraction'); },
});
const start = performance.now();
const result = await tool.execute('bounded-page', { url: 'https://docs.example.org/reference', query });
console.log(JSON.stringify({ result, released, elapsedMs: performance.now() - start }));
