// This is diagnostic advice only. Never change source bytes or execution truth.
// The escaped-line-break repair is a command the model may run with exec; it
// checks itself (file digest, offsets, parse before and after), and Pixel
// never runs it.
import { createHash } from 'node:crypto';

function pythonCommand(command) {
  const python = /^\s*(?:\/[^\s'";|&]+\/)?python(?:3(?:\.\d+)?)?(?=\s|$)/;
  if (python.test(command)) return true;
  // Recognize one literal relative workspace directory and one Python command.
  // This is not a shell executor: expansions, traversal, redirection, heredocs,
  // and additional commands cannot establish this diagnostic's provenance.
  const wrapped = /^[ \t]*cd[ \t]+(?:'([A-Za-z0-9_./ -]+)'|"([A-Za-z0-9_./ -]+)"|([A-Za-z0-9_./-]+))[ \t]+&&[ \t]+([\s\S]+)$/.exec(command);
  if (!wrapped) return false;
  const directory = wrapped[1] ?? wrapped[2] ?? wrapped[3];
  if (directory.startsWith('/') || directory.startsWith('-') || directory.split('/').includes('..')) return false;
  const invocation = wrapped[4];
  if (!python.test(invocation)) return false;
  let quote;
  for (let index = 0; index < invocation.length; index++) {
    const char = invocation[index];
    if (char === '\0' || char === '\r') return false;
    if (quote === "'") { if (char === "'") quote = undefined; continue; }
    if (char === '$' || char === '`') return false;
    if (quote === '"') {
      if (char === '\\') {
        if (++index >= invocation.length || /[\r\n\0]/.test(invocation[index])) return false;
      } else if (char === '"') quote = undefined;
      continue;
    }
    if (char === "'" || char === '"') quote = char;
    else if (/[;&|<>\n\\#(){}*?\[]/.test(char)) return false;
  }
  return quote === undefined;
}

function failedExecutionText(result) {
  if (result?.details?.status !== 'completed' ||
      !Number.isInteger(result.details.exitCode) || result.details.exitCode === 0) return undefined;
  return typeof result.details.aggregated === 'string' ? result.details.aggregated
    : (result.content ?? []).filter(block => block?.type === 'text' && typeof block.text === 'string')
      .map(block => block.text).join('\n');
}

// Python reports the first backslash outside a string that does not end its
// line. Scan the recorded source the same way and return the escape letters
// (n, t) found in code on the reported line, only when this scan agrees that
// the first such backslash is on that line and starts \n or \t. Escapes in
// strings (including raw, byte and triple-quoted strings) and comments never count.
function strayCodeEscapes(source, target) {
  let line = 1, quote = '', first, found = '';
  const nextLine = () => ++line > target;
  for (let index = 0; index < source.length; index++) {
    const char = source[index];
    if (char === '\n') {
      if (quote.length === 1) return undefined;
      if (nextLine()) break;
    } else if (quote) {
      if (char === '\\' && source[++index] === '\n' && nextLine()) break;
      else if (char !== '\\' && source.startsWith(quote, index)) { index += quote.length - 1; quote = ''; }
    } else if (char === '#') {
      while (index + 1 < source.length && source[index + 1] !== '\n') index++;
    } else if (char === '"' || char === "'") {
      quote = source.startsWith(char.repeat(3), index) ? char.repeat(3) : char;
      index += quote.length - 1;
    } else if (char === '\\') {
      const escape = source[index + 1];
      if (escape === '\n') {
        index++;
        if (nextLine()) break;
        continue;
      }
      if (line !== target) return undefined;
      first ??= escape;
      if ((escape === 'n' || escape === 't') && !found.includes(escape)) found += escape;
    }
  }
  return (first === 'n' || first === 't') ? [...found].sort().join('') : undefined;
}

const ESCAPED_LINE_BREAK_KINDS = {n: ['\\n', 'line breaks'], t: ['\\t', 'tabs'], nt: ['\\n and \\t', 'line breaks and tabs']};
const WORKSPACE_FILE_COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
// Beyond this many places the one-line command gets long; give the note only.
export const MAX_ESCAPED_LINE_BREAK_REPAIRS = 64;
export const ESCAPED_LINE_BREAK_WRITE_NEXT = 'Then continue with the task.';
export const ESCAPED_LINE_BREAK_RERUN_NEXT = 'Then rerun the same test command.';

// Whole-file scan (fleet: Mac mini, Qwen3.5-9B, round 078, where the
// traceback named only line 132 of the 8 places on lines 132 and 302). Every
// literal \n or \t written in code, outside strings and comments, with its
// code-point offset (Python's string index) and line. A conservative lexer:
// any other backslash in code, an unterminated string, a carriage return or
// an f-string that closes inside a replacement field (Python 3.12 nested
// quotes, which this lexer does not follow) makes the scan unsure, and it
// returns undefined. Escapes inside strings (including raw, byte and
// triple-quoted strings) never count.
export function strayEscapeOffsets(source) {
  if (typeof source !== 'string' || source.includes('\r')) return undefined;
  const chars = Array.from(source);
  const found = [];
  let quote = '', line = 1, formatted = false, depth = 0;
  for (let index = 0; index < chars.length; index++) {
    const char = chars[index];
    if (char === '\n') {
      if (quote.length === 1) return undefined;
      line++;
    } else if (quote) {
      if (char === '\\') {
        const next = chars[++index];
        if (next === undefined) return undefined;
        if (next === '\n') line++;
      } else if (char === quote[0] && chars.slice(index, index + quote.length).join('') === quote) {
        if (depth) return undefined;
        index += quote.length - 1;
        quote = '';
      } else if (formatted && char === '{') {
        if (!depth && chars[index + 1] === '{') index++;
        else depth++;
      } else if (formatted && char === '}' && depth) depth--;
    } else if (char === '#') {
      while (index + 1 < chars.length && chars[index + 1] !== '\n') index++;
    } else if (char === '"' || char === "'") {
      let start = index;
      while (start > 0 && index - start < 2 && /[rRbBfFuUtT]/.test(chars[start - 1])) start--;
      formatted = /[fFtT]/.test(chars.slice(start, index).join('')) && !/[\w]/.test(chars[start - 1] ?? '');
      depth = 0;
      quote = chars[index + 1] === char && chars[index + 2] === char ? char.repeat(3) : char;
      index += quote.length - 1;
    } else if (char === '\\') {
      const next = chars[index + 1];
      if (next === '\n') { index++; line++; }
      else if (next === 'n' || next === 't') { found.push({offset: index, line, kind: next}); index++; }
      else return undefined;
    }
  }
  return quote ? undefined : found;
}

// The places to repair in one written Python file and, when its path and
// count allow, one command that repairs exactly those places. The command is
// one line, and its quoted program has no backslash, dollar sign, backtick,
// double quote or exclamation mark. It finds the file by name from the
// file's own directory or from the workspace root, and changes nothing
// unless the file digest still matches, every offset still holds a
// backslash followed by n or t, the file does not already parse, and the
// result parses.
export function escapedLineBreakScan(file, source) {
  const found = strayEscapeOffsets(source);
  if (!found?.length || typeof file !== 'string') return undefined;
  const scan = {file, lines: [...new Set(found.map(item => item.line))], count: found.length,
    kinds: [...new Set(found.map(item => item.kind))].sort().join('')};
  const parts = file.split('/');
  if (!file.endsWith('.py') || file.length > 512 || !parts.every(part => WORKSPACE_FILE_COMPONENT.test(part)) ||
      found.length > MAX_ESCAPED_LINE_BREAK_REPAIRS) return scan;
  const directory = parts.slice(0, -1).join('/');
  const digest = createHash('sha256').update(Buffer.from(source, 'utf8')).digest('hex').slice(0, 16);
  const offsets = found.map(({offset, kind}) => kind === 'n' ? offset : -offset - 1).reverse().join(',');
  const candidates = directory ? `'${parts.at(-1)}','${file}'` : `'${file}'`;
  const body = "import hashlib,ast,functools,os;m=' already parses; not modified';" +
    `P=[q for q in (${candidates},) if os.path.isfile(q) and hashlib.sha256(open(q,'rb').read()).hexdigest()[:16]=='${digest}'];` +
    "assert P,'file missing or changed; not modified';p=P[0];s=open(p,'rb').read().decode('utf-8');" +
    `O=(${offsets},);` +
    "assert all(s[max(i,-i-1):max(i,-i-1)+2]==chr(92)+('n' if i>=0 else 't') for i in O),'offsets altered; not modified';" +
    "exec(chr(10).join(['try:',' ast.parse(s)','except SyntaxError: pass','else: raise SystemExit(p+m)']));" +
    "r=functools.reduce(lambda t,i:t[:max(i,-i-1)]+chr(10 if i>=0 else 9)+t[max(i,-i-1)+2:],O,s);ast.parse(r);" +
    `open(p,'wb').write(r.encode('utf-8'));print('replaced ${found.length} escaped line breaks in',p)`;
  if (/[\\$`"!\r\n]/.test(body)) return scan;
  return {...scan, workdir: directory ? `/workspace/${directory}` : '/workspace', command: `python3 -c "${body}"`};
}

function lineList(lines) {
  if (lines.length === 1) return `line ${lines[0]}`;
  if (lines.length > 6) return `lines ${lines.slice(0, 6).join(', ')} and ${lines.length - 6} more`;
  return `lines ${lines.slice(0, -1).join(', ')} and ${lines.at(-1)}`;
}

// Model-facing text for a scan; `next` names the step after the repair.
export function escapedLineBreakText(scan, next) {
  const [escape, belong] = ESCAPED_LINE_BREAK_KINDS[scan.kinds];
  const places = `${scan.count} place${scan.count === 1 ? '' : 's'}`;
  const found = `[ODS Pixel Python syntax] ${scan.file} has literal ${escape} outside string literals on ${lineList(scan.lines)} ` +
    `(${places}): the tool call escaped them twice, and re-typing the file repeats it. Do not rewrite the file.`;
  if (!scan.command) {
    return `${found} Replace only those escape sequences with real ${belong} using targeted edits; keep escapes inside string literals unchanged. ${next}`;
  }
  return `${found} Run exactly this one command with exec (workdir ${scan.workdir}): ${scan.command} ` +
    `It changes only those ${places}, refuses if the file changed or already parses, and writes only a result that parses. ${next}`;
}

// Diagnostic only: name bytes this run recorded for a written file; never
// rewrite them. The traceback must name that file and echo the recorded line.
// With `readCurrent` (the confined current file text), the text carries the
// repair command for every place in the file, when the current bytes still
// show the reported line and the whole-file scan includes it.
export function escapedLineBreakDiagnosis(result, writtenContent, workspaceRoot, readCurrent) {
  const text = failedExecutionText(result);
  if (typeof text !== 'string' || text.length > 65536 || typeof writtenContent?.get !== 'function') return undefined;
  const lines = text.split(/\r?\n/);
  const errors = lines.flatMap((line, index) =>
    /^SyntaxError: unexpected character after line continuation character\s*$/.test(line) ? [index] : []);
  if (errors.length !== 1 || errors[0] < 3 || !/^\s*\^+\s*$/.test(lines[errors[0] - 1])) return undefined;
  const frame = /^\s*File "(\/[^"\r\n]+)", line ([1-9][0-9]{0,6})\s*$/.exec(lines[errors[0] - 3]);
  const echoed = lines[errors[0] - 2].trim();
  if (!frame || !echoed) return undefined;
  const root = typeof workspaceRoot === 'string' ? workspaceRoot.replace(/\/+$/, '') : '';
  const file = frame[1].startsWith('/workspace/') ? frame[1].slice('/workspace/'.length)
    : root.startsWith('/') && frame[1].startsWith(`${root}/`) ? frame[1].slice(root.length + 1) : undefined;
  if (!file || file.length > 512 || !file.split('/').every(part => WORKSPACE_FILE_COMPONENT.test(part))) return undefined;
  const content = writtenContent.get(file);
  if (typeof content !== 'string' || /\r(?!\n)/.test(content)) return undefined;
  const source = content.replace(/\r\n/g, '\n');
  const lineNumber = Number(frame[2]);
  if (source.split('\n')[lineNumber - 1]?.trim() !== echoed) return undefined;
  const kind = ESCAPED_LINE_BREAK_KINDS[strayCodeEscapes(source, lineNumber)];
  if (!kind) return undefined;
  let current;
  try { current = typeof readCurrent === 'function' ? readCurrent(file) : undefined; } catch { current = undefined; }
  const scan = typeof current === 'string' && current.split('\n')[lineNumber - 1]?.trim() === echoed
    ? escapedLineBreakScan(file, current) : undefined;
  if (scan?.command && scan.lines.includes(lineNumber)) {
    return {file, content, text: escapedLineBreakText(scan, ESCAPED_LINE_BREAK_RERUN_NEXT)};
  }
  return {file, content, text: `[ODS Pixel Python syntax] Line ${lineNumber} of ${file} contains literal ${kind[0]} escape sequences ` +
    `outside string literals where real ${kind[1]} belong (the file content was escaped twice). ` +
    `Do not rewrite the file; re-typing repeats the escaping. Replace only those escape sequences with real ${kind[1]} ` +
    'using a targeted edit; keep escapes inside string literals unchanged.'};
}

export function pythonSyntaxGuidance(params, result) {
  if (typeof params?.command !== 'string' || !pythonCommand(params.command)) return undefined;
  const text = failedExecutionText(result);
  if (typeof text !== 'string' ||
      !/^SyntaxError: unexpected character after line continuation character\s*$/m.test(text) ||
      !/^\s*File "[^"\r\n]+", line \d+/m.test(text) || !/^\s*\^+\s*$/m.test(text)) return undefined;
  return '[ODS Pixel Python syntax] Inspect the reported line and nearby source bytes with repr before editing. ' +
    'For <stdin> or <string>, inspect the submitted Python snippet. A literal backslash-n outside a string may need a real line break; ' +
    'escaped quote bytes outside a string can cause this error too. Confirm the actual bytes first. ' +
    'Preserve valid escapes inside strings; never globally replace them. Make one targeted correction, preserve existing files and assertions, ' +
    'then rerun the same failed command within the remaining repair budget. Parsing failure does not verify behavior.';
}
