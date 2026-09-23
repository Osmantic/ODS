// Syntax opts a completed call into host byte revalidation. It never proves
// that shell/PATH/profile execution was read-only.
export function inspectionRevalidationCandidate(params) {
  if (!params || typeof params.command !== 'string' || params.background === true || params.pty === true || params.env != null) return false;
  if (Object.keys(params).some(key=>!['command','workdir','timeout','yieldMs','background','pty'].includes(key))) return false;
  const command = params.command;
  if (command.length > 1024 || !/^[A-Za-z0-9_./ -]+$/.test(command)) return false;
  const tokens = command.trim().split(/ +/);
  if (tokens[0] === 'pwd') return tokens.length === 1 || tokens.length === 2 && /^-[LP]$/.test(tokens[1]);
  if (tokens[0] !== 'ls') return false;
  return tokens.slice(1).every(token=>token === '--' || /^-[alhdF]+$/.test(token) ||
    !token.startsWith('-') && /^[A-Za-z0-9_./][A-Za-z0-9_./-]*$/.test(token));
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
