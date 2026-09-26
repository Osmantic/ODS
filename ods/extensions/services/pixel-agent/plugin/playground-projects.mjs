// Project routing is an owner-workspace convention, never a new access grant.
// The core tools still enforce their normal sandbox and permission checks.
import * as fs from 'node:fs';
import path from 'node:path';
import {createHash, randomBytes} from 'node:crypto';

const LIMIT = 256;
const MAX_STATE_BYTES = 2048;
const STATE_DIRECTORY = '.ods-projects';
const COMPONENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
const GENERIC = /^(?:playground|project|projeto|app|application|site|website|web|game|jogo|public|src|source|build|dist|assets|static|css|js|test|tests|folder|new-project)$/i;
const LOCAL_FOLDERS = /^(?:src|source|public|assets|static|styles?|css|js|scripts?|tests?|docs?|lib|components|build|dist)$/i;
const CORRECTION = 'For a new project, use a workspace-relative path such as Playground/snake-game/index.html or Playground/weather-tool/main.py. Omit host home/workspace directory prefixes. Use that same descriptive folder for every project file and for preview publication. Do not use a bare filename or a generic src/public/project folder as the project name.';
// Names of a role, manifest, test, sample or scratch file, not a project.
// Matched on the lowercase kebab form; such names never become a suggestion.
const ROLE_FOLDER = /^(?:index|main|app|script|program|tool|run|utils?|helpers?|common|lib|setup|readme|conftest|config|settings|constants|manage|server|client|cli|demo|example|sample|temp|tmp|scratch|(?:tests?|spec)(?:-.*)?|.*-(?:tests?|spec))$/;
// Only a program or page file names a new project. Manifests such as
// package.json, requirements.txt or Makefile, documents and data do not.
const PROJECT_FILE = /\.(?:py|js|mjs|cjs|ts|tsx|jsx|html?|sh|rb|go|rs|java|kt|swift|c|cc|cpp|cs|php|lua|pl|dart)$/i;
const NOT_RUN = 'Not run: write the first project file before running commands; write creates its folder, so mkdir is not needed.';

function parts(value) {
  if (typeof value !== 'string' || value.length > 512) return null;
  const result = value.split('/');
  return result.length <= 12 && result.every(part => COMPONENT.test(part) && !part.endsWith('.') && !RESERVED.test(part)) ? result : null;
}
function plainIntent(value) {
  return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, ' ')
    .replace(/^\s*>.*$/gm, ' ');
}
export function requestsNewPlaygroundProject(intent) {
  // Filenames and path components are operands, not project-category words.
  // For example, creating macos-tool-check/probe.txt is not creating a tool.
  const text = plainIntent(intent).replace(/\b[A-Za-z0-9_]+(?:[-./\\][A-Za-z0-9_]+)+\b/g, ' ');
  // Continuation quotes the old creation request, not a new reservation.
  // Core tool policy still controls every inspection and mutation.
  if (/^\s*(?:\/goal\s+)?Continue the goal from the preceding conversation using the existing work\./i.test(text)) return false;
  const clauses = text.split(/[;!?\n]|\.(?=\s|$)/);
  return clauses.some(clause =>
    (/\b(?:create|build|develop|design|implement|generate|write|crie|criar|cria|construa|construir|desenvolva|desenvolver|implemente|gere|escreva)\b/i.test(clause)
      || /\bmake\s+(?:me\s+)?(?:a|an|new|another)\b|\b(?:faca|fazer|faz)\s+(?:um|uma|novo|nova|outro|outra)\b/i.test(clause))
    && /\b(?:project|projeto|site|website|webpage|app|application|aplicativo|aplicacao|game|jogo|joguinho|dashboard|landing\s+page|tool|ferramenta|program|programa|script|utility|utilitario|calculator|calculadora|timer|cronometro)\b/i.test(clause)
    && !/\b(?:do\s+not|don['’]t|never|without|nao|nunca|sem)\s+(?:(?:please|por\s+favor)\s+)?(?:create|build|make|develop|design|implement|generate|write|crie|criar|cria|construa|construir|faca|fazer|desenvolva|desenvolver|implemente|gere|escreva)\b/i.test(clause)
    && !/\b(?:explain|describe|tutorial|explique|descreva)\b/i.test(clause)
    && !/\b(?:existing|current|previous|existente|atual|anterior)\s+(?:project|projeto|site|app|game|jogo)\b/i.test(clause)
    && !/\b(?:for|in|inside|into|on|to|para|nesse|neste|desse|deste|no|na)\s+(?:(?:this|that|the|my|our|esse|este|meu|nosso|o|a)\s+)*(?:app|site|game|jogo|project|projeto|application|aplicativo)\b/i.test(clause));
}

// Explicit owner operands win over this default. This intentionally errs on
// preserving a path: changing an explicitly requested location is worse than
// leaving one new project outside Playground.
// A bare directory name after "workspace directory" is still an explicit
// owner operand. Match an affirmative creation clause, not an example, quoted
// instruction, or prohibition. This preserves the path; it grants no access.
function namesWorkspaceDirectory(text) {
  return text.split(/[;!?\n]|\.(?=\s|$)/).some(clause => {
    if (!/^\s*(?:please\s+)?(?:create|build|make|develop|design|implement|generate|write)\b/i.test(clause)) return false;
    const match = /\b(?:in|inside|under)\s+(?:(?:a|an|the)\s+)?(?:(?:new|separate|empty)\s+)?workspace\s+(?:directory|folder)\s+(?:(?:called|named)\s+)?(?:"([A-Za-z0-9][A-Za-z0-9._-]{0,63})"|'([A-Za-z0-9][A-Za-z0-9._-]{0,63})'|`([A-Za-z0-9][A-Za-z0-9._-]{0,63})`|([A-Za-z0-9][A-Za-z0-9._-]{0,63}))(?=$|[\s,;.!?])/i.exec(clause);
    if (!match || /\b(?:not|never|avoid|don't|don’t|example|e\.g\.)\b/i.test(clause.slice(0,match.index))) return false;
    const name = match.slice(1).find(Boolean);
    return Boolean(parts(name));
  });
}

function ownerNamesPath(intent) {
  const text = plainIntent(intent).replace(/https?:\/\/\S+/g, ' ');
  return namesWorkspaceDirectory(text) || /(?:^|[\s`"'(])(?:\/?[A-Za-z0-9_.-]+[\/\\][A-Za-z0-9_./\\-]+|[A-Za-z]:[\/\\]\S+)(?=$|[\s`"'),;.!?])/i.test(text)
    || /\b(?:folder|directory|pasta|diretorio)\s+(?:called|named|chamad[ao])\s+[`"']?[A-Za-z0-9_.-]+/i.test(text)
    || /\b(?:in|inside|under|em|na|no)\s+(?:the\s+)?(?:folder|directory|pasta|diretorio)\s+(?!with\b|for\b|com\b|para\b)[`"']?[A-Za-z0-9_.-]+/i.test(text);
}
function relative(value, root) {
  if (typeof value !== 'string') return null;
  let text = value.replaceAll('\\', '/');
  const configured = root.replaceAll('\\', '/').replace(/\/$/, '');
  if (text.startsWith(`${configured}/`)) text = text.slice(configured.length + 1);
  else if (text.startsWith('/workspace/')) text = text.slice('/workspace/'.length);
  if (text.startsWith('./')) text = text.slice(2);
  return parts(text) ? text : null;
}
// A folder name that describes a project: valid, not generic, not a role,
// test or sample name, and with at least three letters.
function descriptiveFolder(name) {
  const kebab = String(name).replace(/[._]+/g, '-').toLowerCase();
  return parts(name)?.length === 1 && !GENERIC.test(kebab) && !ROLE_FOLDER.test(kebab)
    && (kebab.match(/[a-z]/g) ?? []).length >= 3;
}
// The first name under Playground that nothing uses yet: the name itself or,
// with suffixes, the -2, -3 ... names that reserveProject would choose. A
// suggestion never names an existing folder, which may be an owner project.
// Nothing is created. Null when the workspace cannot be checked, so advice
// never affects routing.
function freeFolder(root, name, suffixes = false) {
  try {
    const base = path.join(safeRoot(root),'Playground');
    try { const stat = fs.lstatSync(base); if (!stat.isDirectory() || stat.isSymbolicLink()) return null; }
    catch (error) { return error.code === 'ENOENT' ? name : null; }
    for (let i = 1; i <= (suffixes ? 1000 : 1); i++) {
      const candidate = i === 1 ? name : `${name.slice(0,58)}-${i}`;
      try { fs.lstatSync(path.join(base,candidate)); }
      catch (error) { return error.code === 'ENOENT' ? candidate : null; }
    }
  } catch { /* An unavailable workspace gets no suggestion. */ }
  return null;
}
// The folder already suggested in this run, while it is still unused. Later
// refusals repeat it, so one run never gets two different project folders.
function earlierFolder(state, root) {
  return state.suggestedFolder && freeFolder(root,state.suggestedFolder) === state.suggestedFolder ? state.suggestedFolder : null;
}
// Advice for a refused fresh-project write whose target is one file name,
// such as /workspace/PhotoRenamer.py -> Playground/photo-renamer/PhotoRenamer.py.
// Returns null when no descriptive, unused folder follows from the name. The
// caller's path is never rewritten: the model sends the suggested path itself.
function suggestedProjectPath(value, root, state) {
  if (typeof value !== 'string') return null;
  let text = value.replaceAll('\\', '/');
  const configured = root.replaceAll('\\', '/').replace(/\/$/, '');
  if (text.startsWith(`${configured}/`)) text = text.slice(configured.length + 1);
  else if (text.startsWith('/workspace/')) text = text.slice('/workspace/'.length);
  else if (text.startsWith('/')) text = text.slice(1);
  if (text.startsWith('./')) text = text.slice(2);
  if (parts(text)?.length !== 1) return null;
  const earlier = earlierFolder(state,root);
  if (earlier) return `Playground/${earlier}/${text}`;
  if (!PROJECT_FILE.test(text)) return null;
  const folder = text.slice(0, text.lastIndexOf('.')).replace(/([A-Z]+)([A-Z][a-z])/g, '$1-$2').replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/[._]+/g, '-').replace(/-{2,}/g, '-').replace(/^-|-$/g, '').toLowerCase();
  if (!descriptiveFolder(folder)) return null;
  const free = freeFolder(root,folder,true);
  return free ? `Playground/${free}/${text}` : null;
}
// The first descriptive, unused Playground/<name> that a refused command names.
function commandProjectFolder(command, root) {
  for (const match of command.matchAll(/(?:^|[\s"'`=:(/])Playground\/([A-Za-z0-9][A-Za-z0-9._-]{0,63})(?=$|[\s/"'`;&|)])/g)) {
    if (descriptiveFolder(match[1]) && freeFolder(root,match[1]) === match[1]) return match[1];
  }
  return null;
}
// A command whose first step is cd into the bound project directory, written
// relative to the workspace (optionally ./ and a trailing /), and followed by
// the end, a newline, &&, ; or ||. A single & or | runs the cd in a subshell,
// so those commands keep the project workdir.
function entersProject(command, directory) {
  const escaped = directory.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^\\s*cd\\s+(?:\\./)?${escaped}/?(?=[ \\t]*(?:$|\\n|&&|;|\\|\\|))`).test(command);
}
// Models misspell the Playground convention as playground/<name>/... or a
// rooted /playground/<name>/.... Read that first segment as the workspace
// Playground folder only when <name> is a descriptive project folder and no
// distinct entry with the model's spelling exists (a case-sensitive workspace
// can hold both). The result is still workspace-relative and every routing
// check applies to it as if the model had written it; it grants no location.
function playgroundSpelling(value, root, minimum) {
  if (typeof value !== 'string') return null;
  const text = value.replaceAll('\\', '/');
  const direct = relative(text,root);
  const target = direct ?? (/^\/playground\//i.test(text) ? relative(text.slice(1),root) : null);
  const segments = parts(target);
  if (!segments || !/^playground$/i.test(segments[0]) || segments.length < minimum || GENERIC.test(segments[1])) return null;
  const canonical = ['Playground',...segments.slice(1)].join('/');
  if (canonical === direct) return null;
  if (segments[0] !== 'Playground' && distinctSpelling(root,segments[0])) return null;
  return canonical;
}
// How the workspace holds an entry with this spelling: 'missing', 'same' (the
// Playground folder entry itself, as on a case-insensitive workspace) or
// 'distinct' (a separate directory or link on a case-sensitive workspace).
function workspaceEntry(root, name) {
  const base = safeRoot(root);
  const entry = value => { try { return fs.lstatSync(path.join(base,value)); } catch (error) { if (error.code === 'ENOENT') return null; throw error; } };
  const spelled = entry(name);
  if (!spelled) return 'missing';
  const folder = entry('Playground');
  return folder && folder.dev === spelled.dev && folder.ino === spelled.ino ? 'same' : 'distinct';
}
function distinctSpelling(root, name) {
  return workspaceEntry(root,name) === 'distinct';
}
// Drops trailing slashes but keeps a lone root slash. A loop, not a
// trailing-slash regex, which backtracks quadratically on a long slash run.
function trimSlashes(text) {
  let end = text.length;
  while (end > 1 && text[end - 1] === '/') end--;
  return text.slice(0, end);
}
// True when an exec workdir names the workspace root itself.
function workspaceCwd(workdir, root) {
  if (workdir === undefined) return true;
  if (typeof workdir !== 'string') return false;
  const text = trimSlashes(workdir.replaceAll('\\','/'));
  return text === '' || text === '.' || text === '/workspace' || text === trimSlashes(root.replaceAll('\\','/'));
}

// A small POSIX-style reader of exec text, used only to find the words a
// command uses as file paths; the command itself is never rewritten. Quotes
// are removed, $(...), ${...} and backquoted text stay literal inside their
// word, and comments and heredoc bodies are skipped. A backslash before a
// name character is kept so a Windows separator (playground\todo-app) stays.
const SHELL_OPERATOR = /&>>?|<<<|<<-|<<|<>|<&|>>|>\||>&|<|>|&&|\|\||;;|\|&|[;&|()]/y;
function shellTokens(command) {
  const tokens = [], heredocs = [], n = command.length;
  let i = 0, word = null;
  const add = text => { word = (word ?? '') + text; };
  const endWord = () => {
    if (word === null) return;
    const previous = tokens.at(-1);
    if (previous?.type === 'redirect' && (previous.op === '<<' || previous.op === '<<-')) heredocs.push({delimiter:word.replaceAll('\\',''), strip:previous.op === '<<-'});
    tokens.push({type:'word', text:word});
    word = null;
  };
  // From an opening ( or { through its matching close, kept literally.
  const enclosed = () => {
    const open = command[i], close = open === '(' ? ')' : '}', start = i;
    let depth = 0;
    while (i < n) {
      const c = command[i];
      if (c === '\\') { i += 2; continue; }
      if (c === "'") { const end = command.indexOf("'", i + 1); i = end < 0 ? n : end + 1; continue; }
      if (c === open) depth++;
      else if (c === close && --depth === 0) return command.slice(start, ++i);
      i++;
    }
    return command.slice(start);
  };
  // Single-quoted text without its quotes, or backquoted text with them.
  const through = close => {
    const end = command.indexOf(close, i + 1);
    const text = close === '`' ? command.slice(i, end < 0 ? n : end + 1) : command.slice(i + 1, end < 0 ? n : end);
    i = end < 0 ? n : end + 1;
    return text;
  };
  while (i < n) {
    const c = command[i];
    if (c === '\n') {
      endWord();
      tokens.push({type:'separator'});
      i++;
      for (const {delimiter, strip} of heredocs.splice(0)) {
        while (i < n) {
          const next = command.indexOf('\n', i);
          const end = next < 0 ? n : next;
          const line = command.slice(i, end).replace(/\r$/, '');
          i = end + 1;
          if ((strip ? line.replace(/^\t+/, '') : line) === delimiter) break;
        }
      }
      continue;
    }
    if (c === ' ' || c === '\t' || c === '\r') { endWord(); i++; continue; }
    if (c === '#' && word === null) { const end = command.indexOf('\n', i); i = end < 0 ? n : end; continue; }
    if (c === "'") { add(through("'")); continue; }
    if (c === '`') { add(through('`')); continue; }
    if (c === '$' && (command[i + 1] === '(' || command[i + 1] === '{')) { i++; add('$' + enclosed()); continue; }
    if (c === '"') {
      i++;
      let text = '';
      while (i < n && command[i] !== '"') {
        if (command[i] === '\\' && i + 1 < n && '"\\$`\n'.includes(command[i + 1])) { text += command[i + 1]; i += 2; }
        else if (command[i] === '$' && (command[i + 1] === '(' || command[i + 1] === '{')) { i++; text += '$' + enclosed(); }
        else if (command[i] === '`') text += through('`');
        else text += command[i++];
      }
      i++;
      add(text);
      continue;
    }
    if (c === '\\') {
      const next = command[i + 1];
      if (next === '\n') { i += 2; continue; }
      if (next !== undefined && /[A-Za-z0-9._-]/.test(next)) { add('\\'); i++; continue; }
      add(next ?? '');
      i += 2;
      continue;
    }
    SHELL_OPERATOR.lastIndex = i;
    const operator = SHELL_OPERATOR.exec(command)?.[0];
    if (operator) {
      const redirect = /[<>]/.test(operator);
      // A number directly before a redirection is its file descriptor.
      if (redirect && word !== null && /^\d+$/.test(word)) word = null;
      endWord();
      tokens.push(redirect ? {type:'redirect', op:operator} : {type:operator === '(' ? 'open' : operator === ')' ? 'close' : 'separator'});
      i += operator.length;
      continue;
    }
    add(c);
    i++;
  }
  endWord();
  return tokens;
}

// Programs whose arguments are text (messages, patterns), not paths. Their
// redirections are still paths.
const TEXT_PROGRAMS = /^(?:echo|printf|write-output|write-host|grep|egrep|fgrep|zgrep|rg|ag|ack|select-string|sls|findstr)$/;
const TEXT_OPTIONS = /^(?:-m|-am|--message|--grep|--author|--committer|--format|--pretty|--title|--body)$/;
const SHELLS = /^(?:bash|sh|zsh|dash|ksh)$/;
const INTERPRETERS = /^(?:python[0-9.]*|py|node|nodejs|deno|bun|perl|ruby|php|pwsh|powershell|osascript)$/;
const COMMAND_PREFIXES = /^(?:sudo|env|nohup|time|command|builtin|exec|nice|if|then|else|elif|while|until|do|!|\{|\}|\[\[)$/;
const CHANGE_DIRECTORY = /^(?:cd|pushd|chdir|set-location|sl)$/;

// The words an exec command uses as file paths, each marked with whether the
// shell cwd is still the exec cwd (the workspace root) where it is used. A
// path is an argument of a program that is not a text program, a long option
// value (--dir=...), a redirection target, or a cd target. A message or
// pattern argument, interpreter code and heredoc text are not paths. The
// commands inside sh -c text are read the same way.
function shellPathWords(command, root, rooted = true, depth = 0) {
  const words = [], stack = [];
  const start = () => ({program:null, subcommand:undefined, arguments:[], previous:'', skip:false, nested:false, ended:false});
  let current = start(), redirect = null;
  const finish = () => {
    if (current.program && CHANGE_DIRECTORY.test(current.program)) {
      const raw = current.arguments.find(value => !value.startsWith('-'));
      const target = raw === undefined ? undefined : trimSlashes(raw.replaceAll('\\','/'));
      if (!['.','$PWD','${PWD}','$(pwd)'].includes(target)) rooted = Boolean(target) && workspaceCwd(target,root);
    }
    current = start();
  };
  for (const token of shellTokens(command)) {
    if (token.type === 'separator') { finish(); redirect = null; continue; }
    if (token.type === 'open') { finish(); stack.push(rooted); continue; }
    if (token.type === 'close') { finish(); if (stack.length) rooted = stack.pop(); continue; }
    if (token.type === 'redirect') { redirect = token.op; continue; }
    const text = token.text;
    if (redirect) {
      const operator = redirect;
      redirect = null;
      // Heredoc delimiters, here-strings and descriptor duplication name no file.
      if (!operator.startsWith('<<') && !(operator.endsWith('&') && /^(?:\d+|-)$/.test(text))) words.push({text, rooted});
      continue;
    }
    const state = current;
    if (state.program === null) {
      if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(text) || COMMAND_PREFIXES.test(text) || text.startsWith('-')) continue;
      state.program = text.replaceAll('\\','/').split('/').at(-1).toLowerCase().replace(/\.(?:exe|cmd|bat|ps1)$/,'');
      continue;
    }
    const previous = state.previous;
    state.previous = text;
    state.arguments.push(text);
    if (TEXT_PROGRAMS.test(state.program) || state.subcommand === 'grep') continue;
    if (state.skip) {
      state.skip = false;
      if (state.nested && depth < 2) words.push(...shellPathWords(text,root,rooted,depth + 1));
      continue;
    }
    if (!state.ended && text === '--') { state.ended = true; continue; }
    if (!state.ended && text.startsWith('-') && text.length > 1) {
      const shellCode = SHELLS.test(state.program) && /^-[A-Za-z]*c$/.test(text);
      if (TEXT_OPTIONS.test(text) || shellCode || (INTERPRETERS.test(state.program) && /^(?:-c|-e|--eval|-command)$/i.test(text))) {
        state.skip = true;
        state.nested = shellCode;
        continue;
      }
      const equals = text.indexOf('=');
      if (text.startsWith('--') && equals > 0 && !TEXT_OPTIONS.test(text.slice(0,equals))) words.push({text:text.slice(equals + 1), rooted});
      continue;
    }
    if (state.program === 'git' && state.subcommand === undefined && !/^(?:-C|-c|--git-dir|--work-tree|--namespace)$/.test(previous)) {
      state.subcommand = text;
      continue;
    }
    words.push({text, rooted});
  }
  finish();
  return words;
}

// Shell text is never rewritten. A command run from the workspace root that
// uses this project's folder as a path with another Playground spelling gets
// the exact folder instead of an inferred cwd. Returns {block} with that
// correction, {literal} when the spelling resolves as written from the
// workspace root (the owner's separate folder, or the same folder on a
// case-insensitive workspace), or null. A rooted /playground/<name> is
// outside the workspace on every filesystem. Text such as a commit message,
// an echo or a grep pattern is not a path and keeps the ordinary rules.
function projectOperand(command, directory, root) {
  const name = directory.slice('Playground/'.length);
  const configured = root.replaceAll('\\','/').replace(/\/+$/,'');
  let literal = null;
  for (const word of shellPathWords(command,root)) {
    let value = word.text.replaceAll('\\','/');
    let absolute = false;
    if (value.startsWith(`${configured}/`)) value = value.slice(configured.length + 1);
    else if (value.startsWith('/workspace/')) value = value.slice('/workspace/'.length);
    else if (/^\/[^/]/.test(value)) { value = value.slice(1); absolute = true; }
    else if (word.rooted) value = value.replace(/^(?:\$\(pwd\)|\$\{?PWD\}?)\//,'').replace(/^(?:\.\/)+/,'');
    else continue;
    const match = /^(playground)\/([^/]+)(?:\/|$)/i.exec(value);
    if (!match || match[1] === 'Playground' || match[2] !== name) continue;
    const text = `${match[1]}/${name}`;
    if (absolute) return {block:`This project is in ${directory}. /${text} is an absolute path outside the workspace, not this project folder. Files already written for this project are saved in ${directory}. Set exec workdir to /workspace/${directory} and use filenames relative to that directory.`};
    if (workspaceEntry(root,match[1]) !== 'missing') { literal ??= {literal:text}; continue; }
    return {block:spellingReason(directory,text)};
  }
  return literal;
}
// Shown only when that spelling is missing while Playground exists, which
// happens only on a case-sensitive workspace.
function spellingReason(directory, text) {
  return `This project is in ${directory}. Use that exact spelling: ${text} is a different path on a case-sensitive workspace. Files already written for this project are saved in ${directory}. Set exec workdir to /workspace/${directory} and use filenames relative to that directory.`;
}
const PATCH_HEADER = /^(\*\*\* (?:(?:Add|Update|Delete) File|Move to): )([^\r\n]+)$/gm;
function safeDirectory(directory, create = false) {
  if (create) { try { fs.mkdirSync(directory, {mode:0o700}); } catch (error) { if (error.code !== 'EEXIST') throw error; } }
  const stat = fs.lstatSync(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('Unsafe project directory');
  return directory;
}
function safeRoot(root) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.parse(root).root === path.resolve(root)) throw new Error('Workspace unavailable');
  // The configured root itself is trusted and can use a platform alias such
  // as macOS /var -> /private/var. No links below that canonical root are used.
  return safeDirectory(fs.realpathSync(path.resolve(root)));
}
function statePath(root, session, create = false) {
  return path.join(safeDirectory(path.join(safeRoot(root), STATE_DIRECTORY), create), `${session}.json`);
}
function readBinding(root, session) {
  let file;
  try { file = statePath(root,session); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  let descriptor;
  try {
    const stat = fs.lstatSync(file);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size > MAX_STATE_BYTES) throw new Error('Unsafe project registry');
    descriptor = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
    const opened = fs.fstatSync(descriptor);
    if (stat.ino !== opened.ino || stat.dev !== opened.dev) throw new Error('Changed project registry');
    const bytes = Buffer.alloc(MAX_STATE_BYTES + 1);
    const length = fs.readSync(descriptor, bytes, 0, bytes.length, 0);
    if (length > MAX_STATE_BYTES) throw new Error('Project registry too large');
    const data = JSON.parse(bytes.subarray(0,length).toString('utf8'));
    if (data.schemaVersion === 1 && data.disabled === true && Object.keys(data).length === 2) return null;
    if (data.schemaVersion !== 1 || !validBinding(data)) throw new Error('Invalid project binding');
    return {source:data.source,directory:data.directory};
  } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  finally { if (descriptor !== undefined) fs.closeSync(descriptor); }
}
function validBinding(value) {
  const segments = parts(value?.directory);
  return segments?.length === 2 && segments[0] === 'Playground' && !GENERIC.test(segments[1])
    && parts(value?.source)?.length === 1 && !GENERIC.test(value.source);
}
function saveBinding(root, session, binding) {
  readBinding(root,session);
  const file = statePath(root,session,true);
  const records = fs.readdirSync(path.dirname(file));
  if (!records.includes(path.basename(file)) && records.length >= LIMIT) throw new Error('Project registry capacity reached');
  const temporary = path.join(path.dirname(file), `.sessions-${randomBytes(12).toString('hex')}.tmp`);
  try {
    // Separate session records avoid lost updates between concurrent workers.
    fs.writeFileSync(temporary, JSON.stringify({schemaVersion:1,...binding}), {flag:'wx',mode:0o600});
    // Refresh the no-link check before the atomic replacement. Only this
    // bounded routing metadata is replaced; project files are never moved.
    statePath(root,session);
    try { const info = fs.lstatSync(file); if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) throw new Error('Unsafe project registry'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    fs.renameSync(temporary, file);
  } finally { try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
}
function validateProject(root, binding) {
  const base = safeDirectory(path.join(safeRoot(root),'Playground'));
  safeDirectory(path.join(base,binding.directory.split('/')[1]));
}
function reserveProject(root, source) {
  const base = safeDirectory(path.join(safeRoot(root),'Playground'),true);
  for (let i = 1; i <= 1000; i++) {
    const name = i === 1 ? source : `${source.slice(0,58)}-${i}`;
    try { fs.mkdirSync(path.join(base,name),{mode:0o700}); return {source,directory:`Playground/${name}`}; }
    catch (error) { if (error.code !== 'EEXIST') throw error; }
  }
  throw new Error('Choose a different project name');
}
function selectTool(tool, params) {
  if (tool !== 'tool_call') return {tool, args:params, wrap:args=>args};
  const id = params?.id;
  if (typeof id !== 'string' || !/^(?:(?:openclaw:core:)?(?:read|write|edit|apply_patch|exec)|(?:openclaw:pixel-ods:)?pixel_ods_workspace_preview)$/.test(id)) return null;
  return {tool:id.split(':').at(-1),args:params.args,wrap:args=>({...params,args})};
}

// Only simple inspection commands may use an inferred parent cwd or inspect
// a failed routing state. Do not accept shell expressions, executable search
// options (such as rg --pre), or arbitrary programs that can mutate files.
function simpleInspection(selected) {
  if (selected.tool !== 'exec' || typeof selected.args.command !== 'string') return false;
  const command = selected.args.command.trim();
  return /^[A-Za-z0-9_./:\\+ =-]+$/.test(command)
    && /^(?:pwd|ls|dir|Get-ChildItem|Get-Location)(?:\s|$)/i.test(command);
}

// Routing is off for a preserved run. When its first write was a Playground
// misspelling of an existing project the model had read, later misspellings
// of that one project keep its canonical spelling, so a case-sensitive
// workspace never gains a second lowercase copy. Nothing else is routed.
function routeSpellingAlias(selected, alias, root) {
  const args = selected.args;
  const canonical = value => {
    const spelled = playgroundSpelling(value,root,2);
    return spelled && (spelled === alias || spelled.startsWith(`${alias}/`)) ? spelled : null;
  };
  if (selected.tool === 'apply_patch') {
    if (typeof args.input !== 'string') return undefined;
    const input = args.input.replace(PATCH_HEADER,(line,prefix,value)=>{ const mapped = canonical(value); return mapped ? prefix+mapped : line; });
    return input === args.input ? undefined : {params:selected.wrap({...args,input})};
  }
  if (selected.tool === 'exec') {
    const mapped = canonical(args.workdir);
    if (mapped) return {params:selected.wrap({...args,workdir:`/workspace/${mapped}`})};
    const operand = workspaceCwd(args.workdir,root) ? projectOperand(typeof args.command === 'string' ? args.command : '',alias,root) : null;
    return operand?.block ? {block:true,blockReason:operand.block} : undefined;
  }
  const key = selected.tool === 'pixel_ods_workspace_preview' ? 'relativeDirectory' : 'path';
  const mapped = canonical(args[key]);
  return mapped ? {params:selected.wrap({...args,[key]:mapped})} : undefined;
}

// Whether routePlaygroundTool, given this run state, would reserve and bind a
// new project for this call: a write before the first project file whose path
// already has the form the correctable refusals ask for, Playground/<name>/...
// with a non-generic <name> (optionally under /workspace/ or the configured
// root), and names no file read in this run (which the router preserves
// instead). It inspects nothing on disk and changes nothing. The run budget
// uses it to recognize the remedy those refusals name.
export function bindsNewProject({state,tool,params,root,existingPaths=[]}) {
  const selected = selectTool(tool,params);
  if (selected?.tool !== 'write' || !selected.args || typeof root !== 'string' || !path.isAbsolute(root)
    || !state?.initialized || !state.fresh || state.binding || state.preserved || state.failed) return false;
  const value = selected.args.path;
  const target = relative(value,root);
  const segments = parts(target);
  return Boolean(segments) && segments.length >= 3 && segments[0] === 'Playground' && !GENERIC.test(segments[1])
    && !existingPaths.includes(value) && !existingPaths.includes(target);
}

// State is per run. Persistent records contain only hashed session identities
// and safe relative paths, never prompts, credentials, or creative bytes.
export function routePlaygroundTool({state,tool,params,root,session,intent,existingPaths=[],preserveExisting=false,continueProject=false}) {
  const selected = selectTool(tool,params);
  if (!selected || !['read','write','edit','apply_patch','exec','pixel_ods_workspace_preview'].includes(selected.tool)
    || !selected.args || typeof root !== 'string' || !path.isAbsolute(root)
    || typeof session !== 'string' || !session || session.length > 2048) return undefined;
  try {
    if (state.failed) throw new Error('Project routing requires recovery');
    const identity = createHash('sha256').update(session).digest('hex');
    const preserve = () => {
      if (!state.preserved && readBinding(root,identity)) saveBinding(root,identity,{disabled:true});
      state.preserved = true;
      state.fresh = false;
      state.binding = null;
    };
    if (preserveExisting || ownerNamesPath(intent)) {
      if (preserveExisting || ['write','edit','apply_patch','pixel_ods_workspace_preview'].includes(selected.tool)) preserve();
      return undefined;
    }
    if (state.preserved) return state.spellingAlias ? routeSpellingAlias(selected,state.spellingAlias,root) : undefined;
    if (!state.initialized) {
      state.fresh = !continueProject && requestsNewPlaygroundProject(intent);
      state.binding = state.fresh ? null : readBinding(root,identity);
      state.initialized = true;
    }
    const args = selected.args;
    const key = selected.tool === 'exec' ? 'workdir' : selected.tool === 'pixel_ods_workspace_preview' ? 'relativeDirectory' : 'path';
    let target = relative(args[key],root);
    const minimum = key === 'path' ? 3 : 2;
    let spelled;
    if (!state.binding && state.fresh && ['exec','apply_patch'].includes(selected.tool)) {
      const command = typeof args.command === 'string' ? args.command.trim() : '';
      const inspection = selected.tool === 'exec' && !/[;&|><`\r\n]|\$\(/.test(command)
        && /^(?:(?:pwd|ls|dir|rg|Get-ChildItem|Get-Location)(?:\s|$)|(?:node|python3?|npm|git)\s+(?:--version|-v)$|git\s+status(?:\s|$))/i.test(command);
      if (!inspection) {
        // A folder suggested earlier in this run wins over one the command names.
        let next = earlierFolder(state,root) ? state.suggestedPath ?? `Playground/${state.suggestedFolder}/<file name>` : null;
        const named = !next && selected.tool === 'exec' ? commandProjectFolder(command,root) : null;
        if (named) {
          next = `Playground/${named}/<file name>`;
          state.suggestedFolder = named;
          state.suggestedPath = undefined;
        }
        return {block:true,correctable:'playground-first-write',blockReason:`${NOT_RUN}${next ? ` Call write now with path ${next} and its content.` : ''} Create the first project file with write in a descriptive Playground folder before running commands or patches. ${CORRECTION}`};
      }
    }
    if (!state.binding && state.fresh && selected.tool === 'write') {
      // Refusals stay charged failures; only their text names the next path.
      // Both refusals before the first project file are correctable: they run
      // nothing and name their remedy, a write that binds the project
      // (bindsNewProject), which the run budget admits once after its fuse.
      const refuse = () => {
        const suggestion = suggestedProjectPath(args[key],root,state);
        if (suggestion) {
          state.suggestedFolder = suggestion.split('/')[1];
          state.suggestedPath = suggestion;
        }
        return {block:true,correctable:'playground-project-path',blockReason:suggestion
          ? `Not written: project files go in a descriptive Playground folder. Call write again now with path ${suggestion} and the same content, then use that folder for every project file and as exec workdir. ${CORRECTION}`
          : CORRECTION};
      };
      // A path the model already read with its own spelling keeps the
      // unspelled handling below: it names that file exactly as it was read.
      const readAsSpelled = existingPaths.includes(args[key]) || (target !== null && existingPaths.includes(target));
      spelled = readAsSpelled ? null : playgroundSpelling(args[key],root,minimum);
      if (spelled) target = spelled;
      if (!target) return refuse();
      // Only a workspace-relative read is evidence for a workspace file: a
      // rooted /playground/... read may have been a host path.
      if (existingPaths.includes(target)
        || (spelled && existingPaths.some(value => {
          const read = relative(value,root);
          return read !== null && (read === spelled || playgroundSpelling(read,root,minimum) === spelled);
        }))) {
        preserve();
        if (!spelled) return undefined;
        // The model read this existing project with another spelling. Keep
        // the project's own spelling for this write and the rest of the run.
        state.spellingAlias = spelled.split('/').slice(0,2).join('/');
        return {params:selected.wrap({...args,[key]:spelled})};
      }
      const segments = parts(target);
      const candidate = segments[0] === 'Playground' ? segments[1] : segments[0];
      if (segments.length < (segments[0] === 'Playground' ? 3 : 2) || !candidate || GENERIC.test(candidate)) return refuse();
      // Legacy projects that the owner is working in stay exactly where they
      // are. Never silently relocate an existing path or overwrite it as new.
      if (segments[0] !== 'Playground') {
        try { fs.lstatSync(path.join(safeRoot(root),segments[0])); preserve(); return undefined; }
        catch (error) { if (error.code !== 'ENOENT') throw error; }
      }
      const binding = reserveProject(root,candidate);
      saveBinding(root,identity,binding);
      state.binding = binding;
    }
    if (!state.binding) return undefined;
    validateProject(root,state.binding);
    const {source,directory} = state.binding;
    const projectPath = (value, mutation = false) => {
      if (value === directory || value?.startsWith(`${directory}/`)) return value;
      if (value === `Playground/${source}` || value?.startsWith(`Playground/${source}/`)) return directory + value.slice(`Playground/${source}`.length);
      if (value === source || value?.startsWith(`${source}/`)) return directory + value.slice(source.length);
      if (value && !value.startsWith('Playground/') && (mutation || !value.includes('/') || LOCAL_FOLDERS.test(value.split('/')[0]))) return `${directory}/${value}`;
      return undefined;
    };
    if (selected.tool === 'apply_patch') {
      if (typeof args.input !== 'string') return {block:true,blockReason:`Use apply_patch input with file paths inside ${directory}.`};
      let count = 0, invalid = false;
      const input = args.input.replace(PATCH_HEADER,(_line,prefix,value)=>{
        count++;
        const spelledValue = playgroundSpelling(value,root,3);
        const mapped = projectPath(spelledValue && projectPath(spelledValue) ? spelledValue : relative(value,root),true);
        if (!mapped) {invalid=true;return _line;}
        return prefix+mapped;
      });
      if (!count || invalid) return {block:true,blockReason:`Use exact safe file paths inside ${directory} for this project patch.`};
      return input === args.input ? undefined : {params:selected.wrap({...args,input})};
    }
    // A Playground misspelling of this bound project routes like its canonical
    // path. Any other spelled name keeps the ordinary routing below.
    spelled ??= playgroundSpelling(args[key],root,minimum);
    if (spelled && projectPath(spelled)) target = spelled;
    let mapped = projectPath(target,['write','edit'].includes(selected.tool));
    // A path operand misspelling this project from the workspace root cannot
    // resolve there or from an inferred cwd; name the folder instead. One
    // that resolves as written (the owner's separate folder, or the same
    // folder on a case-insensitive workspace) runs from the workspace root.
    const command = selected.tool === 'exec' && typeof args.command === 'string' ? args.command : '';
    const operand = selected.tool === 'exec' && workspaceCwd(args.workdir,root) ? projectOperand(command,directory,root) : null;
    if (operand?.block) return {block:true,blockReason:operand.block};
    // Keep unrelated reads/edits and explicitly located execs untouched. For
    // an unspecified exec cwd, use the project only when the command does not
    // name its workspace-root prefix or first cd into the project from the
    // workspace root; never rewrite shell program text.
    if (selected.tool === 'exec' && (args.workdir === undefined || args.workdir === '.' || args.workdir === '/workspace')) {
      if (operand || command.includes(`${directory}/`) || command.includes('/workspace/') || entersProject(command,directory)) mapped = null;
      else if (command.includes(`${source}/`)) {
        if (directory !== `Playground/${source}`) return {block:true,blockReason:`This project is in ${directory}. Set exec workdir to ${directory} and use filenames relative to that directory; the old ${source}/ prefix names a different project.`};
        if (!simpleInspection(selected)) return {block:true,blockReason:`This project is in ${directory}. Set exec workdir to /workspace/${directory} and use filenames relative to that directory. An automatic parent directory would make this command ambiguous and could move or modify the project folder itself.`};
        mapped = 'Playground';
      } else mapped = directory;
    }
    // Core sandbox exec resolves a relative cwd against the gateway process,
    // not the mounted owner workspace. This hook runs after generic argument
    // normalization, so preserve the sandbox alias when injecting a project.
    // Native execution translates the same alias to its configured workspace.
    if (selected.tool === 'exec' && mapped) mapped = `/workspace/${mapped}`;
    if (!mapped || mapped === args[key]) return undefined;
    if (selected.tool === 'read' && target?.includes('/') && !target.startsWith(`${source}/`) && !target.startsWith('Playground/')) {
      try { fs.lstatSync(path.join(root,...target.split('/'))); return undefined; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return {params:selected.wrap({...args,[key]:mapped})};
  } catch {
    state.failed = true;
    if (simpleInspection(selected)) {
      try {
        safeRoot(root);
        const workdir = selected.args.workdir;
        return {params:selected.wrap({...selected.args,workdir:workdir === undefined || workdir === '.' ? '/workspace' : workdir})};
      } catch { /* An unavailable workspace cannot support safe inspection. */ }
    }
    return {block:true,blockReason:'The project folder could not be safely prepared or restored. Preserve existing files. Inspect the workspace with pwd or ls and check the project metadata before retrying; do not bypass this by writing elsewhere.'};
  }
}
