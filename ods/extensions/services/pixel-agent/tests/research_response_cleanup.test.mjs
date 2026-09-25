import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { createPerplexicaResearchTool } from '../plugin/perplexica-research.mjs';

for (const stage of ['config', 'search']) {
  test(`releases an unfinished HTTP error body from research ${stage}`, async () => {
    let markClosed;
    const closed = new Promise(resolve => {markClosed = resolve;});
    const server = http.createServer((request, response) => {
      if (stage === 'search' && request.url === '/api/config') {
        response.setHeader('Content-Type', 'application/json');
        response.end(JSON.stringify({values:{preferences:{
          defaultChatProvider:'local', defaultChatModel:'chat',
          defaultEmbeddingProvider:'local', defaultEmbeddingModel:'embed',
        }}}));
        return;
      }
      response.on('close', markClosed);
      response.writeHead(503, {'Content-Type':'text/plain'});
      response.write('Unavailable');
      // Keep the failed body open: the client must release it after rejecting status.
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let timer;
    try {
      const tool = createPerplexicaResearchTool({port:server.address().port, env:{}});
      const result = await tool.execute('failed-http', {query:'Research public facts'});
      assert.equal(result.details.status, 'unavailable');
      await Promise.race([closed, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error('Failed response connection was retained')), 1000);
      })]);
    } finally {
      clearTimeout(timer);
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
  });
}
