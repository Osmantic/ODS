import http from 'node:http';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const proxyScript = join(__dirname, 'proxy.mjs');

const mockBrave = http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'application/json' });
  res.end(JSON.stringify({
    web: {
      results: [
        { title: "Test Title", url: "https://test.com", description: "Test snippet" }
      ]
    }
  }));
});

mockBrave.listen(0, '127.0.0.1', async () => {
  const upstreamPort = mockBrave.address().port;
  const proxyPort = upstreamPort + 1; // Just pick a different port

  const env = {
    ...process.env,
    BRAVE_SEARCH_API_KEY: 'dummy',
    BRAVE_SEARCH_PORT_INTERNAL: String(proxyPort),
    BRAVE_SEARCH_UPSTREAM_URL: `http://127.0.0.1:${upstreamPort}`
  };

  const proxy = spawn('node', [proxyScript], { env });

  // Wait for proxy to start
  await new Promise(r => setTimeout(r, 1000));

  try {
    // 1. Test /v1/search
    const v1Res = await fetch(`http://127.0.0.1:${proxyPort}/v1/search?q=test`);
    if (!v1Res.ok) throw new Error(`v1 failed: ${v1Res.status}`);
    const v1Data = await v1Res.json();

    if (!v1Data.query || !Array.isArray(v1Data.results) || v1Data.results[0].snippet !== "Test snippet") {
      throw new Error(`v1 shape contract failed: ${JSON.stringify(v1Data)}`);
    }
    console.log("✅ /v1/search contract passed");

    // 2. Test /search?format=json
    const sRes = await fetch(`http://127.0.0.1:${proxyPort}/search?format=json&q=test`);
    if (!sRes.ok) throw new Error(`searxng failed: ${sRes.status}`);
    const sData = await sRes.json();

    if (
      !sData.query ||
      sData.number_of_results !== 1 ||
      !Array.isArray(sData.results) ||
      sData.results[0].content !== "Test snippet" ||
      sData.results[0].engine !== "brave" ||
      sData.results[0].category !== "general"
    ) {
      throw new Error(`searxng shape contract failed: ${JSON.stringify(sData)}`);
    }
    console.log("✅ /search?format=json contract passed");

  } catch (err) {
    console.error("❌ Test failed:", err);
    process.exitCode = 1;
  } finally {
    mockBrave.close(() => {
      proxy.on('close', () => {
        // Deterministic shutdown complete. Event loop is empty,
        // Node will exit cleanly with process.exitCode.
      });
      proxy.kill();
    });
  }
});
