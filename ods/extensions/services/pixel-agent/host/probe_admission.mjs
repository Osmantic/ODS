// Owner-private one-shot request authorization. No model content is retained.
import * as odsAdmissionFs from 'node:fs';
import * as odsAdmissionPath from 'node:path';
import * as odsAdmissionOs from 'node:os';
import * as odsAdmissionCrypto from 'node:crypto';

function odsAdmissionRoot() {
  return process.env.ODS_PIXEL_PROBE_ROOT || odsAdmissionPath.join(process.env.OPENCLAW_STATE_DIR || odsAdmissionPath.join(odsAdmissionOs.homedir(), '.openclaw'), 'ods-measurement');
}
function odsAdmissionRead(file) {
  const root=odsAdmissionPath.dirname(file);
  if(typeof process.getuid!=='function' || !odsAdmissionPath.isAbsolute(file) || odsAdmissionFs.realpathSync(root)!==root)return null;
  const parent=odsAdmissionFs.statSync(root);
  if(parent.uid!==process.getuid() || (parent.mode&0o077))return null;
  const fd=odsAdmissionFs.openSync(file,odsAdmissionFs.constants.O_RDONLY|odsAdmissionFs.constants.O_NOFOLLOW);
  try { const s=odsAdmissionFs.fstatSync(fd);if(!s.isFile() || s.uid!==process.getuid() || s.nlink!==1 || (s.mode&0o077) || s.size>8192)return null;return JSON.parse(odsAdmissionFs.readFileSync(fd,'utf8')); }
  finally {odsAdmissionFs.closeSync(fd);}
}
function odsAdmissionValid(a,now) {
  return a?.schemaVersion===1 && a.ownerUid===process.getuid() && a.agentId==='pixel' &&
    /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(a.scopeId) && /^[a-f0-9]{64}$/.test(a.signingKey) &&
    Number.isSafeInteger(a.expiresAtMs) && now<a.expiresAtMs && a.expiresAtMs<=now+1800_000 &&
    Number.isSafeInteger(a.maxAttempts) && a.maxAttempts>0 && a.maxAttempts<=16 &&
    ['user','requestId','sessionKey','sessionId','provider','modelId','baseUrl'].every(k=>typeof a[k]==='string' && a[k].length>0 && a[k].length<=256);
}
function odsAdmissionCanonical(value) {
  if(Array.isArray(value))return '['+value.map(odsAdmissionCanonical).join(',')+']';
  if(value!==null && typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+odsAdmissionCanonical(value[k])).join(',')+'}';
  return JSON.stringify(value);
}
function odsAdmissionSha(value) {return odsAdmissionCrypto.createHash('sha256').update(value).digest('hex');}
function odsAdmissionMac(key,value) {return odsAdmissionCrypto.createHmac('sha256',key).update(value).digest('hex');}
function odsAdmissionEqual(a,b) {return typeof a==='string' && typeof b==='string' && a.length===b.length && odsAdmissionCrypto.timingSafeEqual(Buffer.from(a),Buffer.from(b));}
function odsAdmissionWriteExclusive(file,value) {
  const fd=odsAdmissionFs.openSync(file,odsAdmissionFs.constants.O_WRONLY|odsAdmissionFs.constants.O_CREAT|odsAdmissionFs.constants.O_EXCL|odsAdmissionFs.constants.O_NOFOLLOW,0o600);
  try {odsAdmissionFs.writeFileSync(fd,JSON.stringify(value));odsAdmissionFs.fsyncSync(fd);}finally{odsAdmissionFs.closeSync(fd);}
}

export function createProbeIngressHeader({incoming,user,requestId,gatewayBody}, controls={}) {
  try {
    if(!incoming?.history_snapshot || incoming.request_id!==requestId || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(requestId || '') || typeof gatewayBody!=='string' || Buffer.byteLength(gatewayBody)>2*1024*1024)return {};
    const root=controls.root ?? odsAdmissionRoot(),now=(controls.clock ?? Date.now)();
    const authPath=odsAdmissionPath.join(root,'authorize-'+odsAdmissionSha(user+'\0'+requestId)+'.json');
    const a=odsAdmissionRead(authPath);
    if(!odsAdmissionValid(a,now) || a.user!==user || a.requestId!==requestId || !odsAdmissionEqual(a.incomingHmac,odsAdmissionMac(a.signingKey,odsAdmissionCanonical(incoming))))return {};
    // Hard-link election is atomic and cannot overwrite an existing claim.
    // A crash between link/unlink leaves a two-link file: every reader rejects
    // it, and the original authorization cannot be replayed. Owner recovery is
    // explicit; diagnostics never retry or delay the real request.
    odsAdmissionFs.linkSync(authPath,authPath+'.claimed');odsAdmissionFs.unlinkSync(authPath);
    const nonce=odsAdmissionCrypto.randomUUID().replaceAll('-','');
    const gatewayBodySha256=odsAdmissionSha(gatewayBody);
    odsAdmissionWriteExclusive(odsAdmissionPath.join(root,'transfer-'+nonce+'.json'),{...a,nonce,gatewayBodySha256});
    return {'X-ODS-Probe-Admission':nonce+'.'+odsAdmissionMac(a.signingKey,'ods.probe-admission.v1\0'+nonce+'\0'+gatewayBodySha256)};
  } catch {return {};}
}

export function consumeProbeGatewayAdmission({header,payload,sessionKey,runId,agentId}, controls={}) {
  try {
    if(agentId!=='pixel' || typeof header!=='string' || !/^[a-f0-9]{32}\.[a-f0-9]{64}$/.test(header) || !/^chatcmpl_[a-f0-9-]{36}$/.test(runId || ''))return false;
    const root=controls.root ?? odsAdmissionRoot(),now=(controls.clock ?? Date.now)();
    const [nonce,signature]=header.split('.'),file=odsAdmissionPath.join(root,'transfer-'+nonce+'.json');
    const a=odsAdmissionRead(file),body=JSON.stringify(payload);
    if(!odsAdmissionValid(a,now) || a.nonce!==nonce || a.sessionKey!==sessionKey || a.user!==payload.user || a.gatewayBodySha256!==odsAdmissionSha(body) || !odsAdmissionEqual(signature,odsAdmissionMac(a.signingKey,'ods.probe-admission.v1\0'+nonce+'\0'+a.gatewayBodySha256)))return false;
    // Bind the generated native run before its provider can start. The exact
    // pre-existing session ID is still checked against actual SDK context.
    odsAdmissionFs.linkSync(file,file+'.consumed');odsAdmissionFs.unlinkSync(file);
    odsAdmissionWriteExclusive(odsAdmissionPath.join(root,'run-'+odsAdmissionSha(runId)+'.json'),{...a,runId});
    return true;
  }catch{return false;}
}

export {odsAdmissionCanonical,odsAdmissionMac,odsAdmissionSha};
