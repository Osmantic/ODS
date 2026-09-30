// Receipts describe the managed executor only, never the conversational sandbox.
const CODES=new Set(['ready','missing','unavailable','incompatible','unsupported','denied']);
const CHECKS=['node','npm','python','pip','venv','scratch'];
const FAILURE_PHASES=new Set(['configuration','authorization','storage-reservation','storage-create','execution','output-validation']);
const FAILURE_CODES=new Set(['storage-recovery-required','storage-capacity-reserved','engine-headroom-insufficient',
  'engine-info-unavailable',
  'operation-timeout','command-failed','host-io-error','invalid-evidence','authorization-revoked','probe-failed']);
export function validateDiagnostic(value, runtime) {
  if (!value || value.schemaVersion!==1 || value.kind!=='ods-project-diagnostic' || value.scope!=='managed-executor'
      || !['npm','python'].includes(value.runtime) || (runtime && value.runtime!==runtime)
      || !CODES.has(value.code) || !['confirmed','unconfirmed','not-started'].includes(value.cleanup)
      || value.network!=='none' || value.chatSandboxVerified!==false
      || !Number.isInteger(value.scratchLimitBytes) || value.scratchLimitBytes<32*1024*1024 || value.scratchLimitBytes>64*1024*1024
      || !value.checks || typeof value.checks!=='object' || Array.isArray(value.checks)
      || JSON.stringify(value).length>8192) throw Error('unconfirmed diagnostic receipt');
  const keys=Object.keys(value.checks);
  if (keys.length && (keys.length!==CHECKS.length || keys.some(key=>!CHECKS.includes(key)))) throw Error('invalid diagnostic checks');
  for(const check of Object.values(value.checks)) {
    if (!check || !['code','code,version'].includes(Object.keys(check).sort().join(',')) || !CODES.has(check.code)
        || (check.version!==undefined && (check.code!=='ready' || typeof check.version!=='string'
            || !/^[\x20-\x7e]{1,96}$/.test(check.version)))) throw Error('invalid diagnostic check');
  }
  if(value.code==='ready' && keys.length!==CHECKS.length) throw Error('missing diagnostic evidence');
  if(value.failure!==undefined && (!value.failure || Object.keys(value.failure).sort().join(',')!=='code,phase'
      || !FAILURE_PHASES.has(value.failure.phase) || !FAILURE_CODES.has(value.failure.code)
      || value.code==='ready')) throw Error('invalid diagnostic failure');
  return value;
}
