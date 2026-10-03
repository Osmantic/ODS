// Deterministic check for one common shell mistake: `CMD 2>&1 > FILE`.
// Redirections apply left to right, so `2>&1` first points stderr at the
// current stdout (the exec output) and only then is stdout moved to FILE.
// Test runners such as unittest write to stderr, so the model sees the
// output in its exec result while FILE stays empty or partial.
//
// The note is informational: the command is never rewritten or blocked.
// Fixed text keeps tool results byte-stable across calls.
export const REDIRECT_ORDER_NOTE =
  "Note: `2>&1 > FILE` sends stderr to this output, not to FILE; FILE receives only stdout. " +
  "Use `> FILE 2>&1` to capture both.";

const MAX_COMMAND_LENGTH = 16384;
const BAIL = Symbol("bail");
const OPEN_KEYWORDS = new Set(["if", "while", "until", "for", "select", "{"]);
const CLOSE_KEYWORDS = new Set(["fi", "done", "}"]);
const PREFIX_KEYWORDS = new Set(["then", "else", "elif", "do", "!", "time"]);
const WORD_END = new Set([" ", "\t", "\n", ";", "&", "|", "(", ")", "<", ">"]);

// True only when some top-level simple command, whose stdout is not piped or
// backgrounded, ends with stderr on the original stdout via a dup of fd 1 and
// stdout on a regular file path. Anything the scanner does not fully
// understand (backticks, process substitution, case, unterminated quotes,
// ambiguous dups) yields false: a missed note is preferable to a wrong one.
export function stderrRedirectedBeforeStdoutFile(command) {
  if (typeof command !== "string" || !command || command.length > MAX_COMMAND_LENGTH) return false;
  try {
    return scan(command) === true;
  } catch {
    return false;
  }
}

function scan(s) {
  const n = s.length;
  let i = 0;
  let depth = 0;
  let doubleBracket = false;
  let found = false;
  const heredocs = [];
  let cmd = fresh();

  function fresh() {
    return {words: 0, redirects: 0, fds: new Map([[1, "out"], [2, "err"]]), unsure: false};
  }
  function end(op) {
    const {fds} = cmd;
    if (!cmd.unsure && !doubleBracket && depth === 0 && op !== "|" && op !== "&" &&
        fds.get(1) === "file" && fds.get(2) === "out") found = true;
    cmd = fresh();
  }

  // Scan a quoted or substituted span; returns the index after it or BAIL.
  function skipSingle(j) {
    const close = s.indexOf("'", j);
    return close < 0 ? BAIL : close + 1;
  }
  function skipAnsiC(j) {
    for (; j < n; j++) {
      if (s[j] === "\\") j++;
      else if (s[j] === "'") return j + 1;
    }
    return BAIL;
  }
  function skipDouble(j) {
    while (j < n) {
      const c = s[j];
      if (c === "\\") j += 2;
      else if (c === '"') return j + 1;
      else if (c === "`") return BAIL;
      else if (c === "$" && (s[j + 1] === "(" || s[j + 1] === "{")) {
        j = s[j + 1] === "(" ? skipParens(j + 2) : skipBraces(j + 2);
        if (j === BAIL) return BAIL;
      } else j++;
    }
    return BAIL;
  }
  // `$(...)` and `$((...))`: opaque, balanced. Inner commands are not
  // analyzed; constructs whose parentheses do not balance lexically bail.
  function skipParens(j) {
    const start = j;
    let level = 1;
    while (j < n) {
      const c = s[j];
      if (c === "\\") j += 2;
      else if (c === "'") j = skipSingle(j + 1);
      else if (c === '"') j = skipDouble(j + 1);
      else if (c === "`") return BAIL;
      else if (c === "#" && /[\s(;&|]/.test(s[j - 1] ?? " ")) return BAIL;
      else if (c === "(") { level++; j++; }
      else if (c === ")") {
        level--; j++;
        if (level === 0) {
          const inner = s.slice(start, j - 1);
          return /\bcase\b|<</.test(inner) ? BAIL : j;
        }
      } else j++;
      if (j === BAIL) return BAIL;
    }
    return BAIL;
  }
  function skipBraces(j) {
    let level = 1;
    while (j < n) {
      const c = s[j];
      if (c === "\\") j += 2;
      else if (c === "'") j = skipSingle(j + 1);
      else if (c === '"') j = skipDouble(j + 1);
      else if (c === "`") return BAIL;
      else if (c === "$" && s[j + 1] === "(") j = skipParens(j + 2);
      else if (c === "{") { level++; j++; }
      else if (c === "}") { level--; j++; if (level === 0) return j; }
      else j++;
      if (j === BAIL) return BAIL;
    }
    return BAIL;
  }

  // Read one shell word at i. `text` is the quote-removed value (expansions
  // kept verbatim); `quoted` records any quoting or escaping.
  function readWord() {
    let text = "";
    let quoted = false;
    const start = i;
    while (i < n && !WORD_END.has(s[i])) {
      const c = s[i];
      if (c === "\\") {
        if (s[i + 1] === "\n") { i += 2; continue; }
        if (i + 1 >= n) return BAIL;
        text += s[i + 1]; quoted = true; i += 2;
      } else if (c === "'") {
        const next = skipSingle(i + 1);
        if (next === BAIL) return BAIL;
        text += s.slice(i + 1, next - 1); quoted = true; i = next;
      } else if (c === '"') {
        const next = skipDouble(i + 1);
        if (next === BAIL) return BAIL;
        text += s.slice(i + 1, next - 1); quoted = true; i = next;
      } else if (c === "`") {
        return BAIL;
      } else if (c === "$" && s[i + 1] === "'") {
        const next = skipAnsiC(i + 2);
        if (next === BAIL) return BAIL;
        text += s.slice(i, next); quoted = true; i = next;
      } else if (c === "$" && (s[i + 1] === "(" || s[i + 1] === "{")) {
        const next = s[i + 1] === "(" ? skipParens(i + 2) : skipBraces(i + 2);
        if (next === BAIL) return BAIL;
        text += s.slice(i, next); i = next;
      } else {
        text += c; i++;
      }
    }
    return i === start ? undefined : {text, quoted};
  }

  function skipBlanks() {
    while (i < n && (s[i] === " " || s[i] === "\t" || (s[i] === "\\" && s[i + 1] === "\n"))) {
      i += s[i] === "\\" ? 2 : 1;
    }
  }

  // Consume pending here-document bodies after the newline at i - 1.
  function skipHeredocBodies() {
    for (const {delimiter, stripTabs} of heredocs.splice(0)) {
      for (;;) {
        if (i >= n) return;
        let lineEnd = s.indexOf("\n", i);
        if (lineEnd < 0) lineEnd = n;
        let line = s.slice(i, lineEnd);
        if (stripTabs) line = line.replace(/^\t+/, "");
        i = Math.min(n, lineEnd + 1);
        if (line === delimiter) break;
      }
    }
  }

  function redirect(fdText) {
    const explicitFd = fdText === undefined ? undefined : Number(fdText);
    let op;
    for (const candidate of ["<<<", "<<-", "<<", "<&", "<>", ">>", ">|", ">&", "<", ">"]) {
      if (s.startsWith(candidate, i)) { op = candidate; break; }
    }
    if ((op === "<" || op === ">") && s[i + 1] === "(") return BAIL; // process substitution
    i += op.length;
    skipBlanks();
    const word = readWord();
    if (word === BAIL || word === undefined) return BAIL;
    cmd.redirects++;
    const {fds} = cmd;
    const input = op.startsWith("<");
    const fd = explicitFd ?? (input ? 0 : 1);
    if (op === "<<" || op === "<<-") {
      heredocs.push({delimiter: word.text, stripTabs: op === "<<-"});
      return;
    }
    if (op === ">&" || op === "<&") {
      if (!word.quoted && /^[0-9]+$/.test(word.text)) {
        const source = Number(word.text);
        fds.set(fd, fds.get(source) ?? (source === 0 ? "in" : "unknown"));
      } else if (!word.quoted && word.text === "-") {
        fds.set(fd, "closed");
      } else if (op === ">&" && explicitFd === undefined) {
        // `>&FILE` is bash shorthand for `&> FILE`.
        fds.set(1, "file"); fds.set(2, "file");
      } else {
        cmd.unsure = true;
      }
      return;
    }
    if (input) {
      if (fd === 1 || fd === 2) cmd.unsure = true;
      return;
    }
    // Device targets (`2>&1 >/dev/null | grep`) are deliberate fd plumbing.
    fds.set(fd, word.text.startsWith("/dev/") ? "device" : "file");
  }

  while (i < n) {
    const c = s[i];
    if (c === " " || c === "\t") { i++; continue; }
    if (c === "\\" && s[i + 1] === "\n") { i += 2; continue; }
    if (c === "\n") {
      end("\n"); i++;
      skipHeredocBodies();
      continue;
    }
    if (c === "#") {
      while (i < n && s[i] !== "\n") i++;
      continue;
    }
    if (s.startsWith(";;", i) || s.startsWith(";&", i)) return false;
    if (s.startsWith("&&", i) || s.startsWith("||", i)) { end(s.slice(i, i + 2)); i += 2; continue; }
    if (s.startsWith("|&", i)) { end("|"); i += 2; continue; }
    if (c === ";" || c === "|") { end(c); i++; continue; }
    if (s.startsWith("&>", i)) {
      i += s[i + 2] === ">" ? 3 : 2;
      skipBlanks();
      const word = readWord();
      if (word === BAIL || word === undefined) return false;
      cmd.redirects++;
      cmd.fds.set(1, "file"); cmd.fds.set(2, "file");
      continue;
    }
    if (c === "&") { end("&"); i++; continue; }
    if (c === "(") { end("("); depth++; i++; continue; }
    if (c === ")") {
      end(")"); depth--; i++;
      if (depth < 0) return false;
      continue;
    }
    const fdPrefix = /^[0-9]+(?=[<>])/.exec(s.slice(i, i + 12));
    if (fdPrefix || c === "<" || c === ">") {
      if (fdPrefix) i += fdPrefix[0].length;
      if (redirect(fdPrefix?.[0]) === BAIL) return false;
      continue;
    }
    const word = readWord();
    if (word === BAIL || word === undefined) return false;
    const commandPosition = cmd.words === 0 && cmd.redirects === 0 && !word.quoted;
    if (commandPosition && word.text === "case") return false;
    // Inside [[ ]], < and > compare strings; they are not redirections.
    if (commandPosition && word.text === "[[") { doubleBracket = true; cmd.unsure = true; cmd.words++; continue; }
    if (doubleBracket && !word.quoted && word.text === "]]") { doubleBracket = false; cmd.words++; continue; }
    if (commandPosition && OPEN_KEYWORDS.has(word.text)) { depth++; continue; }
    if (commandPosition && CLOSE_KEYWORDS.has(word.text)) {
      depth--;
      if (depth < 0) return false;
      // The keyword itself takes the place of a command word: redirections
      // that follow it apply to the whole compound command.
      cmd.words++;
      continue;
    }
    if (commandPosition && PREFIX_KEYWORDS.has(word.text)) continue;
    cmd.words++;
  }
  if (doubleBracket) return false;
  end("eof");
  return found;
}
