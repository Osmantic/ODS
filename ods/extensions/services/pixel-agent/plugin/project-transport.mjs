import net from 'node:net';

// socketPath/sessionId come from installed runtime configuration/context, never
// the model's tool parameters. The service still checks peer and owner policy.
export function createProjectTransport({socketPath, sessionId}) {
  if (typeof socketPath !== 'string' || !socketPath.startsWith('/') || socketPath.includes('\0')
      || typeof sessionId !== 'string' || !/^[A-Za-z0-9_.:-]{1,192}$/.test(sessionId)) {
    throw Error('configured project socket and session required');
  }
  return (request, {toolCallId, signal} = {}) => new Promise((resolve, reject) => {
    if (typeof toolCallId !== 'string' || !/^[A-Za-z0-9_.:-]{1,192}$/.test(toolCallId)) {
      reject(Error('runtime tool call identity required')); return;
    }
    if (signal?.aborted) {reject(Error('aborted')); return;}
    const payload = JSON.stringify({context: {sessionId, toolCallId}, request}) + '\n';
    if (Buffer.byteLength(payload) > 8192) {reject(Error('request too large')); return;}
    const socket = net.createConnection({path: socketPath});
    let chunks = [], length = 0, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener('abort', abort);
      socket.destroy();
      if (error) reject(error); else resolve(value);
    };
    const abort = () => finish(Error('response interrupted; execution state unknown'));
    signal?.addEventListener('abort', abort, {once: true});
    socket.setTimeout(15000, () => finish(Error('controller response timeout')));
    socket.on('error', error => finish(error));
    socket.on('connect', () => socket.write(payload));
    socket.on('data', chunk => {
      length += chunk.length;
      if (length > 1024 * 1024) return finish(Error('response too large'));
      chunks.push(chunk);
      if (!chunk.includes(10)) return;
      const bytes = Buffer.concat(chunks);
      if (bytes.indexOf(10) !== bytes.length - 1) return finish(Error('invalid response framing'));
      try {finish(null, JSON.parse(bytes.toString('utf8')));}
      catch {finish(Error('invalid controller JSON'));}
    });
    socket.on('end', () => finish(Error('controller response incomplete')));
    socket.on('close', () => {if (!settled) finish(Error('controller disconnected'));});
  });
}
