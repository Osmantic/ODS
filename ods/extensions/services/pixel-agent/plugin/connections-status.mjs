import {execFile} from 'node:child_process';

const HELPER = '/usr/local/libexec/ods-pixel-access/pixel_connection_agent_status.py';
const KEYS = ['schemaVersion','kind','scope','observedAt','available','reason','recordConfigured',
  'localSavedSignIn','connectionState','ownerEnabled','providerPermissionObserved','sourceCheckedAt',
  'reads','providerCheckedNow'];
const STATES = new Set(['not-connected','first-sync','connected','needs-reconnect','blocked-by-admin',
  'setup-broken','missing-permission','unknown-error','behind','unknown','transitioning']);
const ERRORS = new Set(['invalid-grant','not-connected','blocked-by-admin','api-disabled','client-invalid',
  'missing-scope','unknown','rate-limited','network']);
const REASONS = new Set([...ERRORS,'not-available-on-this-system','connection-status-unavailable',
  'record-unusable','journal-unusable','connection-admission-held','sign-in-missing','status-unreadable','sync-stopped']);
const exact = (value, keys) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key));
const optional = (value, check) => value === null || check(value);
const triState = value => value === null || typeof value === 'boolean';
const timestamp = value => {
  if (typeof value !== 'string' || value.length > 32) return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$/.exec(value);
  if (!parts || Number(parts[1]) < 1) return false;
  const instant = new Date(value);
  // Date.parse normalizes some impossible dates; the Root contract rejects them.
  return [instant.getUTCFullYear(),instant.getUTCMonth()+1,instant.getUTCDate(),
    instant.getUTCHours(),instant.getUTCMinutes(),instant.getUTCSeconds()]
    .every((part,index) => part === Number(parts[index+1]));
};
const flags = value => exact(value,['gmailRead','calendarRead']) && Object.values(value).every(triState);
const source = value => exact(value,['lastObservedState','recent','lastSuccessAt','complete','error'])
  && ['ok','error','disabled','unknown'].includes(value.lastObservedState) && triState(value.recent)
  && triState(value.complete) && optional(value.lastSuccessAt,timestamp)
  && optional(value.error,error => ERRORS.has(error));

// Reject the broader owner/UI response as well as extra fields. The model gets
// no account identifiers, OAuth settings, pending approvals or source records.
export function validateConnectionStatus(value) {
  if (!exact(value,KEYS) || value.schemaVersion !== 1 || value.kind !== 'ods-connection-agent-status'
      || value.scope !== 'reviewed-google' || !timestamp(value.observedAt)
      || typeof value.available !== 'boolean' || !optional(value.reason,reason => REASONS.has(reason))
      || !triState(value.recordConfigured) || !triState(value.localSavedSignIn)
      || !STATES.has(value.connectionState) || !flags(value.ownerEnabled) || !flags(value.providerPermissionObserved)
      || !optional(value.sourceCheckedAt,timestamp) || !exact(value.reads,['email','calendar'])
      || !Object.values(value.reads).every(source) || value.providerCheckedNow !== false) {
    throw Error('Connection status unavailable');
  }
  if (value.localSavedSignIn === true && value.recordConfigured !== true
      || value.connectionState === 'not-connected' && (value.recordConfigured !== false || value.localSavedSignIn !== false)
      || !value.available && (value.connectionState !== 'unknown' || value.recordConfigured !== null
        || value.localSavedSignIn !== null || [value.ownerEnabled,value.providerPermissionObserved]
          .some(flags => Object.values(flags).some(flag => flag !== null)))) {
    throw Error('Connection status unavailable');
  }
  return value;
}

export function readConnectionStatus({execute = execFile, platform = process.platform, signal} = {}) {
  if (platform !== 'linux') return Promise.reject(Error('Connection status unavailable'));
  return new Promise((resolve,reject) => {
    execute('/usr/bin/python3',['-I',HELPER],{
      cwd:'/',env:{PATH:'/usr/bin:/bin'},encoding:'utf8',timeout:22000,maxBuffer:8192,signal,
    },(error,stdout) => {
      try {
        if (error || typeof stdout !== 'string' || Buffer.byteLength(stdout,'utf8') > 8192
            || !stdout.endsWith('\n') || stdout.slice(0,-1).includes('\n')) throw Error();
        resolve(validateConnectionStatus(JSON.parse(stdout)));
      } catch { reject(Error('Connection status unavailable')); }
    });
  });
}

export function createConnectionsStatusTool({read = readConnectionStatus} = {}) {
  return {
    name:'pixel_ods_connections_status',
    description:'Check saved Google/Gmail and Calendar connection, read permissions and recent sync health. No emails or events. This is connection status, not the installable extension catalog. Returns separate saved-sign-in, owner permission, last observed provider permission and source health fields. providerCheckedNow is false: recent sync is past evidence, not a fresh Google authorization check. Unknown/unavailable or a missing read permission does not mean the account is disconnected. This read cannot connect, reconnect, install, change settings or approve anything. Use app count tools for counts, app read tools for contents.',
    parameters:{type:'object',additionalProperties:false,properties:{}},
    execute:async (_id,args,signal) => {
      try {
        if (signal?.aborted || args !== undefined && !exact(args,[])) throw Error();
        const status = validateConnectionStatus(await read({signal}));
        return {content:[{type:'text',text:JSON.stringify(status)}]};
      } catch {
        return {isError:true,content:[{type:'text',text:JSON.stringify({
          kind:'ods-connection-agent-status-unavailable',
          message:'Could not read connection status. The connection is unknown; this does not prove the account is disconnected. No account, permission or setting was changed.',
        })}]};
      }
    },
  };
}
