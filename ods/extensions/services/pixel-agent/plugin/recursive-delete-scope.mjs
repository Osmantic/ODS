// Scope of an unauthorized recursive forced deletion (see tool-loop-guard).
//
// The guard refuses every `rm -rf` the owner did not explicitly ask for. It
// was added after a qualification model deleted the owner's whole project
// tree, and a refusal became terminal for the run because blocked models then
// retried the same effect through find -delete, shutil.rmtree or sh -c.
//
// This module only decides whether the FIRST refusal in a run may stay
// recoverable. It never allows a deletion. A refusal is recoverable only when
// every recursive rm in the command provably names a path strictly inside one
// project directory below the workspace root, for example `rm -rf public`
// from /workspace/site, and no other command in it deletes. The workspace
// root, a whole project directory, a top-level dot directory, any .git
// directory, anything outside the workspace, `..`, `~`, variables,
// substitutions, quoting beyond plain words, globs that are not inside a
// project subdirectory, symlinked components, a link, move or copy before the
// rm, any other deleting command (an sh -c, bash -c or eval string naming rm,
// find -delete, git clean, ...) and any other shell syntax this literal
// parser does not model stay terminal.
import fs from "node:fs";
import path from "node:path";

const RECURSIVE_RM_INVOCATIONS = /(?:^|[;&|]\s*)rm\s+((?:(?:--[A-Za-z-]+|-[A-Za-z]+)\s+)+)/gim;

// Offsets of each `rm` word the guard treats as a recursive forced deletion.
export function recursiveForcedDeleteOffsets(command) {
  if (typeof command !== "string" || !command.trim()) return [];
  const offsets = [];
  for (const match of command.matchAll(RECURSIVE_RM_INVOCATIONS)) {
    const options = match[1];
    const recursive = /--recursive\b/i.test(options) || /(?:^|\s)-[A-Za-z]*[rR][A-Za-z]*(?:\s|$)/.test(options);
    const forced = /--force\b/i.test(options) || /(?:^|\s)-[A-Za-z]*f[A-Za-z]*(?:\s|$)/.test(options);
    if (recursive && forced) offsets.push(match.index + match[0].indexOf("rm"));
  }
  return offsets;
}

// After a recoverable refusal the model was told not to delete recursively.
// These are the common substitutes; any of them ends the turn instead. It is
// a tripwire for retries, not a sandbox (the first refusal never was either).
const RECURSIVE_DELETE_ALTERNATE = new RegExp([
  String.raw`\brm\s+(?:-{1,2}[A-Za-z-]+\s+)*(?:-[A-Za-z]*[rR]|--recursive\b)`,
  String.raw`['"]rm['"]\s*,\s*['"]-{1,2}[A-Za-z]*[rR]`,
  String.raw`\s-delete\b`,
  String.raw`-exec(?:dir)?\s+rm\b`,
  String.raw`\brmtree\b`,
  String.raw`\bremove_tree\b`,
  String.raw`\brimraf\b`,
  // git clean -n / --dry-run only lists what it would remove. The lazy and
  // tempered scans here and below keep repeated tokens linear.
  String.raw`\bgit\s+clean\b(?![^\n;&|]*?\s(?:-[A-Za-z]*n[A-Za-z]*|--dry-run)(?=\s|$))`,
  String.raw`\brsync\b(?:(?!rsync\b)[^\n;&|])*?\s--delete\b`,
  // The options object of this call, across lines, and not a later call's.
  String.raw`\b(?:rm|rmdir)(?:Sync)?\s*\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*?\brecursive\s*:\s*true\b`,
  String.raw`\bRemove-Item\b[^\n]*\s-Recurse\b`,
].join("|"), "i");

export function recursiveDeleteAlternate(params) {
  return typeof params?.command === "string" && RECURSIVE_DELETE_ALTERNATE.test(params.command);
}

const WORD_CHARACTER = /[A-Za-z0-9._/+@%=:,*?[\]-]/;
const QUOTED_CONTENT = /^[A-Za-z0-9._/+@%=:, -]*$/;
const GLOB = /[*?[\]]/;

// Literal shell only: plain or simply quoted words, && || ; | & and newline
// separators, and file-descriptor redirections. Parsing stops at the first
// simple command that uses anything else; `end` is where that command starts.
function literalShellCommands(text) {
  const commands = [];
  let words = [];
  let word;
  let redirect = false;
  let commandStart = 0;
  const stopped = () => ({ commands, end: commandStart });
  const finishWord = () => {
    if (!word) return;
    if (redirect) redirect = false;
    else words.push(word);
    word = undefined;
  };
  for (let index = 0; index < text.length;) {
    const character = text[index];
    if (character === " " || character === "\t") {
      finishWord();
      index += 1;
      continue;
    }
    if (text.startsWith("&>", index) || text.startsWith(";;", index)) return stopped();
    const operator = text.startsWith("&&", index) || text.startsWith("||", index)
      ? text.slice(index, index + 2)
      : character === ";" || character === "\n" ? ";"
        : character === "|" || character === "&" ? character : undefined;
    if (operator) {
      finishWord();
      if (redirect) return stopped();
      // A blank line or trailing ; separates nothing; any other operator
      // without a preceding command is not literal shell this parser models.
      if (words.length) {
        commands.push({ words, operator });
        words = [];
      } else if (operator !== ";") {
        return stopped();
      }
      index += operator.length;
      commandStart = index;
      continue;
    }
    if (character === "<" || character === ">" || (!word && /[0-9]/.test(character))) {
      const redirection = /^[0-9]*(?:>>|>|<)(&(?:[0-9]+|-))?/.exec(text.slice(index, index + 24));
      if (redirection) {
        finishWord();
        if (redirect) return stopped();
        redirect = !redirection[1];
        index += redirection[0].length;
        continue;
      }
    }
    if (character === "'" || character === '"') {
      const close = text.indexOf(character, index + 1);
      if (close < 0 || !QUOTED_CONTENT.test(text.slice(index + 1, close))) return stopped();
      word ??= { value: "", start: index };
      word.value += text.slice(index + 1, close);
      index = close + 1;
      continue;
    }
    if (!WORD_CHARACTER.test(character)) return stopped();
    word ??= { value: "", start: index };
    word.value += character;
    index += 1;
  }
  finishWord();
  if (redirect) return stopped();
  if (words.length) commands.push({ words, operator: undefined });
  return { commands, end: text.length };
}

// Keywords and builtins that can change the shell's directory, run hidden
// code, or restructure the command; assignments can change cd (CDPATH).
const UNMODELED_COMMAND =
  /^(?:!|\{|\}|\[\[|\]\]|if|then|else|elif|fi|for|in|do|done|while|until|case|esac|select|function|time|coproc|pushd|popd|source|\.|eval|exec|builtin|command|alias|unalias|set|shopt|export|declare|typeset|local|readonly|unset|trap|enable|hash)$|^[A-Za-z_][A-Za-z0-9_]*=/;
const RM_WORD = /(?:^|\/)rm$/;
// A link, move or copy that runs before the rm can put anything, including
// a symlink to /, at a path that is a plain project directory right now.
const PATH_RESTRUCTURING = /(?:^|[\s/])(?:ln|mv|cp|rsync)(?:\s|$)/;

// A modeled command other than rm that deletes or hands rm to another
// program: sudo or xargs rm, an sh -c, bash -c or eval string naming rm,
// find -delete, git clean, and the other substitutes above.
function otherDeletion(words) {
  return !RM_WORD.test(words[0].value) &&
    (words.some(({ value }) => /\brm\b/.test(value)) ||
      recursiveDeleteAlternate({ command: ` ${words.map(({ value }) => value).join(" ")}` }));
}

function hasParentSegment(value) {
  return value.split("/").includes("..");
}

function rmInvocation(words) {
  const operands = [];
  let options = true;
  let recursive = false;
  for (const { value } of words.slice(1)) {
    if (options && value === "--") {
      options = false;
    } else if (options && value.length > 1 && value.startsWith("-")) {
      if (/^-[A-Za-z]*[rR]/.test(value) || (value.length >= 3 && "--recursive".startsWith(value))) recursive = true;
    } else {
      operands.push(value);
    }
  }
  return { operands, recursive };
}

// Every directory the rm at `before` may run in. A cd moves it only when that
// cd must succeed for the rm to run (an unbroken && chain with no ||);
// otherwise both the old and new directories stay possible.
function possibleDirectories(commands, before, initial) {
  let directories = [initial];
  const conditional = commands.slice(0, before).some(({ operator }) => operator === "||");
  for (let index = 0; index < before; index += 1) {
    const { words } = commands[index];
    if (words[0].value !== "cd") continue;
    const target = words[1]?.value;
    if (words.length !== 2 || !target || target.startsWith("-") || GLOB.test(target) || hasParentSegment(target)) return undefined;
    const moved = directories.map((directory) => path.posix.resolve(directory, target));
    const gated = !conditional && commands.slice(index, before).every(({ operator }) => operator === "&&");
    directories = gated ? moved : [...new Set([...directories, ...moved])];
  }
  return directories;
}

function usableWorkspaceRoot(root, stat) {
  if (typeof root !== "string") return undefined;
  const configured = root.length > 1 ? root.replace(/\/+$/, "") : root;
  if (!configured.startsWith("/") || configured === "/" || configured.length > 4096 ||
      path.posix.normalize(configured) !== configured || /[\x00-\x1f\\]/.test(configured)) return undefined;
  try {
    return stat(configured).isDirectory() ? configured : undefined;
  } catch {
    return undefined;
  }
}

// True only for a path strictly inside a project directory of the workspace
// (a top-level folder, or Playground/<name> for Playground projects) whose
// existing components are real directories, never symlinks. Names compare
// case-insensitively because a macOS workspace may be case-insensitive.
function projectSubtreeTarget(target, root, lstat) {
  let relative;
  for (const base of ["/workspace", root]) {
    if (target.startsWith(`${base}/`)) {
      relative = target.slice(base.length + 1);
      break;
    }
  }
  if (!relative) return false;
  const parts = relative.split("/");
  const projectDepth = parts[0].toLowerCase() === "playground" ? 2 : 1;
  if (parts.length <= projectDepth ||
      parts.some((part) => !part || part === "." || part === ".." || part.toLowerCase() === ".git") ||
      parts.slice(0, projectDepth).some((part) => part.startsWith(".") || GLOB.test(part)) ||
      parts.slice(0, -1).some((part) => GLOB.test(part))) return false;
  const leaf = parts.at(-1);
  const leafGlob = GLOB.test(leaf);
  // A glob may only select entries below a project subdirectory.
  if (leafGlob && (parts.length <= projectDepth + 1 || leaf.startsWith("."))) return false;
  let cursor = root;
  for (const part of leafGlob ? parts.slice(0, -1) : parts) {
    cursor = path.join(cursor, part);
    let entry;
    try {
      entry = lstat(cursor);
    } catch (error) {
      return error?.code === "ENOENT" || error?.code === "ENOTDIR";
    }
    if (entry.isSymbolicLink()) return false;
    if (!entry.isDirectory()) return true;
  }
  return true;
}

export function recursiveDeleteStaysInProject(params, workspaceRoot, { lstat = fs.lstatSync, stat = fs.statSync } = {}) {
  const command = params?.command;
  const offsets = recursiveForcedDeleteOffsets(command);
  if (!offsets.length || command.length > 16384) return false;
  const root = usableWorkspaceRoot(workspaceRoot, stat);
  if (!root) return false;
  const workdir = params.workdir === undefined || params.workdir === "." ? "/workspace" : params.workdir;
  if (typeof workdir !== "string" || !workdir.startsWith("/") || /[\x00-\x1f\\]/.test(workdir) ||
      hasParentSegment(workdir)) return false;
  const initial = path.posix.normalize(workdir);
  const { commands, end } = literalShellCommands(command);
  // Everything the parser did not model must contain no other deletion, and
  // no modeled command, before or after the rm, may delete some other way.
  const unparsed = command.slice(end);
  if (offsets.some((offset) => offset >= end) || /\brm\b/.test(unparsed) ||
      recursiveDeleteAlternate({ command: unparsed }) ||
      commands.some(({ words }) => words.slice(1).some(({ value }) => RM_WORD.test(value))) ||
      commands.some(({ words }) => otherDeletion(words))) return false;
  const rmStarts = new Set();
  const lastRm = commands.findLastIndex(({ words }) => RM_WORD.test(words[0].value));
  for (let index = 0; index <= lastRm; index += 1) {
    const { words } = commands[index];
    const name = words[0].value;
    if (name === "cd") continue;
    if (UNMODELED_COMMAND.test(name)) return false;
    if (!RM_WORD.test(name)) {
      if (words.some(({ value }) => PATH_RESTRUCTURING.test(value))) return false;
      continue;
    }
    rmStarts.add(words[0].start);
    const { operands, recursive } = rmInvocation(words);
    if (!recursive) continue;
    const directories = possibleDirectories(commands, index, initial);
    if (!directories || !operands.length) return false;
    for (const operand of operands) {
      if (!operand || hasParentSegment(operand)) return false;
      for (const directory of directories) {
        if (!projectSubtreeTarget(path.posix.resolve(directory, operand), root, lstat)) return false;
      }
    }
  }
  return offsets.every((offset) => rmStarts.has(offset));
}
