// Fixed private publication transport supplied by owner-approved activation.
// Never pass an HTTP/model-selected command, path or owner API credential here.
import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';

export function createHandoffOwnerAdapter({command, directory, timeoutSeconds = 60}) {
  if (process.platform === 'win32' || !Array.isArray(command) || !command.length ||
      !isAbsolute(command[0]) || command.some(arg => typeof arg !== 'string' || arg.includes('\0')) ||
      typeof directory !== 'string' || !isAbsolute(directory) || directory.includes('\0') ||
      !Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 120) {
    throw new Error('ODS handoff owner transport unavailable');
  }
  const executable = [...command];
  return async function authorizeHandoff({checkpoint, checkpointDigest, signal}) {
    if (!signal || signal.aborted) return null;
    const checkpointJson = JSON.stringify(checkpoint);
    if (Buffer.byteLength(checkpointJson) > 2 * 1024 * 1024) return null;
    const body = Buffer.from(JSON.stringify({schemaVersion: 1, checkpointJson, checkpointDigest, timeoutSeconds}));
    if (body.length > 5 * 1024 * 1024) return null;
    const prefix = Buffer.alloc(4); prefix.writeUInt32BE(body.length);
    const child = spawn(executable[0], [...executable.slice(1), '--provider-directory', directory], {
      cwd: '/', stdio: ['pipe','pipe','pipe'], detached: true,
      env: {PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', PYTHONDONTWRITEBYTECODE: '1'},
    });
    return await new Promise(resolve => {
      let bytes = Buffer.alloc(0), stderrBytes = 0, failed = false, killTimer;
      const stop = () => {
        if (failed) return;
        failed = true; child.stdin.end();
        killTimer = setTimeout(() => child.kill('SIGKILL'), 3000);
      };
      signal.addEventListener('abort', stop, {once: true});
      if (signal.aborted) stop();
      const timer = setTimeout(stop, (timeoutSeconds + 12) * 1000);
      child.stdin.on('error', stop); child.on('error', stop);
      child.stderr.on('data', part => {stderrBytes += part.length; if (stderrBytes > 8192) stop();});
      child.stdout.on('data', part => {
        if (failed) return;
        if (bytes.length + part.length > 8196) {stop(); return;}
        bytes = Buffer.concat([bytes, part]);
        if (bytes.length > 8196 || bytes.length >= 4 && (bytes.readUInt32BE(0) < 1 || bytes.readUInt32BE(0) > 8192)) stop();
      });
      child.on('close', code => {
        clearTimeout(timer); clearTimeout(killTimer); signal.removeEventListener('abort', stop);
        if (failed || signal.aborted || code !== 0 || bytes.length < 4 || bytes.readUInt32BE(0) !== bytes.length - 4) {
          resolve(null); return;
        }
        try {
          const receipt = JSON.parse(bytes.subarray(4).toString('utf8'));
          resolve(receipt.checkpointDigest === checkpointDigest ? receipt : null);
        } catch {resolve(null);}
      });
      if (!failed) child.stdin.write(Buffer.concat([prefix, body]));
    });
  };
}
