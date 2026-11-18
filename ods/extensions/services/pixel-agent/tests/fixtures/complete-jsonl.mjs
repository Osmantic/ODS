import {readFileSync} from 'node:fs';

// A live append can end mid-record. The newline commits a JSONL record;
// leave an unfinished tail for the next poll, but reject malformed full lines.
export function readCompleteJsonl(path) {
  let text;
  try {
    text = readFileSync(path, 'utf8');
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
  const end = text.lastIndexOf('\n');
  if (end < 0) return [];
  return text.slice(0, end).split('\n').filter(Boolean).map(JSON.parse);
}
