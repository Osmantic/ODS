import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const uuid = '[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}';
const session = new RegExp(`^agent:pixel:cron:(${uuid}):run:(${uuid})$`);
const marker = new RegExp(`^${uuid}_${uuid}$`);
const directory = '.ods-scheduled-note-receipts';

export function recordScheduledNoteRequest(run, state = process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw')) {
  const match = typeof run.sessionKey === 'string' && session.exec(run.sessionKey);
  if (!match || match[0] !== run.sessionKey || run.sessionId !== match[2] || run.id !== match[2]) return;
  // This ODS record contains no note, approval ID or current permission. Only
  // the native scheduler can supply the matching job and run for history.
  try {
    if (!path.isAbsolute(state) || fs.realpathSync(state) !== state) return;
    const parent = fs.lstatSync(state);
    if (!parent.isDirectory() || parent.uid !== process.getuid() || (parent.mode & 0o022)) return;
    const dir = path.join(state, directory);
    try { fs.mkdirSync(dir, {mode:0o700}); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    const info = fs.lstatSync(dir);
    if (!info.isDirectory() || info.uid !== process.getuid() || (info.mode & 0o077)) return;
    const file = path.join(dir, match[1] + '_' + match[2]);
    try {
      const fd = fs.openSync(file, 'wx', 0o600);
      try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
    } catch (error) { if (error.code === 'EEXIST') return; throw error; }

    // Keep recent receipts. Concurrent writers create independent markers.
    const names = fs.readdirSync(dir).filter(name => name.length === 73 && marker.test(name));
    if (names.length > 4096) {
      const files = names.map(name => ({name, info:fs.lstatSync(path.join(dir,name), {throwIfNoEntry:false})}))
        .filter(entry => entry.info?.isFile() && entry.info.uid === process.getuid() && entry.info.size === 0);
      files.sort((a,b) => a.info.mtimeMs - b.info.mtimeMs || a.name.localeCompare(b.name));
      for (const entry of files.slice(0, Math.max(0, files.length - 4096))) {
        try { fs.unlinkSync(path.join(dir,entry.name)); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      }
    }
    const fd = fs.openSync(dir, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
    try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  } catch { /* A history receipt is optional. It never grants or repeats a write. */ }
}
