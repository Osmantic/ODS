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
const CORRECTION = 'For a new project, choose a short descriptive folder such as Playground/snake-game/index.html or Playground/weather-tool/main.py. Use that same folder for every project file and for preview publication. Do not use a bare filename or a generic src/public/project folder as the project name.';

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
  const text = plainIntent(intent);
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
function ownerNamesPath(intent) {
  const text = plainIntent(intent).replace(/https?:\/\/\S+/g, ' ');
  return /(?:^|[\s`"'(])(?:\/?[A-Za-z0-9_.-]+[\/\\][A-Za-z0-9_./\\-]+|[A-Za-z]:[\/\\]\S+)(?=$|[\s`"'),;.!?])/i.test(text)
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
    if (state.preserved) return undefined;
    if (!state.initialized) {
      state.fresh = !continueProject && requestsNewPlaygroundProject(intent);
      state.binding = state.fresh ? null : readBinding(root,identity);
      state.initialized = true;
    }
    const args = selected.args;
    const key = selected.tool === 'exec' ? 'workdir' : selected.tool === 'pixel_ods_workspace_preview' ? 'relativeDirectory' : 'path';
    const target = relative(args[key],root);
    if (!state.binding && state.fresh && ['exec','apply_patch'].includes(selected.tool)) {
      const command = typeof args.command === 'string' ? args.command.trim() : '';
      const inspection = selected.tool === 'exec' && !/[;&|><`\r\n]|\$\(/.test(command)
        && /^(?:(?:pwd|ls|dir|rg|Get-ChildItem|Get-Location)(?:\s|$)|(?:node|python3?|npm|git)\s+(?:--version|-v)$|git\s+status(?:\s|$))/i.test(command);
      if (!inspection) return {block:true,blockReason:`Create the first project file with write in a descriptive Playground folder before running commands or patches. ${CORRECTION}`};
    }
    if (!state.binding && state.fresh && selected.tool === 'write') {
      if (!target) return {block:true,blockReason:CORRECTION};
      if (existingPaths.includes(target)) { preserve(); return undefined; }
      const segments = parts(target);
      const candidate = segments[0] === 'Playground' ? segments[1] : segments[0];
      if (segments.length < (segments[0] === 'Playground' ? 3 : 2) || !candidate || GENERIC.test(candidate)) return {block:true,blockReason:CORRECTION};
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
      const input = args.input.replace(/^(\*\*\* (?:(?:Add|Update|Delete) File|Move to): )([^\r\n]+)$/gm,(_line,prefix,value)=>{
        count++;
        const mapped = projectPath(relative(value,root),true);
        if (!mapped) {invalid=true;return _line;}
        return prefix+mapped;
      });
      if (!count || invalid) return {block:true,blockReason:`Use exact safe file paths inside ${directory} for this project patch.`};
      return input === args.input ? undefined : {params:selected.wrap({...args,input})};
    }
    let mapped = projectPath(target,['write','edit'].includes(selected.tool));
    // Keep unrelated reads/edits and explicitly located execs untouched. For
    // an unspecified exec cwd, use the project only when the command does not
    // name its workspace-root prefix; never rewrite shell program text.
    if (selected.tool === 'exec' && (args.workdir === undefined || args.workdir === '.' || args.workdir === '/workspace')) {
      const command = typeof args.command === 'string' ? args.command : '';
      if (command.includes(`${directory}/`) || command.includes('/workspace/')) mapped = null;
      else if (command.includes(`${source}/`)) {
        if (directory !== `Playground/${source}`) return {block:true,blockReason:`This project is in ${directory}. Set exec workdir to ${directory} and use filenames relative to that directory; the old ${source}/ prefix names a different project.`};
        mapped = 'Playground';
      } else mapped = directory;
    }
    if (!mapped || mapped === args[key]) return undefined;
    if (selected.tool === 'read' && target?.includes('/') && !target.startsWith(`${source}/`) && !target.startsWith('Playground/')) {
      try { fs.lstatSync(path.join(root,...target.split('/'))); return undefined; } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return {params:selected.wrap({...args,[key]:mapped})};
  } catch {
    state.failed = true;
    return {block:true,blockReason:'The project folder could not be safely prepared or restored. Preserve existing files. Check the workspace directory and project metadata before retrying; do not bypass this by writing elsewhere.'};
  }
}
