// Syntax opts a completed call into host byte revalidation. It never proves
// that shell/PATH/profile execution was read-only.
function boundedGrepInspection(command) {
  // An optional status-only echo reports grep's no-match exit without changing
  // files. No arbitrary shell suffix, expansion, redirect or pipeline is admitted.
  const source = command.replace(/;\s*echo\s+"[A-Za-z0-9 _:=.-]*\$\?"\s*$/, '').trim();
  const tokens = [];
  let offset = 0;
  while (offset < source.length) {
    while (source[offset] === ' ') offset++;
    if (offset === source.length) break;
    let token = '';
    const quote = source[offset] === "'" || source[offset] === '"' ? source[offset++] : undefined;
    if (quote) {
      const end = source.indexOf(quote, offset);
      if (end < 0) return false;
      token = source.slice(offset, end);
      if (/[\r\n\0]/.test(token) || quote === '"' && /[$`\\]/.test(token)) return false;
      offset = end + 1;
      if (offset < source.length && source[offset] !== ' ') return false;
    } else {
      const match = /^[A-Za-z0-9_./:=+-]+/.exec(source.slice(offset));
      if (!match) return false;
      token = match[0]; offset += token.length;
      if (offset < source.length && source[offset] !== ' ') return false;
    }
    tokens.push(token);
  }
  if (tokens.shift() !== 'grep') return false;
  while (tokens.length && /^-[nciIlqvFEsHho]+$/.test(tokens[0])) tokens.shift();
  if (tokens[0] === '--') tokens.shift();
  if (tokens.length < 2 || !tokens[0] || tokens[0].startsWith('-')) return false;
  return tokens.slice(1).every(path => /^[A-Za-z0-9_./][A-Za-z0-9_./-]*$/.test(path));
}

export function inspectionRevalidationCandidate(params) {
  if (!params || typeof params.command !== 'string' || params.background === true || params.pty === true || params.env != null) return false;
  if (Object.keys(params).some(key=>!['command','workdir','timeout','yieldMs','background','pty'].includes(key))) return false;
  const command = params.command;
  if (command.length > 1024) return false;
  if (/^grep /.test(command)) return boundedGrepInspection(command);
  if (!/^[A-Za-z0-9_./ -]+$/.test(command)) return false;
  const tokens = command.trim().split(/ +/);
  if (tokens[0] === 'pwd') return tokens.length === 1 || tokens.length === 2 && /^-[LP]$/.test(tokens[1]);
  if (tokens[0] !== 'ls') return false;
  return tokens.slice(1).every(token=>token === '--' || /^-[alhdF]+$/.test(token) ||
    !token.startsWith('-') && /^[A-Za-z0-9_./][A-Za-z0-9_./-]*$/.test(token));
}

// A settled core-file call or foreground command can only request a fresh
// host byte comparison. The host re-derives the published directory's
// snapshot digest; equality, never command syntax or outcome, restores
// currency. It does not establish task completion or new authorship.
// Commands that visibly detach work (or inject a shell environment) stay
// ineligible. Like publication itself, the comparison is point-in-time and
// cannot attest quiescence of a descendant a command hides from its syntax.
const DETACHING_COMMAND = /(?:^|[^&<>|])&(?!&)|\b(?:nohup|setsid|disown|coproc)\b/;
export function workspaceRevalidationCandidate(tool, params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return false;
  if (['read','write','edit','apply_patch'].includes(tool)) return true;
  return tool === 'exec' && (inspectionRevalidationCandidate(params) ||
    typeof params.command === 'string' && params.command.trim() !== '' && params.background !== true &&
    params.pty !== true && params.env == null && !DETACHING_COMMAND.test(params.command));
}

// A paired receipt settles an eligible call once nothing it started can still
// run, whatever its outcome: file tools are synchronous, and an exec must have
// exited, with any exit code. Timeouts, signals, runtime failures and running
// sessions stay unsettled and revoke the pending comparison, as does an exit-0
// receipt that also reports an error.
export function settledRevalidationReceipt(tool, event) {
  if (tool !== 'exec') return ['write','edit','apply_patch'].includes(tool);
  const details = event?.result?.details;
  if (details?.status !== 'completed' || !Number.isInteger(details.exitCode)) return false;
  return details.exitCode !== 0 || !event.error && event.result.isError !== true;
}

// Calls that cannot change workspace bytes, whatever their outcome. They
// neither advance nor revoke a pending host revalidation. Preview inspection
// renders the immutable published snapshot in the isolated inspection broker.
const READ_ONLY_TOOLS = new Set(['read','web_search','web_fetch','pixel_ods_web_extract','pixel_ods_research','tool_search','tool_describe',
  'pixel_ods_workspace_preview_inspect']);
export function workspaceReadOnlyCall(tool, params) {
  if (tool === 'tool_call') {
    const name = /^(?:openclaw:(?:core|pixel-ods):)?([a-z_]+)$/.exec(typeof params?.id === 'string' ? params.id : '')?.[1];
    return Boolean(name) && name !== 'tool_call' && workspaceReadOnlyCall(name, params.args);
  }
  return READ_ONLY_TOOLS.has(tool) || tool === 'process' && ['list','poll','log'].includes(params?.action);
}

export async function boundedPreviewVerification(verify, receipt, valid, {timeoutMs=4000}={}) {
  const abort = new AbortController();
  let timer;
  try {
    if (!valid()) return false;
    const result = await Promise.race([
      Promise.resolve().then(()=>verify(receipt,{signal:abort.signal})),
      new Promise(resolve=>{timer=setTimeout(()=>{abort.abort();resolve(false);},timeoutMs);}),
    ]);
    return result === true && !abort.signal.aborted && valid();
  } catch { return false; }
  finally {clearTimeout(timer);abort.abort();}
}
