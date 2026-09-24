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
  while (tokens.length && /^-[nciIlqvFEsHh]+$/.test(tokens[0])) tokens.shift();
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

// A successful synchronous core-file receipt can only request a fresh host
// byte comparison. It does not establish task completion or new authorship.
// Preserve the existing narrow exec eligibility: arbitrary foreground shells
// can leave redirected or setsid descendants after their own successful exit.
export function workspaceRevalidationCandidate(tool, params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) return false;
  if (['read','write','edit','apply_patch'].includes(tool)) return true;
  return tool === 'exec' && inspectionRevalidationCandidate(params);
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
