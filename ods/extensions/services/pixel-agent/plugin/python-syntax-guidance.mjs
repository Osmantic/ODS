// This is diagnostic advice only. Never change source bytes or execution truth.
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

// Diagnostic only: name bytes this run recorded for a written file; never
// rewrite them. The traceback must name that file and echo the recorded line.
export function escapedLineBreakDiagnosis(result, writtenContent, workspaceRoot) {
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
  if (!file || file.length > 512 || !file.split('/').every(part => /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(part))) return undefined;
  const content = writtenContent.get(file);
  if (typeof content !== 'string' || /\r(?!\n)/.test(content)) return undefined;
  const source = content.replace(/\r\n/g, '\n');
  const lineNumber = Number(frame[2]);
  if (source.split('\n')[lineNumber - 1]?.trim() !== echoed) return undefined;
  const kind = ESCAPED_LINE_BREAK_KINDS[strayCodeEscapes(source, lineNumber)];
  if (!kind) return undefined;
  return {file, content, text: `[ODS Pixel Python syntax] Line ${lineNumber} of ${file} contains literal ${kind[0]} escape sequences ` +
    `outside string literals where real ${kind[1]} belong (the file content was escaped twice). ` +
    `Rewrite that file with real ${kind[1]}; keep escapes inside string literals unchanged.`};
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
