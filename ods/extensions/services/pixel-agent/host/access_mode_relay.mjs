// Owner-only HTTP-to-UDS relay. Chat/socket membership is not mode authority.
import fs from 'node:fs';
import net from 'node:net';
import {timingSafeEqual} from 'node:crypto';

export const ACCESS_SOCKET_PATH = process.platform === 'darwin'
  ? '/private/var/run/ods-pixel-access/control.sock'
  : '/run/ods-pixel-access/control.sock';

export function readAccessOwnerKey(filename, uid = process.geteuid?.()) {
  if (!filename || !filename.startsWith('/') || filename.includes('\0')) return null;
  let fd;
  try {
    fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    const info = fs.fstatSync(fd);
    if (!info.isFile() || info.uid !== uid || info.nlink !== 1 || (info.mode & 0o077) || info.size > 4096) return null;
    const key = fs.readFileSync(fd, 'utf8').trim();
    return /^[!-~]{32,4096}$/.test(key) ? key : null;
  } catch { return null; }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}

export function validAccessChange(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join() === 'confirmed,mode,revision'
    && ['sandboxed', 'full-access'].includes(value.mode)
    && typeof value.confirmed === 'boolean' && (value.mode !== 'full-access' || value.confirmed)
    && typeof value.revision === 'string' && /^[a-f0-9]{64}$/.test(value.revision);
}

const hex = value => typeof value === 'string' && value.length === 64 && /^[a-f0-9]+$/.test(value);
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join() === [...keys].sort().join();
export function validModelContract(value) {
  return (exact(value, ['model','contextLength','maxTokens','reasoning'])
      || exact(value, ['model','contextLength','maxTokens','reasoning','routeFingerprint']))
    && typeof value.model === 'string' && value.model.length <= 256
    && /^[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]*$/.test(value.model) && !/[\r\n]/.test(value.model)
    && Number.isInteger(value.contextLength) && value.contextLength >= 4096 && value.contextLength <= 10000000
    && Number.isInteger(value.maxTokens) && value.maxTokens >= 1 && value.maxTokens <= value.contextLength
    && typeof value.reasoning === 'boolean'
    && (!Object.hasOwn(value,'routeFingerprint') || hex(value.routeFingerprint));
}

export function validModelControl(value) {
  if (exact(value,['operation']) && value.operation === 'model-status') return true;
  if (!exact(value,['operation','request']) || !value.request || !hex(value.request.transactionId)) return false;
  const request = value.request;
  if (value.operation === 'model-begin') return exact(request,['transactionId','revision']) && hex(request.revision);
  if (value.operation === 'model-apply') return exact(request,['transactionId','target']) && validModelContract(request.target);
  if (value.operation === 'model-finish') return exact(request,['transactionId','outcome']) && ['commit','rollback'].includes(request.outcome);
  return false;
}

export function publicModelControl(value) {
  const keys = ['schemaVersion','status','revision','contract','pending','transactionId','outcome'];
  if (!value || !keys.every(key=>Object.hasOwn(value,key)) || value.schemaVersion !== 1
      || !['ready','held','applied','completed'].includes(value.status) || !hex(value.revision)
      || !validModelContract(value.contract) || typeof value.pending !== 'boolean'
      || value.pending !== ['held','applied'].includes(value.status)
      || (value.transactionId !== null && !hex(value.transactionId))
      || (value.status === 'ready') !== (value.transactionId === null)
      || (value.status === 'completed' ? !['commit','rollback'].includes(value.outcome) : value.outcome !== null)) {
    throw new Error('invalid-model-control-response');
  }
  return Object.fromEntries(keys.map(key=>[key,value[key]]));
}

export async function handleModelControl(req, res, {ownerKey, request = requestAccessController} = {}) {
  const reply = (status, body) => {res.writeHead(status, {'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(body));};
  if (!ownerKey) return reply(503,{error:'model-owner-auth-unavailable'});
  const expected=Buffer.from(`Bearer ${ownerKey}`), supplied=Buffer.from(typeof req.headers.authorization === 'string' ? req.headers.authorization : '');
  if (expected.length !== supplied.length || !timingSafeEqual(expected,supplied)) return reply(403,{error:'owner-required'});
  if (req.url !== '/v1/model-control') return reply(400,{error:'invalid-request'});
  if (req.method !== 'POST') return reply(405,{error:'method-not-allowed'});
  if (String(req.headers['content-type'] || '').split(';')[0].trim() !== 'application/json') return reply(415,{error:'invalid-content-type'});
  try {
    const chunks=[]; let bytes=0;
    for await (const chunk of req) {
      bytes+=chunk.length;
      if (bytes>2048) return reply(413,{error:'request-too-large'});
      chunks.push(chunk);
    }
    let payload;
    try {payload=JSON.parse(Buffer.concat(chunks).toString('utf8'));} catch {return reply(400,{error:'invalid-request'});}
    if (!validModelControl(payload)) return reply(400,{error:'invalid-request'});
    // The root socket also serves ODS installer model promotion, whose
    // model-status/begin/finish shapes differ from browser model switching.
    const controllerPayload={...payload,operation:payload.operation.replace(/^model-/, 'model-route-')};
    const result=await request(controllerPayload,{timeout:payload.operation === 'model-status' ? 20000 : 305000});
    return reply(result.status,result.status === 200 ? publicModelControl(result.body) : {error:'model-change-unconfirmed'});
  } catch {return reply(503,{error:'model-control-unavailable'});}
}

export function requestAccessController(payload, {socketPath = ACCESS_SOCKET_PATH, timeout = 305000} = {}) {
  return new Promise((resolve, reject) => {
    const socket = net.createConnection(socketPath);
    let chunks = [], bytes = 0, settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer); socket.destroy();
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('access-service-unavailable')), timeout);
    socket.once('connect', () => socket.write(JSON.stringify(payload) + '\n'));
    socket.once('error', () => finish(new Error('access-service-unavailable')));
    socket.on('data', chunk => {
      bytes += chunk.length;
      if (bytes > 65536) return finish(new Error('invalid-access-response'));
      chunks.push(chunk);
      const raw = Buffer.concat(chunks);
      if (!raw.includes(10)) return;
      try {
        if (raw.at(-1) !== 10 || raw.subarray(0, -1).includes(10)) throw new Error();
        const value = JSON.parse(raw.toString('utf8'));
        if (!value || Object.keys(value).sort().join() !== 'body,status'
            || ![200,400,403,409,503].includes(value.status) || !value.body
            || typeof value.body !== 'object' || Array.isArray(value.body)) throw new Error();
        finish(null, value);
      } catch { finish(new Error('invalid-access-response')); }
    });
    socket.once('end', () => {if (!settled) finish(new Error('invalid-access-response'));});
  });
}

export async function handleAccessMode(req, res, {ownerKey, request = requestAccessController} = {}) {
  const reply = (status, body) => {res.writeHead(status, {'Content-Type':'application/json', 'Cache-Control':'no-store'});res.end(JSON.stringify(body));};
  const header = req.headers.authorization;
  if (!ownerKey) return reply(503, {error:'access-owner-auth-unavailable'});
  const expected = Buffer.from(`Bearer ${ownerKey}`), supplied = Buffer.from(typeof header === 'string' ? header : '');
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) return reply(403, {error:'owner-required'});
  if (req.url !== '/v1/access-mode') return reply(400, {error:'invalid-request'});
  if (!['GET','POST'].includes(req.method)) return reply(405, {error:'method-not-allowed'});
  try {
    let body = null;
    if (req.method === 'POST') {
      if (String(req.headers['content-type'] || '').split(';')[0].trim() !== 'application/json') return reply(415, {error:'invalid-content-type'});
      const chunks = []; let bytes = 0;
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 1024) return reply(413, {error:'request-too-large'});
        chunks.push(chunk);
      }
      try {body = JSON.parse(Buffer.concat(chunks).toString('utf8'));} catch {return reply(400, {error:'invalid-request'});}
      if (!validAccessChange(body)) return reply(400, {error:'invalid-request'});
    }
    const result = await request(body ? {operation:'change', request:body} : {operation:'status'}, {timeout:body ? 305000 : 20000});
    reply(result.status, result.body);
  } catch {reply(503, {error:'access-service-unavailable'});}
}
