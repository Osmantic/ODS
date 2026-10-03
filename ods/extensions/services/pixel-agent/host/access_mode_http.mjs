// Owner-run loopback transport for Docker Desktop; authority stays in the UDS controller.
import http from 'node:http';
import {fileURLToPath} from 'node:url';
import {handleAccessMode, handleModelControl, readAccessOwnerKey} from './access_mode_relay.mjs';

export function accessPort(value = '18790') {
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,4}$/.test(value)
      || Number(value) > 65535) throw new Error('invalid-native-access-port');
  return Number(value);
}

export function createAccessServer({ownerKey, request} = {}) {
  if (typeof ownerKey !== 'string' || !/^[!-~]{32,4096}$/.test(ownerKey)) {
    throw new Error('access-owner-auth-unavailable');
  }
  const server = http.createServer({maxHeaderSize: 8192, headersTimeout: 10000,
    requestTimeout: 15000}, async (req, res) => {
    const reply = (status, error) => {
      res.writeHead(status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'});
      res.end(JSON.stringify({error}));
    };
    // This is a service transport, never a browser or generated-site API.
    if (req.headers.origin !== undefined || req.headers['sec-fetch-site'] !== undefined) {
      return reply(403, 'owner-required');
    }
    if (!['/v1/access-mode', '/v1/model-control'].includes(req.url)) {
      return reply(404, 'not-found');
    }
    if (req.method === 'GET' && (req.headers['transfer-encoding'] !== undefined
        || (req.headers['content-length'] !== undefined && req.headers['content-length'] !== '0'))) {
      return reply(400, 'invalid-request');
    }
    try {
      const handler = req.url === '/v1/access-mode' ? handleAccessMode : handleModelControl;
      await handler(req, res, {ownerKey, request});
    } catch {
      if (!res.headersSent) reply(503, 'access-service-unavailable');
      else res.destroy();
    }
  });
  server.maxRequestsPerSocket = 100;
  return server;
}

export function startNativeAccessServer() {
  if (process.platform !== 'darwin' || !Number.isInteger(process.geteuid?.()) || process.geteuid() === 0) {
    throw new Error('macos-owner-service-required');
  }
  const ownerKey = readAccessOwnerKey('/etc/ods/pixel-access-relay.key');
  const server = createAccessServer({ownerKey});
  server.listen(accessPort(process.env.PIXEL_NATIVE_ACCESS_PORT), '127.0.0.1');
  server.on('error', () => {
    process.stderr.write('native-access-listener-unavailable\n');
    process.exitCode = 1;
  });
  return server;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try { startNativeAccessServer(); }
  catch { process.stderr.write('native-access-startup-failed\n'); process.exitCode = 1; }
}
